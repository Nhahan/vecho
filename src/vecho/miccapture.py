"""Microphone capture in a child process: ``python -m vecho.miccapture DEVICE RATE CHANNELS``.

PortAudio's macOS backend can deadlock while stopping a stream (a lock-order inversion between
the stop call and its own IsRunning listener; PortAudio issue #1174). The stuck thread keeps
holding CoreAudio locks, so every later attempt to open a microphone in that process hangs
too. Capturing in a child process confines the damage: a child that does not stop in time is
killed, and the operating system releases everything it held.

Protocol (child side):

* stdout: ``OK\\n`` once the stream is open, or ``ERROR <message>\\n`` and exit.
* stdin: ``start\\n`` starts capturing; end of input (the parent closing the pipe, or dying)
  stops it and ends the process.
* stdout, after ``start``: frames of ``<II`` (sample count, flags) followed by that many mono
  int16 samples. Flag bit 0 marks an input overflow.
"""

from __future__ import annotations

import contextlib
import os
import queue
import signal
import struct
import sys
import threading
from collections.abc import Callable
from typing import IO, Any

import numpy as np

HEADER = struct.Struct("<II")
OVERFLOW = 1
_ABORT_TIMEOUT = 1.0

StreamFactory = Callable[[int, int, int, Callable[..., None]], Any]


def _open_sounddevice(device: int, rate: int, channels: int, callback: Callable[..., None]) -> Any:
    import sounddevice

    return sounddevice.InputStream(
        device=device, samplerate=rate, channels=channels, dtype="int16", callback=callback
    )


def run(
    device: int,
    rate: int,
    channels: int,
    stdin: IO[bytes],
    stdout: IO[bytes],
    open_stream: StreamFactory = _open_sounddevice,
) -> int:
    """Capture until stdin ends. Returns the exit status."""
    frames: queue.Queue[bytes | None] = queue.Queue()

    def on_audio(indata: Any, count: int, time_info: Any, status: Any) -> None:
        block = np.asarray(indata)
        if block.ndim == 2 and block.shape[1] > 1:
            mono = block.astype(np.int32).mean(axis=1).astype(np.int16)
        else:
            mono = block.reshape(-1).astype(np.int16)
        flags = OVERFLOW if getattr(status, "input_overflow", False) else 0
        frames.put(HEADER.pack(mono.size, flags) + mono.tobytes())

    try:
        stream = open_stream(device, rate, channels, on_audio)
    except Exception as exc:
        message = " ".join(str(exc).split()) or type(exc).__name__
        stdout.write(f"ERROR {message}\n".encode())
        stdout.flush()
        return 1
    stdout.write(b"OK\n")
    stdout.flush()

    if stdin.readline().strip() != b"start":
        return 0  # the parent gave up before starting

    def write_loop() -> None:
        while (chunk := frames.get()) is not None:
            try:
                stdout.write(chunk)
                stdout.flush()
            except OSError:  # the parent is gone
                return

    writer = threading.Thread(target=write_loop, daemon=True)
    writer.start()
    try:
        stream.start()
    except Exception:
        return 1
    stdin.read()  # until the parent closes the pipe (or dies)

    # Stopping may deadlock in PortAudio; the process exits either way.
    stopper = threading.Thread(target=lambda: _shut(stream), daemon=True)
    stopper.start()
    stopper.join(_ABORT_TIMEOUT)
    frames.put(None)
    writer.join(_ABORT_TIMEOUT)
    return 0


def _shut(stream: Any) -> None:
    for name in ("abort", "close"):
        with contextlib.suppress(Exception):
            getattr(stream, name)()


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is the parent's to handle
    device, rate, channels = (int(arg) for arg in sys.argv[1:4])
    status = run(device, rate, channels, sys.stdin.buffer, sys.stdout.buffer)
    sys.stdout.flush()
    os._exit(status)  # skip interpreter teardown: PortAudio may still be stuck


if __name__ == "__main__":
    main()
