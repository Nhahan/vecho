import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from vecho import audio
from vecho.audio import (
    InputDevice,
    Recorder,
    TrackRecorder,
    resolve_device,
)
from vecho.errors import AudioError

MIC = InputDevice(0, "MacBook Pro Microphone", 1, 48000.0, is_default=True)
LINE_IN = InputDevice(3, "USB Audio 2ch", 2, 48000.0)
USB = InputDevice(5, "USB Mic", 1, 44100.0)
USB2 = InputDevice(6, "USB Mic Pro", 1, 44100.0)
DEVICES = [MIC, LINE_IN, USB, USB2]


class FakeStream:
    """Stands in for sounddevice.InputStream and lets tests push audio blocks."""

    def __init__(self, samplerate, channels, callback):
        self.samplerate = samplerate
        self.channels = channels
        self.callback = callback
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def abort(self):

        self.stop()

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def push(self, block, status=None):
        self.callback(block, len(block), None, status)


def make_factory(streams, refuse_rates=()):
    def factory(device, samplerate, channels, callback):
        if samplerate in refuse_rates:
            raise OSError(f"invalid sample rate {samplerate}")
        stream = FakeStream(samplerate, channels, callback)
        streams.append(stream)
        return stream

    return factory


def read_wav(path):
    with wave.open(str(path), "rb") as wav:
        params = wav.getparams()
        data = np.frombuffer(wav.readframes(params.nframes), dtype=np.int16)
    return params, data


def test_resolve_default_index_and_name():
    assert resolve_device(None, DEVICES) is MIC
    assert resolve_device("3", DEVICES) is LINE_IN
    assert resolve_device("usb audio", DEVICES) is LINE_IN
    assert resolve_device("USB Mic", DEVICES) is USB  # exact name beats substring


def test_resolve_falls_back_to_first_when_no_default():
    assert resolve_device(None, [LINE_IN, USB]) is LINE_IN


@pytest.mark.parametrize("spec", ["99", "nothing-like-this", "usb"])
def test_resolve_errors(spec):
    with pytest.raises(AudioError):
        resolve_device(spec, DEVICES)


def test_resolve_without_devices():
    with pytest.raises(AudioError, match="no audio input"):
        resolve_device(None, [])


def test_track_recorder_writes_mono_wav(tmp_path):
    streams = []
    path = tmp_path / "me.wav"
    track = TrackRecorder("me", MIC, path, 16000, make_factory(streams))
    track.start()
    stream = streams[0]
    assert stream.started and stream.samplerate == 16000 and stream.channels == 1
    stream.push(np.full((1600, 1), 8192, dtype=np.int16))
    stream.push(np.full((800, 1), -16384, dtype=np.int16))
    stats = track.stop()

    params, data = read_wav(path)
    assert (params.nchannels, params.sampwidth, params.framerate) == (1, 2, 16000)
    assert len(data) == 2400 == stats.frames
    assert stats.duration == pytest.approx(0.15)
    assert stats.peak == pytest.approx(0.5)
    assert not stats.silent
    assert stream.stopped and stream.closed


def test_stereo_input_is_downmixed_to_mono(tmp_path):
    streams = []
    path = tmp_path / "remote.wav"
    track = TrackRecorder("remote", LINE_IN, path, 16000, make_factory(streams))
    track.start()
    assert streams[0].channels == 2
    left_right = np.column_stack([np.full(100, 1000), np.full(100, 3000)]).astype(np.int16)
    streams[0].push(left_right)
    track.stop()
    _, data = read_wav(path)
    assert set(data.tolist()) == {2000}


def test_falls_back_to_native_sample_rate(tmp_path):
    streams = []
    track = TrackRecorder(
        "me", MIC, tmp_path / "me.wav", 16000, make_factory(streams, refuse_rates={16000})
    )
    track.start()
    streams[0].push(np.zeros((480, 1), dtype=np.int16))
    stats = track.stop()
    assert stats.sample_rate == 48000
    params, _ = read_wav(tmp_path / "me.wav")
    assert params.framerate == 48000


def test_start_fails_when_no_rate_works(tmp_path):
    track = TrackRecorder(
        "me", MIC, tmp_path / "me.wav", 16000, make_factory([], refuse_rates={16000, 48000})
    )
    with pytest.raises(AudioError, match="cannot open"):
        track.start()


def test_silent_track_is_flagged_and_overflow_counted(tmp_path):
    streams = []
    track = TrackRecorder("me", MIC, tmp_path / "me.wav", 16000, make_factory(streams))
    track.start()
    streams[0].push(np.zeros((160, 1), dtype=np.int16), SimpleNamespace(input_overflow=True))
    stats = track.stop()
    assert stats.silent
    assert stats.overflows == 1


