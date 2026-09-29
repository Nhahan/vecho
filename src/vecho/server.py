"""Local web app: recording controls, processing and history in the browser.

Runs on 127.0.0.1 only. Every API call must carry a per-run random token and a matching
Host header, so other web pages open in the same browser cannot drive it.
"""

from __future__ import annotations

import importlib.util
import json
import mimetypes
import re
import secrets
import shutil
import threading
import urllib.parse
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from . import __version__, audio, jobs, recording, roles, systemaudio, transcript
from .config import Config
from .errors import SessionError, VechoError
from .session import SUMMARY_MD, TRANSCRIPT_JSON, Session, SessionStore
from .summarize import OllamaClient

MAX_UPLOAD_BYTES = 2 * 1024**3
MIX_FILE = "mix.wav"
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm", ".mp4"}
_SESSION_ID = re.compile(r"^[\w.\-]+$")


class NotFound(VechoError):
    pass


class Conflict(VechoError):
    pass


def summary_body(markdown: str) -> str:
    """Strip the title/metadata header that ``summary.md`` starts with."""
    index = markdown.find("\n## ")
    return markdown[index + 1 :].strip() if index >= 0 else markdown.strip()


def tldr_of(markdown: str) -> str:
    body = summary_body(markdown)
    for line in body.splitlines():
        text = line.strip().lstrip("-*").strip()
        if text and not line.startswith("#"):
            return text[:200]
    return ""


