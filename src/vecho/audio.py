"""Audio input: device discovery and simultaneous multi-track recording.

A two-way conversation is captured as two independent mono tracks: the
microphone (you) and a loopback device such as BlackHole that carries the
system output (the other party). Keeping the tracks separate gives free
speaker attribution later, without diarization.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .errors import AudioError

# Virtual devices that expose system audio as an input on macOS.
LOOPBACK_HINTS = ("blackhole", "loopback audio", "soundflower")

# Normalized peak (0..1) below which a whole track is considered silent.
SILENCE_PEAK = 0.01

_INT16_FULL_SCALE = 32768.0


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    channels: int
    default_samplerate: float
    is_default: bool = False

    @property
    def is_loopback(self) -> bool:
        lowered = self.name.lower()
        return any(hint in lowered for hint in LOOPBACK_HINTS)


def _sounddevice() -> Any:
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        raise AudioError(f"audio backend (sounddevice/PortAudio) is unavailable: {exc}") from exc
    return sounddevice


def list_input_devices() -> list[InputDevice]:
    sd = _sounddevice()
    try:
        default_index = sd.default.device[0]
        raw = sd.query_devices()
    except Exception as exc:  # PortAudioError and friends
        raise AudioError(f"cannot query audio devices: {exc}") from exc
    return [
        InputDevice(
            index=index,
            name=str(info["name"]),
            channels=int(info["max_input_channels"]),
            default_samplerate=float(info["default_samplerate"]),
            is_default=index == default_index,
        )
        for index, info in enumerate(raw)
        if info["max_input_channels"] > 0
    ]


def find_loopback_device(devices: Sequence[InputDevice]) -> InputDevice | None:
    return next((d for d in devices if d.is_loopback), None)


def resolve_device(spec: str | None, devices: Sequence[InputDevice]) -> InputDevice:
    """Pick an input device by index, by (partial) name, or the system default when None."""
    if not devices:
        raise AudioError("no audio input devices found; check microphone permissions")

    if spec is None:
        return next((d for d in devices if d.is_default), devices[0])

    if spec.strip().isdigit():
        wanted = int(spec)
        for device in devices:
            if device.index == wanted:
                return device
        raise AudioError(f"device {wanted} is not an input device; run `vecho devices`")

    needle = spec.strip().lower()
    exact = [d for d in devices if d.name.lower() == needle]
    matches = exact or [d for d in devices if needle in d.name.lower()]
    if not matches:
        raise AudioError(f"no input device matches '{spec}'; run `vecho devices`")
    if len(matches) > 1:
        names = ", ".join(f"{d.index}:{d.name}" for d in matches)
        raise AudioError(f"'{spec}' is ambiguous ({names}); use the device index instead")
    return matches[0]


@dataclass(frozen=True)
class TrackStats:
    role: str
    path: Path
    sample_rate: int
    frames: int
    peak: float
    overflows: int
    error: str | None = None  # set when the source failed but audio was kept

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def silent(self) -> bool:
        return self.peak < SILENCE_PEAK


StreamFactory = Callable[[int, int, int, Callable[..., None]], Any]


def _default_stream_factory(
    device: int, samplerate: int, channels: int, callback: Callable[..., None]
) -> Any:
    sd = _sounddevice()
    return sd.InputStream(
        device=device, samplerate=samplerate, channels=channels, dtype="int16", callback=callback
    )


class TrackRecorder:
    """Streams one input device to a mono 16-bit WAV file.

    The audio callback only downmixes and enqueues; a writer thread does the
    disk I/O so a slow write can never stall the real-time audio thread.
    """

    def __init__(
        self,
        role: str,
        device: InputDevice,
        path: Path,
        sample_rate: int = 16000,
        stream_factory: StreamFactory | None = None,
    ) -> None:
        self.role = role
        self.device = device
        self.path = path
        self.sample_rate = sample_rate
        self._factory = stream_factory or _default_stream_factory
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._stream: Any = None
        self._wav: wave.Wave_write | None = None
        self._thread: threading.Thread | None = None
        self._frames = 0
        self._write_error: Exception | None = None
        self.level = 0.0
        self.peak = 0.0
        self.overflows = 0

    def start(self) -> None:
        channels = max(1, min(2, self.device.channels))
        rates = [self.sample_rate]
        native = int(self.device.default_samplerate)
        if native and native != self.sample_rate:
            rates.append(native)  # some devices refuse the preferred rate

        last_error: Exception | None = None
        for rate in rates:
            try:
                self._stream = self._factory(self.device.index, rate, channels, self._on_audio)
                self.sample_rate = rate
                break
            except Exception as exc:
                last_error = exc
        else:
            raise AudioError(f"cannot open '{self.device.name}': {last_error}")

        try:
            # Held open for the whole recording; closed in _teardown().
            self._wav = wave.open(str(self.path), "wb")  # noqa: SIM115
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(self.sample_rate)
            self._thread = threading.Thread(
                target=self._write_loop, name=f"vecho-writer-{self.role}", daemon=True
            )
            self._thread.start()
            self._stream.start()
        except Exception as exc:
            self._teardown()
            raise AudioError(f"cannot start recording from '{self.device.name}': {exc}") from exc

    def _on_audio(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if getattr(status, "input_overflow", False):
            self.overflows += 1
        block = np.asarray(indata)
        if block.ndim == 2 and block.shape[1] > 1:
            mono = block.astype(np.int32).mean(axis=1).astype(np.int16)
        else:
            mono = block.reshape(-1).astype(np.int16)
        if mono.size == 0:
            return
        level = int(np.abs(mono.astype(np.int32)).max()) / _INT16_FULL_SCALE
        self.level = level
        self.peak = max(self.peak, level)
        self._queue.put(mono.tobytes())

    def _write_loop(self) -> None:
        while True:
            chunk = self._queue.get()
            if chunk is None:
                return
            if self._write_error is not None or self._wav is None:
                continue  # keep draining so the queue cannot grow after a failure
            try:
                self._wav.writeframes(chunk)
                self._frames += len(chunk) // 2
            except Exception as exc:  # e.g. disk full
                self._write_error = exc

    def _teardown(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            for action in (stream.stop, stream.close):
                with contextlib.suppress(Exception):  # the device may already be gone
                    action()
        thread, self._thread = self._thread, None
        if thread is not None:
            self._queue.put(None)
            thread.join()
        wav, self._wav = self._wav, None
        if wav is not None:
            wav.close()

    def stop(self) -> TrackStats:
        self._teardown()
        if self._write_error is not None:
            raise AudioError(f"writing {self.path.name} failed: {self._write_error}")
        return TrackStats(
            role=self.role,
            path=self.path,
            sample_rate=self.sample_rate,
            frames=self._frames,
            peak=self.peak,
            overflows=self.overflows,
        )


class Recorder:
    """Starts and stops several tracks together."""

    def __init__(self, tracks: Sequence[TrackRecorder]) -> None:
        self.tracks = list(tracks)
        self._started_at: float | None = None

    def start(self) -> None:
        started: list[TrackRecorder] = []
        try:
            for track in self.tracks:
                track.start()
                started.append(track)
        except Exception:
            for track in started:
                with contextlib.suppress(AudioError):
                    track.stop()
            raise
        self._started_at = time.monotonic()

    @property
    def elapsed(self) -> float:
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at

    def levels(self) -> dict[str, float]:
        return {track.role: track.level for track in self.tracks}

    def stop(self) -> list[TrackStats]:
        stats: list[TrackStats] = []
        errors: list[str] = []
        for track in self.tracks:
            try:
                stats.append(track.stop())
            except AudioError as exc:
                errors.append(str(exc))
        if errors:
            raise AudioError("; ".join(errors))
        return stats