def test_recorder_runs_tracks_together_and_reports_levels(tmp_path):
    streams = []
    factory = make_factory(streams)
    recorder = Recorder(
        [
            TrackRecorder("me", MIC, tmp_path / "me.wav", 16000, factory),
            TrackRecorder("remote", LINE_IN, tmp_path / "remote.wav", 16000, factory),
        ]
    )
    recorder.start()
    streams[0].push(np.full((160, 1), 16384, dtype=np.int16))
    assert recorder.levels() == {"me": pytest.approx(0.5), "remote": 0.0}
    assert recorder.elapsed >= 0
    stats = recorder.stop()
    assert [s.role for s in stats] == ["me", "remote"]


def test_recorder_start_failure_releases_started_tracks(tmp_path):
    streams = []
    good = TrackRecorder("me", MIC, tmp_path / "me.wav", 16000, make_factory(streams))
    bad = TrackRecorder(
        "remote", LINE_IN, tmp_path / "remote.wav", 16000, make_factory([], {16000, 48000})
    )
    with pytest.raises(AudioError):
        Recorder([good, bad]).start()
    assert streams[0].stopped and streams[0].closed


def test_disk_write_failure_is_reported_on_stop(tmp_path):
    streams = []
    track = TrackRecorder("me", MIC, tmp_path / "me.wav", 16000, make_factory(streams))
    track.start()

    class BrokenWav:
        def writeframes(self, _):
            raise OSError("disk full")

        def close(self):
            pass

    track._wav = BrokenWav()
    streams[0].push(np.ones((160, 1), dtype=np.int16))
    stats = track.stop()  # reported, not raised: the other tracks must still be stopped
    assert "disk full" in stats.error


def test_a_stream_that_never_stops_cannot_hang_the_recording(tmp_path, monkeypatch):
    import threading
    import time

    from vecho import audio

    monkeypatch.setattr(audio, "STREAM_CLOSE_TIMEOUT", 0.3)
    forever = threading.Event()

    class StuckStream(FakeStream):
        def abort(self):
            forever.wait()  # like Pa_StopStream waiting for a callback that never comes

    def factory(device, samplerate, channels, callback):
        return StuckStream(samplerate, channels, callback)

    track = TrackRecorder("me", MIC, tmp_path / "me.wav", 16000, factory)
    track.start()
    track._stream.push(np.full((1600, 1), 1000, dtype=np.int16))
    started = time.monotonic()
    stats = track.stop()
    assert time.monotonic() - started < 2
    assert stats.frames == 1600  # what was recorded is saved
    track._on_audio(np.ones((160, 1), dtype=np.int16), 160, None, None)  # late callback: ignored
    forever.set()


def test_audio_that_stops_arriving_is_reported(tmp_path, monkeypatch):
    """A device unplugged mid-recording: the track is cut short, not silent."""
    monkeypatch.setattr(audio, "GAP_REPORT_SEC", 0.2)

    class OneBlock:
        def __init__(self, callback):
            self.callback = callback

        def start(self):
            self.callback(np.full((1600, 1), 3000, dtype=np.int16), 1600, None, None)

        def abort(self):
            pass

        def close(self):
            pass

    device = audio.InputDevice(0, "Mic", 1, 16000.0)
    track = audio.TrackRecorder(
        "me", device, tmp_path / "me.wav", stream_factory=lambda d, r, c, cb: OneBlock(cb)
    )
    track.start()
    time.sleep(0.6)
    stats = track.stop()
    assert stats.error and "disconnected" in stats.error
    assert track.level == 0.0


def test_audio_lost_in_the_middle_is_measured_against_the_clock(tmp_path):
    """A source that skips 1.5 s (the computer too busy to take it) is short by that much."""
    import threading

    class Skipping:
        def __init__(self, callback):
            self.callback, self.running = callback, True

        def start(self):
            def feed():
                for k in range(30):
                    if not self.running:
                        return
                    if k != 10:  # one 0.1 s block arrives; the rest of the stall is lost
                        self.callback(np.zeros((1600, 1), dtype=np.int16), 1600, None, None)
                    time.sleep(1.5 if k == 10 else 0.1)

            threading.Thread(target=feed, daemon=True).start()

        def abort(self):
            self.running = False

        def close(self):
            pass

    device = audio.InputDevice(0, "Mic", 1, 16000.0)
    track = audio.TrackRecorder(
        "me", device, tmp_path / "me.wav", stream_factory=lambda d, r, c, cb: Skipping(cb)
    )
    track.start()
    time.sleep(4.2)
    stats = track.stop()
    assert 1.2 < stats.lost < 2.0
    assert audio.TrackStats("me", tmp_path, 16000, 16000, 0.5, 0).lost == 0.0  # no timing known
