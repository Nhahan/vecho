"""Microphone capture in a child process, which keeps a stuck PortAudio stop out of the app."""

from __future__ import annotations

import io
import sys
import textwrap
import threading
import time
import wave

import numpy as np
import pytest

from vecho import audio, miccapture
from vecho.audio import CaptureProcess, InputDevice, TrackRecorder
from vecho.errors import AudioError

# A stand-in for sounddevice, imported by the real `python -m vecho.miccapture` child.
# FAKE_MODE: "ok" streams a tone; "stuck" also never returns from abort() (PortAudio #1174).
FAKE_SOUNDDEVICE = """
import os, threading, time
import numpy as np

class InputStream:
    def __init__(self, device, samplerate, channels, dtype, callback):
        if device == 99:
            raise RuntimeError("Error opening InputStream: Invalid device [PaErrorCode -9996]")
        self.callback, self.channels, self.running = callback, channels, False

    def start(self):
        self.running = True
        def feed():
            while self.running:
                block = np.full((160, self.channels), 1000, dtype=np.int16)
                self.callback(block, 160, None, None)
                time.sleep(0.01)
        threading.Thread(target=feed, daemon=True).start()

    def abort(self):
        if os.environ.get("FAKE_MODE") == "stuck":
            threading.Event().wait()  # deadlocked, like PortAudio's stop on macOS
        self.running = False

    def close(self):
        pass
"""


@pytest.fixture
def fake_portaudio(tmp_path, monkeypatch):
    (tmp_path / "sounddevice.py").write_text(FAKE_SOUNDDEVICE)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("FAKE_MODE", "ok")
    return monkeypatch


def record(path, seconds=0.3):
    mic = InputDevice(0, "Mic", 2, 16000.0, is_default=True)
    track = TrackRecorder("me", mic, path)
    track.start()
    time.sleep(seconds)
    return track.stop()


def test_a_recording_is_captured_in_a_child_process(tmp_path, fake_portaudio):
    stats = record(tmp_path / "me.wav")
    assert stats.error is None and stats.frames > 0 and stats.peak > 0
    with wave.open(str(tmp_path / "me.wav")) as wav:
        assert wav.getnchannels() == 1 and wav.getnframes() == stats.frames


def test_a_stuck_stop_neither_hangs_nor_blocks_the_next_recording(tmp_path, fake_portaudio):
    fake_portaudio.setenv("FAKE_MODE", "stuck")
    started = time.monotonic()
    first = record(tmp_path / "a.wav")
    assert first.frames > 0
    assert time.monotonic() - started < 0.3 + audio.STOP_TIMEOUT + 1.5

    fake_portaudio.setenv("FAKE_MODE", "ok")
    assert record(tmp_path / "b.wav").frames > 0  # the stuck stream died with its process


def test_a_device_that_cannot_be_opened_is_reported(tmp_path, fake_portaudio):
    track = TrackRecorder("me", InputDevice(99, "Gone", 1, 16000.0), tmp_path / "me.wav")
    with pytest.raises(AudioError, match="Invalid device"):
        track.start()


def child(script):
    return [sys.executable, "-c", textwrap.dedent(script)]


def test_a_child_that_never_answers_is_killed():
    started = time.monotonic()
    with pytest.raises(AudioError, match="did not respond"):
        CaptureProcess(child("import time; time.sleep(60)"), lambda *a: None, open_timeout=0.5)
    assert time.monotonic() - started < 0.5 + audio.STOP_TIMEOUT + 1


def test_a_child_that_ignores_stop_is_killed():
    stream = CaptureProcess(
        child(
            """
            import sys, time
            print("OK", flush=True)
            sys.stdin.readline()
            time.sleep(60)
            """
        ),
        lambda *a: None,
    )
    stream.start()
    started = time.monotonic()
    stream.abort()
    assert time.monotonic() - started < audio.STOP_TIMEOUT + 1
    assert stream._process.poll() is not None


def test_the_child_protocol_round_trips_blocks_and_overflows():
    class Stream:
        def __init__(self, callback):
            self.callback = callback

        def start(self):
            self.callback(np.array([[100, 300], [-100, -300]], dtype=np.int16), 2, None, None)
            self.callback(np.zeros((1, 2), np.int16), 1, None, type("S", (), {"input_overflow": 1}))

        def abort(self):
            pass

        def close(self):
            pass

    out = io.BytesIO()
    status = miccapture.run(
        0, 16000, 2, io.BytesIO(b"start\n"), out, lambda d, r, c, callback: Stream(callback)
    )
    assert status == 0
    data = out.getvalue()
    assert data.startswith(b"OK\n")
    received = []
    reader = CaptureProcess.__new__(CaptureProcess)
    reader._callback = lambda block, count, _, s: received.append((block.ravel().tolist(), s))
    reader._process = type("P", (), {"stdout": io.BytesIO(data[3:])})()
    reader._read_loop()
    assert [(b, s.input_overflow) for b, s in received] == [([200, -200], False), ([0], True)]


def test_an_open_error_is_reported_on_one_line():
    out = io.BytesIO()

    def fail(*args):
        raise RuntimeError("Error opening\nInputStream")

    assert miccapture.run(0, 16000, 1, io.BytesIO(), out, fail) == 1
    assert out.getvalue() == b"ERROR Error opening InputStream\n"


def test_the_child_stops_when_the_parent_goes_away():
    """Closing stdin (what happens when the app dies) ends the capture."""
    done = threading.Event()

    class Stream:
        def __init__(self, callback):
            pass

        def start(self):
            pass

        def abort(self):
            done.set()

        def close(self):
            pass

    miccapture.run(
        0, 16000, 1, io.BytesIO(b"start\n"), io.BytesIO(), lambda d, r, c, cb: Stream(cb)
    )
    assert done.is_set()
