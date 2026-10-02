"""Local web app: recording controls, processing and history in the browser.

Runs on 127.0.0.1 only. Every API call must carry a per-run random token and a matching
Host header, so other web pages open in the same browser cannot drive it.
"""

from __future__ import annotations

import contextlib
import errno
import importlib.util
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import unicodedata
import urllib.parse
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from . import __version__, audio, jobs, recording, roles, systemaudio, templates, transcript
from .config import Config
from .errors import SessionError, VechoError
from .session import SUMMARY_MD, TRANSCRIPT_JSON, Session, SessionStore
from .summarize import OllamaClient
from .transcribe import engine_label

MAX_UPLOAD_BYTES = 2 * 1024**3
MIX_FILE = "mix.wav"
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm", ".mp4"}


def _is_folder_name(name: str) -> bool:
    """One path component: any folder in the sessions directory (even "회의 copy") but no path."""
    return bool(name) and name not in {".", ".."} and not set(name) & {"/", "\\", "\0"}


class NotFound(VechoError):
    pass


class Conflict(VechoError):
    pass


def summary_body(markdown: str) -> str:
    """Strip the title and metadata lines that ``summary.md`` starts with."""
    lines = markdown.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
        if lines and lines[0].startswith(">"):
            lines = lines[1:]
    return "\n".join(lines).strip()


def tldr_of(markdown: str) -> str:
    """A one-line preview: the first line that says something, as plain text."""
    for line in summary_body(markdown).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", "```")) or set(stripped) <= set("-*_ "):
            continue
        if re.fullmatch(r"(?:[-*+]|\d+[.)])?\s*\*\*[^*]+\*\*\s*:?", stripped):
            continue  # a bare label such as "- **지원 현황**"
        text = re.sub(r"^(?:[-*+>]|\d+[.)])\s*", "", stripped)
        text = re.sub(r"\*\*|__|`", "", text).replace("[ ]", "").replace("[x]", "").strip()
        if len(text) >= 4:
            return text[:200]
    return ""


