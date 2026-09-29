"""A recording that runs until it is told to stop — shared by the CLI and the app server."""

from __future__ import annotations

import contextlib
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import audio, roles, systemaudio
from .config import Config
from .errors import AudioError, VechoError
from .session import Session, SessionStore

NO_SYSTEM_AUDIO_HELP = (
    "Use --mic-only to record just the microphone, or --remote to record another input device."
)

TOO_SHORT_SEC = 1.0

Source = audio.InputDevice | systemaudio.SystemAudioSource
Track = Any  # TrackRecorder, SystemAudioRecorder or LoopbackRecorder


def choose_remote(spec: str | None, devices: Sequence[audio.InputDevice], config: Config) -> Source:
    """How the other party is captured: the system audio by default (``None`` or ``system``),
    otherwise the named input device."""
    if spec is not None and spec.strip().lower() != "system":
        return audio.resolve_device(spec, devices)
    try:
        return systemaudio.prepare(config.home / "bin")
    except AudioError as exc:
        raise AudioError(f"{exc}\n{NO_SYSTEM_AUDIO_HELP}") from exc


def make_track(role: str, source: Source, path: Path, sample_rate: int) -> Track:
    if isinstance(source, systemaudio.SystemAudioSource):
        if source.backend == systemaudio.LOOPBACK:
            from .loopback import LoopbackRecorder

            return LoopbackRecorder(role, source, path, sample_rate)
        return systemaudio.SystemAudioRecorder(role, source, path, sample_rate)
    return audio.TrackRecorder(role, source, path, sample_rate)


def silence_hint(source: Source) -> str:
    if isinstance(source, systemaudio.SystemAudioSource):
        return source.silence_hint
    return f"check that audio is routed to '{source.name}'"


def describe_source(source: Source) -> str:
    return source.name if source.index < 0 else f"[{source.index}] {source.name}"


@dataclass
class RecordingResult:
    session: Session
    stats: list[audio.TrackStats]
    warnings: list[str] = field(default_factory=list)
    # Machine-readable twins of ``warnings`` for UIs: {"code", "role", "hint"}.
    issues: list[dict[str, str]] = field(default_factory=list)

    @property
    def too_short(self) -> bool:
        return (self.session.meta.duration_sec or 0.0) < TOO_SHORT_SEC


class LiveRecording:
    """Owns the session directory and track recorders of one recording."""

    def __init__(
        self,
        config: Config,
        title: str = "",
        mic: str | None = None,
        remote: str | None = None,
        mic_only: bool = False,
    ) -> None:
        self.config = config

        devices = audio.list_input_devices()
        self.sources: list[tuple[str, Source]] = [(roles.ME, audio.resolve_device(mic, devices))]
        if not mic_only:
            source = choose_remote(remote, devices, config)
            if isinstance(source, audio.InputDevice) and source.index == self.sources[0][1].index:
                raise AudioError("the microphone and the remote source must be different devices")
            self.sources.append((roles.REMOTE, source))

        self.session = SessionStore(config.sessions_dir).create(title)
        self.recorder = audio.Recorder(
            [
                make_track(role, source, self.session.path_for(f"{role}.wav"), config.sample_rate)
                for role, source in self.sources
            ]
        )
        self._started_at: float | None = None
        self._stopped = False

    def start(self) -> None:
        try:
            self.recorder.start()
        except BaseException:
            shutil.rmtree(self.session.dir, ignore_errors=True)  # nothing was recorded
            raise
        # Register the tracks first so an interrupted or failed stop still leaves a usable session.
        self.session.meta.tracks = {role: f"{role}.wav" for role, _ in self.sources}
        self.session.save()
        self._started_at = time.monotonic()

    @property
    def elapsed(self) -> float:
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at

    def levels(self) -> dict[str, float]:
        return self.recorder.levels()

    def abort(self) -> None:
        """Finalize the audio files after an interruption, ignoring errors."""
        with contextlib.suppress(VechoError):
            self.stop()

    def stop(self) -> RecordingResult:
        if self._stopped:
            raise AudioError("the recording was already stopped")
        self._stopped = True
        stats = self.recorder.stop()

        session = self.session
        session.meta.duration_sec = max((s.duration for s in stats), default=0.0)
        session.save()

        warnings: list[str] = []
        issues: list[dict[str, str]] = []
        sources = dict(self.sources)
        for stat in stats:
            label = self.config.label_for(stat.role)
            if stat.error:
                warnings.append(f"the {label} track stopped early: {stat.error}")
                issues.append({"code": "stopped", "role": stat.role, "hint": stat.error})
            elif stat.silent:
                hint = silence_hint(sources[stat.role])
                warnings.append(f"the {label} track is silent; {hint}.")
                issues.append({"code": "silent", "role": stat.role, "hint": hint})
            if stat.overflows:
                warnings.append(f"the {label} track dropped audio {stat.overflows} time(s).")
                issues.append({"code": "dropped", "role": stat.role, "hint": str(stat.overflows)})
        return RecordingResult(session, stats, warnings, issues)