class App:
    """Everything the web UI can do, independent of HTTP."""

    def __init__(self, config: Config, processor: jobs.Processor | None = None) -> None:
        self.config = config
        self.store = SessionStore(config.sessions_dir)
        self.processor = processor or jobs.Processor(config)
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()
        self._live: recording.LiveRecording | None = None
        self._issues: dict[str, list[dict[str, str]]] = {}
        self._notes: list[str] = []

    # -- recording --------------------------------------------------------------------------

    def start_recording(self, title: str = "", mic_only: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._live is not None:
                raise Conflict("a recording is already running")
            notes: list[str] = []
            issues: list[dict[str, str]] = []
            live = recording.LiveRecording(
                self.config,
                title=title.strip(),
                mic_only=mic_only,
                warn=lambda message: issues.append(
                    {"code": "routing", "role": "", "hint": message}
                ),
                note=notes.append,
            )
            live.start()
            self._live = live
            self._issues[live.session.id] = issues
            self._notes = notes
        return self.state()

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            live, self._live = self._live, None
        if live is None:
            raise Conflict("nothing is being recorded")
        result = live.stop()
        session = result.session
        issues = self._issues.setdefault(session.id, [])
        issues.extend(result.issues)
        processing = not result.too_short
        if processing:
            self.processor.submit(session, "all")
        else:
            issues.append({"code": "too_short", "role": "", "hint": ""})
        session.meta.issues = issues
        session.save()
        return {
            "session_id": session.id,
            "issues": issues,
            "processing": processing,
        }

    def shutdown(self) -> None:
        """Keep whatever is being recorded when the app quits."""
        with self._lock:
            live, self._live = self._live, None
        if live is not None:
            live.abort()
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
                "notes": self._notes,
                "issues": self._issues.get(live.session.id, []),
            }
        return {"version": __version__, "recording": current, "jobs": self.processor.snapshot()}

    # -- sessions ---------------------------------------------------------------------------

    def _session(self, session_id: str) -> Session:
        if not _SESSION_ID.match(session_id) or session_id in {".", ".."}:
            raise NotFound("no such session")
        directory = self.store.root / session_id
        if not (directory / "session.json").is_file():
            raise NotFound("no such session")
        return Session.load(directory)

    def _status(self, session: Session) -> str:
        live = self._live
        if live is not None and live.session.id == session.id:
            return "recording"
        job = self.processor.state(session.id)
        if job is not None and job.active:
            return "processing"
        if job is not None and job.stage == jobs.ERROR:
            return "error"
        if session.has_summary:
            return "summarized"
        if session.has_transcript:
            return "transcribed" if jobs.has_speech(session) else "empty"
        return "recorded"

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
                    "tldr": tldr_of(summary.read_text("utf-8")) if summary.is_file() else "",
                }
            )
        return items

    def session_detail(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        summary_path = session.path_for(SUMMARY_MD)
        segments = []
        if session.has_transcript:
            for segment in transcript.coalesce(
                transcript.load_segments(session.path_for(TRANSCRIPT_JSON))
            ):
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
            "tracks": list(meta.tracks),
            "summary": summary_body(summary_path.read_text("utf-8"))
            if summary_path.is_file()
            else "",
            "segments": segments,
            "job": job.to_dict() if job else None,
            "issues": meta.issues,
            "labels": {role: self.config.label_for(role) for role in roles.ALL},
        }

    def rename(self, session_id: str, title: str) -> dict[str, Any]:
        session = self._session(session_id)
        title = title.strip()
        if not title:
            raise VechoError("the title cannot be empty")
        session.meta.title = title[:200]
        session.save()
        return self.session_detail(session_id)

    def delete(self, session_id: str) -> None:
        session = self._session(session_id)
        if self._status(session) in {"recording", "processing"}:
            raise Conflict("this session is still being recorded or processed")
        shutil.rmtree(session.dir)
        self.processor.forget(session_id)
        self._issues.pop(session_id, None)

    def process(self, session_id: str, step: str) -> dict[str, Any]:
        session = self._session(session_id)
        if self._status(session) == "recording":
            raise Conflict("stop the recording first")
        if step == "summarize" and not session.has_transcript:
            step = "all"
        self.processor.submit(session, step)
        return self.session_detail(session_id)

    def import_audio(self, filename: str, stream: Any, length: int) -> dict[str, Any]:
        suffix = Path(filename).suffix.lower()
        if suffix not in AUDIO_EXTENSIONS:
            raise VechoError(f"unsupported file type '{suffix or filename}'")
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            raise VechoError("the file is empty or too large")
        session = self.store.create(Path(filename).stem)
        name = f"{roles.MIXED}{suffix}"
        target = session.path_for(name)
        try:
            with target.open("wb") as out:
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise VechoError("the upload was interrupted")
                    out.write(chunk)
                    remaining -= len(chunk)
        except BaseException:
            shutil.rmtree(session.dir, ignore_errors=True)
            raise
        session.meta.tracks = {roles.MIXED: name}
        session.save()
        self.processor.submit(session, "all")
        return {"session_id": session.id}

    # -- audio playback -----------------------------------------------------------------------

    def playback_file(self, session_id: str) -> Path:
        """One file with both sides of the conversation, for the player."""
        session = self._session(session_id)
        tracks = [session.dir / name for name in session.meta.tracks.values()]
        tracks = [path for path in tracks if path.is_file()]
        if not tracks:
            raise NotFound("this session has no audio")
        if len(tracks) == 1:
            return tracks[0]
        if any(path.suffix.lower() != ".wav" for path in tracks):
            return tracks[0]
        mix = session.path_for(MIX_FILE)
        newest = max(path.stat().st_mtime for path in tracks)
        if not mix.is_file() or mix.stat().st_mtime < newest:
            _mix_wavs(tracks, mix)
        return mix

    # -- health -----------------------------------------------------------------------------

    def doctor(self) -> list[dict[str, str]]:
        checks: list[dict[str, str]] = []

        def add(key: str, ok: bool, detail: str, fix: str = "") -> None:
            checks.append(
                {"key": key, "status": "ok" if ok else "fail", "detail": detail, "fix": fix}
            )

        try:
            devices = audio.list_input_devices()
            mic = next((d for d in devices if d.is_default), None)
            add(
                "microphone",
                mic is not None,
                mic.name if mic else "no microphone found",
                "" if mic else "Connect a microphone and allow microphone access for this app.",
            )
        except VechoError as exc:
            devices = []
            add("microphone", False, str(exc), "Allow microphone access for this app.")

        try:
            source = systemaudio.prepare(self.config.home / "bin")
            add("system_audio", True, source.name)
        except VechoError as exc:
            loopback = audio.find_loopback_device(devices)
            if loopback is not None:
                add("system_audio", True, loopback.name)
            else:
                add("system_audio", False, str(exc), systemaudio.install_hint())

        whisper = importlib.util.find_spec("faster_whisper") is not None
        add(
            "whisper",
            whisper,
            self.config.whisper_model if whisper else "not installed",
            "" if whisper else "pip install faster-whisper",
        )

        client = OllamaClient(self.config.llm_host, self.config.llm_model, timeout=5)
        try:
            has_model = client.has_model()
            add(
                "llm",
                has_model,
                self.config.llm_model,
                "" if has_model else f"ollama pull {self.config.llm_model}",
            )
        except VechoError as exc:
            add("llm", False, str(exc), "Install Ollama from https://ollama.com and start it.")
        return checks