class App:
    """Everything the web UI can do, independent of HTTP."""

    def __init__(self, config: Config, processor: jobs.Processor | None = None) -> None:
        self.config = config
        self.store = SessionStore(config.sessions_dir)
        self.templates = templates.TemplateStore(config.templates_dir)
        self.processor = processor or jobs.Processor(config)
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()
        self._live: recording.LiveRecording | None = None
        self._issues: dict[str, list[dict[str, str]]] = {}
        self._stopping: set[str] = set()  # recordings being finalized
        self._uploading: set[str] = set()
        self._settled = threading.Condition(self._lock)
        self._starting = False
        self._starting_id: str | None = None  # the session a start is creating
        self._closing = False
        self._mix_lock = threading.Lock()

    # -- recording --------------------------------------------------------------------------

    def start_recording(
        self, title: str = "", mic_only: bool = False, template: str | None = None
    ) -> dict[str, Any]:
        template = self._template_name(template)
        with self._lock:
            if self._closing:
                raise Conflict("the app is quitting", "session_busy")
            if self._live is not None or self._starting:
                raise Conflict("a recording is already running", "already_recording")
            self._starting = True
        try:
            # Outside the lock: the first start may build the capture helper, which takes a
            # while, and nothing else (status polls, quitting) should wait for that.
            live = recording.LiveRecording(self.config, title=title.strip(), mic_only=mic_only)
            with self._lock:
                self._starting_id = live.session.id  # shown as recording: not deletable
            live.session.meta.template = template  # saved by start()
            live.start()
            with self._lock:
                self._live = live
                self._issues[live.session.id] = []
        finally:
            with self._settled:
                self._starting = False
                self._starting_id = None
                self._settled.notify_all()  # quitting waits for a start to finish
        return self.state()

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            live, self._live = self._live, None
            if live is not None:
                self._stopping.add(live.session.id)  # not deletable while being finalized
        if live is None:
            raise Conflict("nothing is being recorded", "not_recording")
        # (quitting waits for _stopping to empty; see shutdown())
        try:
            session, issues, processing = self._finish(live)
            if processing:
                self.processor.submit(session, "all")
        finally:
            with self._settled:
                self._stopping.discard(live.session.id)
                self._settled.notify_all()
        return {
            "session_id": session.id,
            "issues": issues,
            "processing": processing,
        }

    def _finish(self, live: recording.LiveRecording) -> tuple[Session, list[dict[str, str]], bool]:
        """Stop a recording and save what is known about it; returns whether to process it."""
        result = live.stop()
        session = result.session
        issues = self._issues.setdefault(session.id, [])
        issues.extend(result.issues)
        processing = not result.too_short
        if not processing:
            issues.append({"code": "too_short", "role": "", "hint": ""})
        session.meta.issues = issues
        session.save()
        return session, issues, processing

    def shutdown(self, wait: float = 20.0) -> None:
        """Keep whatever is being recorded when the app quits.

        A start or stop that is still in progress (either can take several seconds) is waited
        for, so its session is saved rather than cut off halfway or left recording.
        """
        with self._settled:
            self._closing = True
            self._settled.wait_for(lambda: not self._starting, timeout=wait)
            live, self._live = self._live, None
        if live is not None:
            try:
                self._finish(live)  # processed from the app's list on the next launch
            except Exception:
                live.abort()
        with self._settled:
            self._settled.wait_for(lambda: not self._stopping, timeout=wait)
        self.processor.shutdown()

    def state(self) -> dict[str, Any]:
        live = self._live
        current = None
        if live is not None:
            current = {
                "session_id": live.session.id,
                "title": live.session.meta.title,
                "elapsed": live.elapsed,
                "levels": live.levels(),
                "sources": {
                    role: {"label": self.config.label_for(role), "name": source.name}
                    for role, source in live.sources
                },
                "issues": self._issues.get(live.session.id, []),
            }
        return {"version": __version__, "recording": current, "jobs": self.processor.snapshot()}

    # -- sessions ---------------------------------------------------------------------------

    def _session(self, session_id: str) -> Session:
        if (
            not _is_folder_name(session_id)
            or len(session_id.encode("utf-8")) > 255  # longer than any file name can be
        ):
            raise NotFound("no such session", "session_missing")
        directory = self.store.root / session_id
        if not (directory / "session.json").is_file():
            raise NotFound("no such session", "session_missing")
        return Session.load(directory)

    def _status(self, session: Session) -> str:
        live = self._live
        if (
            (live is not None and live.session.id == session.id)
            or session.id in self._stopping
            or session.id == self._starting_id
        ):
            return "recording"
        job = self.processor.state(session.id)
        if (job is not None and job.active) or session.id in self._uploading:
            return "processing"
        if job is not None and job.stage == jobs.ERROR:
            return "error"
        if session.has_summary:
            return "summarized"
        if session.has_transcript:
            return "transcribed" if jobs.has_speech(session) else "empty"
        return "recorded"

    def _session_text(self, path: Path) -> str:
        """A generated text file, even one damaged or saved in another encoding."""
        try:
            return path.read_text("utf-8", errors="replace") if path.is_file() else ""
        except OSError:
            return ""

    def list_sessions(self) -> list[dict[str, Any]]:
        items = []
        for session in self.store.list():
            summary = session.path_for(SUMMARY_MD)
            items.append(
                {
                    "id": session.id,
                    "title": session.meta.title,
                    "created_at": session.meta.created_at,
                    "duration": session.meta.duration_sec,
                    "status": self._status(session),
                    "tldr": tldr_of(self._session_text(summary)),
                }
            )
        return items

    def session_detail(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        summary_path = session.path_for(SUMMARY_MD)
        segments = []
        issues = list(session.meta.issues)
        try:
            loaded = (
                transcript.load_segments(session.path_for(TRANSCRIPT_JSON))
                if session.has_transcript
                else []
            )
        except VechoError:  # still open the session, so it can be transcribed again or deleted
            loaded = []
            issues.append({"code": "bad_transcript", "role": "", "hint": ""})
        if loaded:
            for segment in transcript.coalesce(loaded):
                segments.append(
                    {
                        "role": segment.role,
                        "label": self.config.label_for(segment.role),
                        "start": segment.start,
                        "end": segment.end,
                        "text": segment.text,
                    }
                )
        job = self.processor.state(session.id)
        meta = session.meta
        return {
            "id": session.id,
            "title": session.meta.title,
            "created_at": meta.created_at,
            "duration": meta.duration_sec,
            "status": self._status(session),
            "language": meta.language,
            "whisper_model": meta.whisper_model,
            "llm_model": meta.llm_model,
            "template": meta.template,
            # which renderer fits the summary on disk (decided by how it was made, not by
            # whether that template still exists)
            "template_summary": bool(
                summary_path.is_file()
                and (meta.summary_template or meta.template)  # older sessions lack the first
                and (meta.summary_template or meta.template) != templates.BUILTIN_NAME
            ),
            "tracks": list(meta.tracks),
            "summary": summary_body(self._session_text(summary_path)),
            "segments": segments,
            "job": job.to_dict() if job else None,
            "issues": issues,
            "labels": {role: self.config.label_for(role) for role in roles.ALL},
        }

    def rename(self, session_id: str, title: str) -> dict[str, Any]:
        session = self._session(session_id)
        title = title.strip()
        if not title:
            raise VechoError("the title cannot be empty", "title_empty")
        session.meta.title = title[:200]
        session.save()
        live = self._live
        if live is not None and live.session.id == session_id:
            live.session.refresh()  # so the recorder shows (and later saves) the new title
        return self.session_detail(session_id)

    def delete(self, session_id: str) -> None:
        session = self._session(session_id)
        with self._lock:  # no job may be queued between the check and the removal
            if self._status(session) in {"recording", "processing"}:
                raise Conflict("this session is still being recorded or processed", "session_busy")
            shutil.rmtree(session.dir)
            self.processor.forget(session_id)
            self._issues.pop(session_id, None)

    def process(self, session_id: str, step: str, template: str | None = None) -> dict[str, Any]:
        session = self._session(session_id)
        name = self._template_name(template) if template is not None else None
        with self._lock:  # no delete between the check and queueing the job
            status = self._status(session)
            if status == "recording":
                raise Conflict("stop the recording first", "session_busy")
            if status == "processing":
                raise Conflict("this session is already being processed", "session_busy")
            if not session.dir.is_dir():
                raise NotFound("no such session", "session_missing")
            if step == "summarize" and not session.has_transcript:
                step = "all"
            self.processor.submit(session, step, name)
        return self.session_detail(session_id)

    # -- templates ----------------------------------------------------------------------------

    def _template_name(self, name: str | None) -> str:
        """A valid template name: the requested one, or the default when not given."""
        if not name:
            return self.templates.default_name()
        template = self.templates.get(name)
        if template.name != name:
            raise NotFound(f"no template named '{name}'", "template_missing")
        return template.name

    def list_templates(self) -> dict[str, Any]:
        return {
            "default": self.templates.default_name(),
            "templates": [
                {
                    "name": t.name,
                    "builtin": t.builtin,
                    "body": t.body,
                    "sections": templates.top_sections(t.body),
                }
                for t in self.templates.list()
            ],
        }

    def save_template(self, name: str, body: str, previous: str | None = None) -> dict[str, Any]:
        """Create (``previous`` None) or update/rename (``previous`` = its current name)."""
        if previous is None:
            wanted = unicodedata.normalize("NFC", " ".join(name.split())).casefold()
            if any(t.name.casefold() == wanted for t in self.templates.list()[1:]):
                raise templates.TemplateError(
                    f"a template named '{name}' already exists", "template_exists"
                )
        saved = self.templates.save(name, body, previous)
        if previous is not None and saved.name != previous:
            self._follow_rename(previous, saved.name)
        return self.list_templates()

    def _follow_rename(self, old: str, new: str) -> None:
        """Sessions that chose a template keep it when it is renamed."""
        for session in self.store.list():
            if session.meta.template == old:
                session.meta.template = new
                with contextlib.suppress(VechoError, OSError):
                    session.save()

    def delete_template(self, name: str) -> dict[str, Any]:
        self.templates.delete(name)
        return self.list_templates()

    def set_default_template(self, name: str) -> dict[str, Any]:
        self.templates.set_default(name)
        return self.list_templates()

    def import_audio(
        self, filename: str, stream: Any, length: int, template: str | None = None
    ) -> dict[str, Any]:
        template = self._template_name(template)
        suffix = Path(filename).suffix.lower()
        if suffix not in AUDIO_EXTENSIONS:
            raise VechoError(f"unsupported file type '{suffix or filename}'", "file_type")
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            raise VechoError("the file is empty or too large", "file_size")
        session = self.store.create(unicodedata.normalize("NFC", Path(filename).stem))
        name = f"{roles.MIXED}{suffix}"
        target = session.path_for(name)
        with self._lock:
            self._uploading.add(session.id)  # shown as busy (not deletable) while uploading
        try:
            with target.open("wb") as out:
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise VechoError("the upload was interrupted", "upload_interrupted")
                    out.write(chunk)
                    remaining -= len(chunk)
            session.meta.tracks = {roles.MIXED: name}
            session.meta.template = template
            session.save()
            self.processor.submit(session, "all")
        except BaseException:
            shutil.rmtree(session.dir, ignore_errors=True)
            raise
        finally:
            with self._lock:
                self._uploading.discard(session.id)
        return {"session_id": session.id}

    # -- audio playback -----------------------------------------------------------------------

    def playback_file(self, session_id: str) -> Path:
        """One file with both sides of the conversation, for the player."""
        session = self._session(session_id)
        tracks = [session.dir / name for name in session.meta.tracks.values()]
        tracks = [path for path in tracks if path.is_file()]
        delays = {
            session.dir / session.meta.tracks[role]: delay
            for role, delay in session.meta.offsets.items()
            if role in session.meta.tracks
        }
        if not tracks:
            raise NotFound("this session has no audio", "no_audio")
        if len(tracks) == 1 and _plain_wav(tracks[0]):
            return tracks[0]
        mix = session.path_for(MIX_FILE)
        newest = max(path.stat().st_mtime for path in tracks)
        with self._mix_lock:  # browsers ask for several ranges at once
            if not mix.is_file() or mix.stat().st_mtime < newest:
                _mix_wavs(tracks, mix, delays)
        return mix

    # -- health -----------------------------------------------------------------------------

    def doctor(self) -> list[dict[str, str]]:
        """Readiness checks. ``fix`` is a code the app explains in plain words (``hint`` is the
        technical detail for the command line)."""
        from . import models

        checks: list[dict[str, str]] = []

        def add(key: str, ok: bool, detail: str, fix: str = "", hint: str = "") -> None:
            checks.append(
                {
                    "key": key,
                    "status": "ok" if ok else "fail",
                    "detail": detail,
                    "fix": fix,
                    "hint": hint,
                }
            )

        try:
            devices = audio.list_input_devices()
            mic = next((d for d in devices if d.is_default), None)
            add(
                "microphone",
                mic is not None,
                mic.name if mic else "no microphone found",
                "" if mic else "mic_permission",
            )
        except VechoError as exc:
            add("microphone", False, str(exc), "mic_permission")

        try:
            source = systemaudio.prepare(self.config.home / "bin")
            add("system_audio", True, source.name)
        except VechoError as exc:
            add("system_audio", False, str(exc), "system_audio", systemaudio.install_hint())

        whisper = importlib.util.find_spec("faster_whisper") is not None
        add(
            "whisper",
            whisper,
            engine_label(self.config) if whisper else "not installed",
            "" if whisper else "reinstall",
        )

        host, model = self.config.llm_host, self.config.llm_model
        if not models.ollama_running(host) and models.ollama_installed():
            models.start_ollama(host, wait=15)  # installed but not started yet
        client = OllamaClient(host, model, timeout=5)
        try:
            has_model = client.has_model()
            # a missing model is fine: the first summary downloads it
            add("llm", True, model, "" if has_model else "model_auto")
        except VechoError as exc:
            add(
                "llm",
                False,
                str(exc),
                "start_ollama" if models.ollama_installed() else "install_ollama",
            )
        return checks


PLAYBACK_TYPES = {".wav": "audio/wav"}

_MIX_BLOCK = 1 << 20  # frames per step: bounded memory however long the recording is


def _read_block(wav: wave.Wave_read, frames: int) -> np.ndarray:
    data = np.frombuffer(wav.readframes(frames), dtype="<i2").astype(np.int32)
    channels = wav.getnchannels()
    if channels > 1:
        data = data[: len(data) - len(data) % channels].reshape(-1, channels).mean(axis=1)
        data = data.astype(np.int32)
    return data


def _plain_wav(path: Path) -> bool:
    """A 16-bit PCM WAV the mixer and every browser can play as is."""
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getsampwidth() == 2
    except (wave.Error, EOFError, OSError):
        return False


def _as_plain_wav(path: Path, directory: Path) -> Path:
    """``path`` itself if it is a plain WAV, else a 16 kHz mono copy decoded with FFmpeg (PyAV)."""
    if _plain_wav(path):
        return path
    fd, name = tempfile.mkstemp(dir=directory, prefix=".decoded.", suffix=".wav")
    os.close(fd)
    try:
        from .decoding import pcm_blocks

        # frame by frame: a two-hour import never sits decoded in memory as a whole
        with wave.open(name, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            for block in pcm_blocks(path):
                out.writeframes(block.astype("<i2").tobytes())
    except Exception as exc:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise VechoError(f"cannot decode {path.name} for playback: {exc}") from exc
    return Path(name)


def _padded_block(
    reader: wave.Wave_read, whole: np.ndarray | None, pad: int, position: int
) -> np.ndarray:
    """Frames [position, position + block) of a track that starts ``pad`` frames late."""
    end = position + _MIX_BLOCK
    silence = max(0, min(end, pad) - position)
    start = max(0, position - pad)
    count = _MIX_BLOCK - silence
    audio = whole[start : start + count] if whole is not None else _read_block(reader, count)
    if not silence:
        return audio
    return np.concatenate([np.zeros(silence, dtype=np.int32), audio])


def _resample(data: np.ndarray, rate: int, target_rate: int) -> np.ndarray:
    positions = np.arange(int(len(data) * target_rate / rate)) * rate / target_rate
    return np.interp(positions, np.arange(len(data)), data).astype(np.int32)


def _mix_wavs(paths: list[Path], target: Path, delays: dict[Path, float] | None = None) -> None:
    """Sum the tracks into one mono file, block by block (bounded memory).

    ``delays`` (seconds) shifts tracks that started recording later than the others.
    """
    delays = delays or {}
    with contextlib.ExitStack() as stack:
        plain = [_as_plain_wav(path, target.parent) for path in paths]
        for original, usable in zip(paths, plain, strict=True):
            if usable != original:
                stack.callback(usable.unlink, missing_ok=True)
        delays = {
            usable: delays.get(original, 0.0) for original, usable in zip(paths, plain, strict=True)
        }
        paths = plain
        readers = [stack.enter_context(wave.open(str(path), "rb")) for path in paths]
        rate = max(reader.getframerate() for reader in readers)
        pads = [int(round(delays.get(path, 0.0) * rate)) for path in paths]
        # Rare: a device that only records at another rate. Those tracks are resampled whole.
        whole = {
            i: _resample(_read_block(r, r.getnframes()), r.getframerate(), rate)
            for i, r in enumerate(readers)
            if r.getframerate() != rate
        }
        fd, partial = tempfile.mkstemp(dir=target.parent, prefix=".mix.", suffix=".tmp")
        os.close(fd)
        try:
            with wave.open(partial, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(rate)
                position = 0
                while True:
                    blocks = [
                        _padded_block(reader, whole.get(i), pads[i], position)
                        for i, reader in enumerate(readers)
                    ]
                    length = max(len(block) for block in blocks)
                    if not length:
                        break
                    mixed = np.zeros(length, dtype=np.int32)
                    for block in blocks:
                        mixed[: len(block)] += block
                    out.writeframes(np.clip(mixed, -32768, 32767).astype("<i2").tobytes())
                    position += _MIX_BLOCK
            os.replace(partial, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(partial)
            raise


# Static files of the web UI, by public name -> content type. Nothing else is ever served.
ASSETS = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "PretendardVariable.woff2": "font/woff2",
}

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; media-src 'self'; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


def _ui_file(name: str) -> bytes:
    return resources.files("vecho").joinpath("resources", "ui", name).read_bytes()


def load_ui(token: str) -> bytes:
    return _ui_file("index.html").replace(b"__VECHO_TOKEN__", token.encode("ascii"))


_DRAIN_MAX = 64 * 1024 * 1024  # beyond this, a refused upload is simply cut off


def _optional_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise VechoError("expected text")
    return value


def _header_text(raw: str) -> str:
    """A header value the UI percent-encodes; other clients may send raw UTF-8 instead."""
    with contextlib.suppress(UnicodeError):  # http.server decodes headers as Latin-1
        raw = raw.encode("latin-1").decode("utf-8")
    return urllib.parse.unquote(raw)


class Handler(BaseHTTPRequestHandler):
    server: VechoHTTPServer
    protocol_version = "HTTP/1.1"
    timeout = 60  # a client that stalls mid-request must not hold a session "processing"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    # -- plumbing ---------------------------------------------------------------------------

    @property
    def app(self) -> App:
        return self.server.app

    def _send(
        self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None
    ) -> None:
        if not getattr(self, "_consumed", True):
            self._drain()
        self.send_response(status)
        if not getattr(self, "_consumed", True):
            # the request body was never read; it would be parsed as the next request
            self.close_connection = True
            self.send_header("Connection", "close")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        headers = headers or {}
        self.send_header("Cache-Control", headers.pop("Cache-Control", "no-store"))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _drain(self) -> None:
        """Read what is left of an unread request body before answering.

        A client still sending gets a connection reset instead of the answer when the server
        closes a socket with unread data, so a refused upload would look like a network error.
        """
        left = min(self._length_or_zero(), _DRAIN_MAX)
        with contextlib.suppress(OSError):
            self.connection.settimeout(2)
            while left > 0:
                chunk = self.rfile.read1(min(left, 1 << 16))
                if not chunk:
                    break
                left -= len(chunk)

    def _length_or_zero(self) -> int:
        raw = (self.headers.get("Content-Length") or "0").strip()
        return int(raw) if raw.isdigit() else 0

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str, code: str | None = None) -> None:
        self._json(status, {"error": message, **({"code": code} if code else {})})

    def _length(self) -> int:
        raw = (self.headers.get("Content-Length") or "0").strip()
        if not raw.isdigit():
            raise VechoError("invalid Content-Length")
        return int(raw)

    def _body(self) -> dict[str, Any]:
        length = self._length()
        if length > 1024 * 1024:
            raise VechoError("request too large", "request_too_large")
        raw = self.rfile.read(length) if length else b"{}"
        self._consumed = True
        try:
            data = json.loads(raw or b"{}")
        except (ValueError, RecursionError) as exc:
            raise VechoError("invalid JSON") from exc
        if not isinstance(data, dict):
            raise VechoError("invalid JSON")
        return data

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _token_ok(self, query: dict[str, list[str]]) -> bool:
        supplied = self.headers.get("X-Vecho-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(supplied, self.app.token)

    def _dispatch(self) -> None:
        # a body is only "unread" when one was sent; set True once a route reads it
        self._consumed = (self.headers.get("Content-Length") or "0").strip() in {"", "0"}
        url = urllib.parse.urlsplit(self.path)
        raw_path = url.path
        path = urllib.parse.unquote(raw_path)
        query = urllib.parse.parse_qs(url.query)
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "bad host")
            return
        if path in {"/", "/index.html"} and self.command in {"GET", "HEAD"}:
            self._send(
                HTTPStatus.OK,
                load_ui(self.app.token),
                "text/html; charset=utf-8",
                {"Content-Security-Policy": CSP},
            )
            return
        if path.startswith("/assets/") and self.command in {"GET", "HEAD"}:
            name = path[len("/assets/") :]
            if name in ASSETS:
                self._send(
                    HTTPStatus.OK, _ui_file(name), ASSETS[name], {"Cache-Control": "no-cache"}
                )
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if path == "/favicon.ico":
            self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
            return
        if not path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._token_ok(query):
            self._error(HTTPStatus.FORBIDDEN, "missing or wrong token", "bad_token")
            return
        # Split before decoding, so a "/" inside a name ("a%2Fb") stays part of that name.
        parts = [urllib.parse.unquote(p) for p in raw_path[len("/api/") :].strip("/").split("/")]
        try:
            self._route(self.command, parts, query)
        except NotFound as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc), exc.code)
        except Conflict as exc:
            self._error(HTTPStatus.CONFLICT, str(exc), exc.code)
        except (SessionError, VechoError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc), exc.code)
        except (ConnectionError, BrokenPipeError):
            raise  # the client went away; nothing to answer
        except Exception as exc:  # never drop a request without an answer
            self.log_error("unexpected error: %r", exc)
            full = isinstance(exc, OSError) and exc.errno == errno.ENOSPC
            if not full and isinstance(exc.__cause__, OSError):
                full = exc.__cause__.errno == errno.ENOSPC
            with contextlib.suppress(Exception):
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"unexpected error: {exc}",
                    "disk_full" if full else None,
                )

    def _route(self, method: str, parts: list[str], query: dict[str, list[str]]) -> None:
        app = self.app
        match method, parts:
            case "GET", ["state"]:
                self._json(200, app.state())
            case "POST", ["record", "start"]:
                body = self._body()
                self._json(
                    200,
                    app.start_recording(
                        _optional_text(body.get("title")) or "",
                        bool(body.get("mic_only", False)),
                        _optional_text(body.get("template")),
                    ),
                )
            case "POST", ["record", "stop"]:
                self._json(200, app.stop_recording())
            case "GET", ["sessions"]:
                self._json(200, app.list_sessions())
            case "GET", ["sessions", sid]:
                self._json(200, app.session_detail(sid))
            case "PATCH", ["sessions", sid]:
                self._json(200, app.rename(sid, _optional_text(self._body().get("title")) or ""))
            case "DELETE", ["sessions", sid]:
                app.delete(sid)
                self._json(200, {"deleted": sid})
            case "POST", ["sessions", sid, "process"]:
                body = self._body()
                self._json(
                    200,
                    app.process(
                        sid, str(body.get("step", "all")), _optional_text(body.get("template"))
                    ),
                )
            case (("GET" | "HEAD"), ["sessions", sid, "audio"]):
                self._file(app.playback_file(sid))
            case "POST", ["import"]:
                name = _header_text(self.headers.get("X-Filename", ""))
                length = self._length()
                template = _header_text(self.headers.get("X-Template", "")) or None
                result = app.import_audio(name, self.rfile, length, template)
                self._consumed = True
                self._json(200, result)
            case "GET", ["templates"]:
                self._json(200, app.list_templates())
            case "PUT", ["templates", name]:
                body = self._body()
                previous = _optional_text(body.get("previous"))
                self._json(200, app.save_template(name, str(body.get("body", "")), previous))
            case "DELETE", ["templates", name]:
                self._json(200, app.delete_template(name))
            case "POST", ["templates", name, "default"]:
                self._json(200, app.set_default_template(name))
            case "GET", ["doctor"]:
                self._json(200, app.doctor())
            case _:
                self._error(HTTPStatus.NOT_FOUND, "not found")

    def _file(self, path: Path) -> None:
        """Serve a file with Range support so the audio player can seek."""
        size = path.stat().st_size
        # the same on every system (Windows' registry says audio/wav, others audio/x-wav)
        content_type = PLAYBACK_TYPES.get(path.suffix.lower()) or (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        start, end = 0, size - 1
        status = HTTPStatus.OK
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if match and size:
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            elif last:
                start = max(0, size - int(last))
            if start > end or start >= size:
                self._send(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    b"",
                    content_type,
                    {"Content-Range": f"bytes */{size}"},
                )
                return
            status = HTTPStatus.PARTIAL_CONTENT
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - start + 1 if size else 0))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or not size:
            return
        with path.open("rb") as handle:  # stream: never hold a whole recording in memory
            handle.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = handle.read(min(1 << 20, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = _dispatch


class VechoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: App, port: int = 0) -> None:
        self.app = app
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A browser closing a connection early is normal; keep the log for real errors."""
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)
