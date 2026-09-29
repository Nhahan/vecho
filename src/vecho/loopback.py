"""System audio capture on Windows (WASAPI loopback) and Linux (PulseAudio/PipeWire monitor).

Both platforms expose "what the speakers play" as a loopback microphone of the default
output device, which the ``soundcard`` library can open without drivers or admin rights.
The recorder follows the default output when it changes (headphones plugged in mid-call)
and pads any gap with silence so this track stays aligned with the microphone track.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .audio import TrackStats
from .errors import AudioError

CAPTURE_RATE = 48000
BLOCK_SEC = 0.1
FOLLOW_SEC = 2.0
RETRY_SEC = 1.0


def load_soundcard() -> Any:
    try:
        import soundcard
    except Exception as exc:  # ImportError, or a missing PulseAudio library on Linux
        raise AudioError(f"system audio capture is unavailable: {exc}") from exc
    return soundcard


def open_loopback(sc: Any) -> tuple[Any, Any]:
    """(default speaker, its loopback microphone)."""
    speaker = sc.default_speaker()
    if speaker is None:
        raise AudioError("there is no sound output device")
    microphone = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    return speaker, microphone


class LoopbackRecorder:
    """Same interface as ``audio.TrackRecorder``: start(), stop() -> TrackStats, level, peak."""

    def __init__(
        self,
        role: str,
        source: Any,
        path: Path,
        sample_rate: int = 16000,
        soundcard_module: Any = None,
        follow_sec: float = FOLLOW_SEC,
    ) -> None:
        from .systemaudio import Decimator

        self.role = role
        self.device = source
        self.path = path
        self.sample_rate = sample_rate if CAPTURE_RATE % sample_rate == 0 else CAPTURE_RATE
        self._factor = CAPTURE_RATE // self.sample_rate
        self._decimator = Decimator(self._factor)
        self._sc = soundcard_module
        self._follow_sec = follow_sec
        self.level = 0.0
        self.peak = 0.0
        self.overflows = 0
        self._frames = 0
        self._wav: wave.Wave_write | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_error: Exception | None = None
        self._error: str | None = None
        self._started_at = 0.0
        self._first_at: float | None = None

    def start(self) -> None:
        sc = self._sc or load_soundcard()
        self._sc = sc
        self._wav = wave.open(str(self.path), "wb")  # noqa: SIM115 - closed in stop()
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self.sample_rate)
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="vecho-loopback", daemon=True)
        self._thread.start()
        self._ready.wait(10)
        if self._start_error is not None:
            self._stop.set()
            self._thread.join(5)
            self._wav.close()
            self._wav = None
            raise AudioError(f"system audio capture failed: {self._start_error}")

    def _write(self, samples: np.ndarray) -> None:
        if len(samples) == 0 or self._wav is None:
            return
        if self._first_at is None:
            self._first_at = time.monotonic() - len(samples) / self.sample_rate
        level = int(np.abs(samples.astype(np.int32)).max()) / 32768.0
        self.level = level
        self.peak = max(self.peak, level)
        self._wav.writeframes(samples.astype("<i2").tobytes())
        self._frames += len(samples)

    def _pad_to_wall_clock(self) -> None:
        """Fill time lost while (re)opening the device with silence, keeping tracks aligned."""
        expected = int((time.monotonic() - self._started_at) * self.sample_rate)
        missing = expected - self._frames - int(BLOCK_SEC * self.sample_rate)
        if missing > 0:
            self._write(np.zeros(missing, dtype=np.int16))

    def _run(self) -> None:
        sc = self._sc
        first = True
        while not self._stop.is_set():
            try:
                speaker, microphone = open_loopback(sc)
                with microphone.recorder(samplerate=CAPTURE_RATE, channels=None) as stream:
                    if first:
                        first = False
                        self._ready.set()
                    else:
                        self._pad_to_wall_clock()
                        self._error = None  # recovered; the gap was filled with silence
                    checked = time.monotonic()
                    while not self._stop.is_set():
                        block = np.asarray(stream.record(numframes=int(CAPTURE_RATE * BLOCK_SEC)))
                        if block.ndim == 2:
                            block = block.mean(axis=1)
                        pcm = np.clip(np.rint(block * 32767), -32768, 32767).astype(np.int16)
                        self._write(self._decimator.process(pcm))
                        if time.monotonic() - checked >= self._follow_sec:
                            checked = time.monotonic()
                            current = sc.default_speaker()
                            if current is not None and current.id != speaker.id:
                                break  # the user switched outputs: follow it
            except Exception as exc:
                if first:
                    self._start_error = exc
                    self._ready.set()
                    return
                self._error = str(exc)
                self._stop.wait(RETRY_SEC)

    def stop(self) -> TrackStats:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception as exc:  # e.g. disk full while fixing up the header
                self._error = self._error or f"writing {self.path.name} failed: {exc}"
            self._wav = None
        return TrackStats(
            role=self.role,
            path=self.path,
            sample_rate=self.sample_rate,
            frames=self._frames,
            peak=self.peak,
            overflows=self.overflows,
            error=self._error,
            started_at=self._first_at,
        )