def _read_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        if wav.getnchannels() > 1:
            data = data.reshape(-1, wav.getnchannels()).mean(axis=1)
    return rate, data.astype(np.float64)


def _mix_wavs(paths: list[Path], target: Path) -> None:
    loaded = [_read_wav(path) for path in paths]
    rate = max(r for r, _ in loaded)
    tracks = []
    for track_rate, data in loaded:
        if track_rate != rate and len(data):
            positions = np.arange(int(len(data) * rate / track_rate)) * track_rate / rate
            data = np.interp(positions, np.arange(len(data)), data)
        tracks.append(data)
    length = max((len(t) for t in tracks), default=0)
    mixed = np.zeros(length)
    for data in tracks:
        mixed[: len(data)] += data
    pcm = np.clip(np.rint(mixed), -32768, 32767).astype("<i2")
    partial = target.with_suffix(".tmp")
    with wave.open(str(partial), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    partial.replace(target)


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


class Handler(BaseHTTPRequestHandler):
    server: VechoHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    # -- plumbing ---------------------------------------------------------------------------

    @property
    def app(self) -> App:
        return self.server.app

    def _send(
        self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
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

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 * 1024:
            raise VechoError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
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
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
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
            self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
            return
        try:
            self._route(self.command, path[len("/api/") :].strip("/").split("/"), query)
        except NotFound as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
        except Conflict as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except (SessionError, VechoError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

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
                        str(body.get("title", "")), bool(body.get("mic_only", False))
                    ),
                )
            case "POST", ["record", "stop"]:
                self._json(200, app.stop_recording())
            case "GET", ["sessions"]:
                self._json(200, app.list_sessions())
            case "GET", ["sessions", sid]:
                self._json(200, app.session_detail(sid))
            case "PATCH", ["sessions", sid]:
                self._json(200, app.rename(sid, str(self._body().get("title", ""))))
            case "DELETE", ["sessions", sid]:
                app.delete(sid)
                self._json(200, {"deleted": sid})
            case "POST", ["sessions", sid, "process"]:
                self._json(200, app.process(sid, str(self._body().get("step", "all"))))
            case (("GET" | "HEAD"), ["sessions", sid, "audio"]):
                self._file(app.playback_file(sid))
            case "POST", ["import"]:
                name = urllib.parse.unquote(self.headers.get("X-Filename", ""))
                length = int(self.headers.get("Content-Length") or 0)
                self._json(200, app.import_audio(name, self.rfile, length))
            case "GET", ["doctor"]:
                self._json(200, app.doctor())
            case _:
                self._error(HTTPStatus.NOT_FOUND, "not found")

    def _file(self, path: Path) -> None:
        """Serve a file with Range support so the audio player can seek."""
        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
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
        with path.open("rb") as handle:
            handle.seek(start)
            body = handle.read(end - start + 1)
        headers = {"Accept-Ranges": "bytes"}
        if status == HTTPStatus.PARTIAL_CONTENT:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._send(status, body, content_type, headers)

    do_GET = do_POST = do_PATCH = do_DELETE = do_HEAD = _dispatch


class VechoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: App, port: int = 0) -> None:
        self.app = app
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"
