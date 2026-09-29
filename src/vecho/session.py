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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMeta:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class Session:
    def __init__(self, directory: Path, meta: SessionMeta) -> None:
        self.dir = directory
        self.meta = meta
        self._saved_title = meta.title

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
        """Write metadata atomically so a crash never leaves a truncated file.

        Several long-lived objects hold the same session (a running recording, a processing job)
        while the user may rename it through another. Unless *this* object changed the title,
        the title on disk wins, so a later save cannot silently undo a rename.
        """
        path = self.path_for(META_FILE)
        if self.meta.title == self._saved_title and path.is_file():
            with contextlib.suppress(OSError, ValueError, AttributeError):
                on_disk = json.loads(path.read_text("utf-8"))
                self.meta.title = str(on_disk.get("title", self.meta.title))
        write_atomic(path, json.dumps(asdict(self.meta), ensure_ascii=False, indent=2))
        self._saved_title = self.meta.title

    @classmethod
    def load(cls, directory: Path) -> Session:
        path = directory / META_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"cannot read session metadata {path}: {exc}") from exc
        return cls(directory, SessionMeta.from_dict(data))


def write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` in one step, so readers (like the app) never see a half-written file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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
        sessions = self.list()
        if not sessions:
            raise SessionError(f"no sessions found in {self.root}")
        if ref in {"latest", "last"}:
            return sessions[0]
        for matcher in (
            lambda s: s.id == ref,
            lambda s: s.id.startswith(ref),
            lambda s: ref in s.id,
        ):
            matches = [s for s in sessions if matcher(s)]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                ids = ", ".join(s.id for s in matches[:5])
                raise SessionError(f"'{ref}' is ambiguous; it matches: {ids}")
        raise SessionError(f"no session matches '{ref}'")
