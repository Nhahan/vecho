"""On-disk storage for recording sessions.

Each session is a directory holding its audio tracks, metadata and generated
artifacts::

    <home>/sessions/20260929-093000-weekly-sync/
        session.json  me.wav  remote.wav
        transcript.json  transcript.md  summary.md
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import threading
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import SessionError

META_FILE = "session.json"
TRANSCRIPT_JSON = "transcript.json"
TRANSCRIPT_MD = "transcript.md"
SUMMARY_MD = "summary.md"

_SLUG_MAX = 40


@dataclass
class SessionMeta:
    id: str
    title: str
    created_at: str
    duration_sec: float | None = None
    tracks: dict[str, str] = field(default_factory=dict)  # role -> audio file name
    language: str | None = None
    whisper_model: str | None = None
    llm_model: str | None = None
    transcribed_at: str | None = None
    summarized_at: str | None = None
    issues: list[dict[str, str]] = field(default_factory=list)  # recording problems, for the app
    template: str | None = None  # summary template chosen for this session
    offsets: dict[str, float] = field(default_factory=dict)  # track start delays, in seconds
    summary_template: str | None = None  # template summary.md was made with

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMeta:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class Session:
    def __init__(self, directory: Path, meta: SessionMeta) -> None:
        self.dir = directory
        self.meta = meta
        self._base = asdict(meta)  # the metadata as last read or written by this object

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def display_title(self) -> str:
        return self.meta.title or self.meta.id

    def path_for(self, name: str) -> Path:
        return self.dir / name

    def audio_path(self, role: str) -> Path:
        try:
            return self.dir / self.meta.tracks[role]
        except KeyError:
            raise SessionError(f"session {self.id} has no '{role}' track") from None

    @property
    def has_transcript(self) -> bool:
        return self.path_for(TRANSCRIPT_JSON).is_file()

    @property
    def has_summary(self) -> bool:
        return self.path_for(SUMMARY_MD).is_file()

    def save(self) -> None:
        """Write metadata atomically, merging with changes others made in the meantime.

        Several objects can hold the same session at once (a running recording, a processing
        job, the app renaming it). Only the fields *this* object changed are written; every
        other field keeps what is on disk, so one save never undoes another's work.
        """
        path = self.path_for(META_FILE)
        with _lock_for(path):
            mine = asdict(self.meta)
            merged = dict(mine)
            with contextlib.suppress(OSError, ValueError, AttributeError, TypeError):
                on_disk = asdict(SessionMeta.from_dict(json.loads(path.read_text("utf-8"))))
                for key, value in on_disk.items():
                    if mine.get(key) == self._base.get(key):  # unchanged here: theirs wins
                        merged[key] = value
            for key, value in merged.items():
                setattr(self.meta, key, value)
            write_atomic(path, json.dumps(merged, ensure_ascii=False, indent=2))
            self._base = asdict(self.meta)

    def refresh(self) -> None:
        """Pick up changes others saved (e.g. a rename) without losing unsaved ones here."""
        self.save()

    @classmethod
    def load(cls, directory: Path) -> Session:
        path = directory / META_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            meta = SessionMeta.from_dict(data)
        except (OSError, ValueError, TypeError) as exc:  # includes bad UTF-8 and missing fields
            raise SessionError(f"cannot read session metadata {path}: {exc}") from exc
        return cls(directory, meta)


_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.Lock())


def write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` in one step, so readers (like the app) never see a half-written file.

    Each write uses its own temporary file, so two threads saving at once cannot collide.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def slugify(title: str) -> str:
    """Filesystem-safe slug that keeps Unicode letters such as Hangul."""
    slug = re.sub(r"[^\w]+", "-", title.strip().lower()).strip("-_")
    return slug[:_SLUG_MAX].strip("-_")


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now()).astimezone().isoformat(timespec="seconds")


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def create(self, title: str = "", now: datetime | None = None) -> Session:
        title = unicodedata.normalize("NFC", title)  # macOS file names arrive decomposed
        now = now or datetime.now()
        base = now.strftime("%Y%m%d-%H%M%S")
        slug = slugify(title)
        session_id = f"{base}-{slug}" if slug else base

        self.root.mkdir(parents=True, exist_ok=True)
        directory = self.root / session_id
        suffix = 1
        while True:
            try:
                directory.mkdir()
                break
            except FileExistsError:
                suffix += 1
                directory = self.root / f"{session_id}-{suffix}"
        meta = SessionMeta(id=directory.name, title=title.strip(), created_at=now_iso(now))
        session = Session(directory, meta)
        session.save()
        return session

    def list(self) -> list[Session]:
        """All readable sessions, newest first."""
        if not self.root.is_dir():
            return []
        sessions = []
        for directory in sorted(self.root.iterdir(), reverse=True):
            if not (directory / META_FILE).is_file():
                continue
            try:
                sessions.append(Session.load(directory))
            except SessionError:
                continue
        return sessions

    def resolve(self, ref: str) -> Session:
        """Find a session by ``latest``, exact id, unique prefix, or unique substring."""
        ref = unicodedata.normalize("NFC", ref)
        sessions = self.list()
        if not sessions:
            raise SessionError(f"no sessions found in {self.root}")
        if ref in {"latest", "last"}:
            return sessions[0]
        for matcher in (
            lambda s: _nfc(s.id) == ref,
            lambda s: _nfc(s.id).startswith(ref),
            lambda s: ref in _nfc(s.id),
        ):
            matches = [s for s in sessions if matcher(s)]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                ids = ", ".join(s.id for s in matches[:5])
                raise SessionError(f"'{ref}' is ambiguous; it matches: {ids}")
        raise SessionError(f"no session matches '{ref}'")
