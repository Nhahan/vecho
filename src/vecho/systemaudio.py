"""Driverless capture of everything the computer plays ("the other party").

macOS: a Core Audio process tap (below). Windows and Linux: the loopback of the default
output device, see :mod:`vecho.loopback`.

On macOS:

No virtual audio device, no change to the sound output and no admin rights are involved,
so nothing shows up in the user's sound settings. It works with any output (speakers,
AirPods, a monitor...) and keeps working when the output changes mid-call. It needs
macOS 14.4+; older systems fall back to a loopback device such as BlackHole.

A small compiled Swift helper does the capture and streams raw PCM over a pipe (see
``resources/system_audio.swift`` for the protocol); this module builds it on first use
and turns the stream into a WAV track.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import wave
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import numpy as np

from .audio import TrackStats
from .errors import AudioError

MIN_MACOS = (14, 4)
TARGET_RATE = 16000
_READ_SIZE = 65536

SYSTEM_AUDIO_NAME = "System audio (all apps)"


MAC_SILENCE_HINT = (
    "allow your terminal under System Settings > Privacy & Security > "
    "Screen & System Audio Recording, and check that something is playing"
)
OTHER_SILENCE_HINT = "check that something is playing through the default sound output"

TAP = "tap"
LOOPBACK = "loopback"


@dataclass(frozen=True)
class SystemAudioSource:
    """Stands in for an input device when the other party is captured from system audio."""

    command: tuple[str, ...] = ()
    backend: str = TAP
    name: str = SYSTEM_AUDIO_NAME
    index: int = -1
    is_loopback: bool = False  # no sound-output routing is ever needed
    silence_hint: str = MAC_SILENCE_HINT


def macos_version() -> tuple[int, ...]:
    if platform.system() != "Darwin":
        return ()
    try:
        return tuple(int(part) for part in platform.mac_ver()[0].split("."))
    except ValueError:
        return ()


def is_supported() -> bool:
    """True when this computer can capture system audio without extra software."""
    system = platform.system()
    if system == "Darwin":
        return macos_version()[:2] >= MIN_MACOS
    return system in {"Windows", "Linux"}


def install_hint() -> str:
    """What to do when system audio cannot be captured on this platform."""
    system = platform.system()
    if system == "Darwin":
        if macos_version()[:2] >= MIN_MACOS:
            return "Install the Xcode command line tools: xcode-select --install"
        return "Update to macOS 14.4 or later, or install BlackHole (see the README)."
    if system == "Linux":
        return "Use PulseAudio or PipeWire (with pipewire-pulse) and install libpulse."
    return "Check that a sound output device is enabled in the sound settings."


def _script_text() -> str:
    return resources.files("vecho").joinpath("resources", "system_audio.swift").read_text("utf-8")


def build_helper(bin_dir: Path) -> Path:
    """Compile the capture helper once; the binary is cached by source hash."""
    source = _script_text()
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    binary = bin_dir / f"vecho-system-audio-{digest}"
    if binary.is_file() and os.access(binary, os.X_OK):
        return binary

    swiftc = shutil.which("swiftc")
    if swiftc is None:
        raise AudioError(
            "swiftc is missing, so system audio capture cannot be built; "
            "install the Xcode command line tools: xcode-select --install"
        )
    bin_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "system_audio.swift"
        script.write_text(source, encoding="utf-8")
        partial = bin_dir / f".{binary.name}.tmp"
        proc = subprocess.run(
            [swiftc, "-O", str(script), "-o", str(partial)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            partial.unlink(missing_ok=True)
            raise AudioError(f"cannot build the system audio helper: {proc.stderr.strip()[-400:]}")
        os.replace(partial, binary)
    for stale in bin_dir.glob("vecho-system-audio-*"):
        if stale != binary:
            stale.unlink(missing_ok=True)
    return binary


def prepare(bin_dir: Path) -> SystemAudioSource:
    """Return a ready-to-record source, or raise AudioError explaining why not."""
    if platform.system() in {"Windows", "Linux"}:
        from . import loopback

        sc = loopback.load_soundcard()
        try:
            speaker, _ = loopback.open_loopback(sc)
        except AudioError:
            raise
        except Exception as exc:
            raise AudioError(f"system audio capture is unavailable: {exc}") from exc
        return SystemAudioSource(
            backend=LOOPBACK,
            name=f"{SYSTEM_AUDIO_NAME} — {speaker.name}",
            silence_hint=OTHER_SILENCE_HINT,
        )
    if not is_supported():
        found = ".".join(map(str, macos_version())) or platform.system()
        raise AudioError(
            f"system audio capture needs macOS {MIN_MACOS[0]}.{MIN_MACOS[1]}+ (this is {found})"
        )
    return SystemAudioSource(command=(str(build_helper(bin_dir)),))


class Decimator:
    """Streaming low-pass + downsample by an integer factor.

    Averaging blocks (a boxcar) lets everything above the new Nyquist frequency fold back
    into the speech band, which measurably hurts recognition of digits and consonants. A
    windowed-sinc FIR removes it first. State is kept between chunks so the output is
    identical however the stream is cut up.
    """

    TAPS = 97

    def __init__(self, factor: int) -> None:
        self.factor = factor
        cutoff = 0.45 / factor  # a little below the new Nyquist (fraction of the input rate)
        n = np.arange(self.TAPS) - (self.TAPS - 1) / 2
        taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hanning(self.TAPS)
        self._taps = (taps / taps.sum()).astype(np.float64)  # unity gain at DC
        self._history: np.ndarray | None = None
        self._phase = 0

    def process(self, samples: np.ndarray) -> np.ndarray:
        if self.factor == 1 or len(samples) == 0:
            return samples
        x = samples.astype(np.float64)
        if self._history is None:  # start from the first sample, not from silence
            self._history = np.full(self.TAPS - 1, x[0])
        buffer = np.concatenate([self._history, x])
        self._history = buffer[-(self.TAPS - 1) :]
        filtered = np.convolve(buffer, self._taps, mode="valid")
        picks = np.arange(self._phase, len(filtered), self.factor)
        next_pick = picks[-1] + self.factor if len(picks) else self._phase
        self._phase = next_pick - len(filtered)
        return np.clip(np.rint(filtered[picks]), -32768, 32767).astype(np.int16)


class SystemAudioRecorder:
    """Records the helper's PCM stream to a mono 16-bit WAV, like ``TrackRecorder``."""

    def __init__(
        self,
        role: str,
        source: SystemAudioSource,
        path: Path,
        sample_rate: int = TARGET_RATE,
        startup_timeout: float = 15.0,
    ) -> None:
        self.role = role
        self.device = source
        self.path = path
        self.sample_rate = sample_rate
        self.startup_timeout = startup_timeout
        self.level = 0.0
        self.peak = 0.0
        self.overflows = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115 - lives as long as the helper
        self._wav: wave.Wave_write | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._native_rate = 0
        self._factor = 1
        self._frames = 0
        self._error: str | None = None
        self._stopping = False

    def start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                list(self.device.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
            )
        except OSError as exc:
            raise AudioError(f"cannot start the system audio helper: {exc}") from exc

        self._thread = threading.Thread(
            target=self._read_loop, name="vecho-tap-reader", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(self.startup_timeout) or self._native_rate == 0:
            detail = self._stderr_text() or "it did not start in time"
            self._kill()
            raise AudioError(f"system audio capture failed: {detail}")

    def _stderr_text(self) -> str:
        self._stderr.seek(0)
        return self._stderr.read().decode("utf-8", "replace").strip().removeprefix("error: ")

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            header = proc.stdout.readline().decode("ascii", "replace").split()
            if len(header) == 2 and header[0] == "RATE" and header[1].isdigit():
                self._native_rate = int(header[1])
        finally:
            self._ready.set()
        if not self._native_rate:
            return

        rate = self._native_rate
        self._factor = rate // self.sample_rate if rate % self.sample_rate == 0 else 1
        self.sample_rate = rate // self._factor
        # Held open for the whole recording; closed in stop().
        self._wav = wave.open(str(self.path), "wb")  # noqa: SIM115
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self.sample_rate)

        decimator = Decimator(self._factor)
        carry = b""
        while True:
            # read1 keeps using the buffered reader that already consumed the header line
            chunk = proc.stdout.read1(_READ_SIZE)
            if not chunk:
                break
            data = carry + chunk
            usable = len(data) - (len(data) % 2)
            carry = data[usable:]
            body = decimator.process(np.frombuffer(data[:usable], dtype="<i2"))
            if len(body):
                self._consume(body)
        if not self._stopping:
            self._error = self._stderr_text() or "the helper exited unexpectedly"

    def _consume(self, samples: np.ndarray) -> None:
        level = int(np.abs(samples.astype(np.int32)).max()) / 32768.0
        self.level = level
        self.peak = max(self.peak, level)
        assert self._wav is not None
        try:
            self._wav.writeframes(samples.tobytes())
            self._frames += len(samples)
        except OSError as exc:  # e.g. disk full
            self._error = f"writing {self.path.name} failed: {exc}"

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if proc.stdin:
            proc.stdin.close()

    def stop(self) -> TrackStats:
        self._stopping = True
        proc = self._proc
        if proc is not None:
            if proc.stdin:
                with contextlib.suppress(OSError):
                    proc.stdin.close()  # the helper stops when its stdin closes
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._proc = None
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        self._stderr.close()
        if self._error and self._frames == 0:
            raise AudioError(f"system audio capture failed: {self._error}")
        return TrackStats(
            role=self.role,
            path=self.path,
            sample_rate=self.sample_rate,
            frames=self._frames,
            peak=self.peak,
            overflows=self.overflows,
            error=self._error,
        )
