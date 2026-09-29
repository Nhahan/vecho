import wave
from types import SimpleNamespace

import numpy as np
import pytest

from vecho.audio import (
    InputDevice,
    Recorder,
    TrackRecorder,
    find_loopback_device,
    resolve_device,
)
from vecho.errors import AudioError

MIC = InputDevice(0, "MacBook Pro Microphone", 1, 48000.0, is_default=True)
BLACKHOLE = InputDevice(3, "BlackHole 2ch", 2, 48000.0)
USB = InputDevice(5, "USB Mic", 1, 44100.0)
USB2 = InputDevice(6, "USB Mic Pro", 1, 44100.0)
DEVICES = [MIC, BLACKHOLE, USB, USB2]


class FakeStream:
    """Stands in for sounddevice.InputStream and lets tests push audio blocks."""

    def __init__(self, samplerate, channels, callback):
        self.samplerate = samplerate
        self.channels = channels
        self.callback = callback
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

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


def test_loopback_detection():
    assert BLACKHOLE.is_loopback and not MIC.is_loopback
    assert find_loopback_device(DEVICES) is BLACKHOLE
    assert find_loopback_device([MIC, USB]) is None


def test_resolve_default_index_and_name():
    assert resolve_device(None, DEVICES) is MIC
    assert resolve_device("3", DEVICES) is BLACKHOLE
    assert resolve_device("blackhole", DEVICES) is BLACKHOLE
    assert resolve_device("USB Mic", DEVICES) is USB  # exact name beats substring


def test_resolve_falls_back_to_first_when_no_default():
    assert resolve_device(None, [BLACKHOLE, USB]) is BLACKHOLE


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
    track = TrackRecorder("remote", BLACKHOLE, path, 16000, make_factory(streams))
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
            TrackRecorder("remote", BLACKHOLE, tmp_path / "remote.wav", 16000, factory),
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
        "remote", BLACKHOLE, tmp_path / "remote.wav", 16000, make_factory([], {16000, 48000})
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
    with pytest.raises(AudioError, match="disk full"):
        track.stop()
