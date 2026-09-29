import time
import wave

import numpy as np
import pytest

from fake_soundcard import FakeSoundcard, Speaker
from vecho import loopback, systemaudio
from vecho.errors import AudioError
from vecho.loopback import LoopbackRecorder
from vecho.systemaudio import LOOPBACK, SystemAudioSource

SOURCE = SystemAudioSource(backend=LOOPBACK)


def make(tmp_path, card, **kwargs):
    return LoopbackRecorder(
        "remote", SOURCE, tmp_path / "remote.wav", 16000, soundcard_module=card, **kwargs
    )


def frames_of(path):
    with wave.open(str(path)) as wav:
        return wav.getframerate(), np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)


def test_records_the_default_output_at_16khz(tmp_path):
    card = FakeSoundcard(level=0.5)
    recorder = make(tmp_path, card)
    recorder.start()
    time.sleep(0.2)
    stats = recorder.stop()
    rate, data = frames_of(tmp_path / "remote.wav")
    assert rate == 16000 and stats.frames == len(data) > 0
    assert np.all(np.abs(data - 16384) <= 1)
    assert stats.peak == pytest.approx(0.5, abs=0.01) and stats.error is None
    assert card.opened == ["Speakers"]


def test_start_failure_is_reported(tmp_path):
    card = FakeSoundcard()
    card.fail_open = "no permission"
    with pytest.raises(AudioError, match="no permission"):
        make(tmp_path, card).start()


def test_follows_a_change_of_default_output(tmp_path):
    card = FakeSoundcard()
    recorder = make(tmp_path, card, follow_sec=0.01)
    recorder.start()
    time.sleep(0.05)
    card.speaker = Speaker("Headset")
    time.sleep(0.2)
    recorder.stop()
    assert card.opened[0] == "Speakers" and "Headset" in card.opened


def test_recovers_from_a_lost_device_and_keeps_tracks_aligned(tmp_path, monkeypatch):
    monkeypatch.setattr(loopback, "RETRY_SEC", 0.3)
    card = FakeSoundcard()
    card.fail_after = 1
    recorder = make(tmp_path, card)
    started = time.monotonic()
    recorder.start()
    time.sleep(0.8)
    stats = recorder.stop()
    elapsed = time.monotonic() - started
    assert len(card.opened) >= 2 and stats.error is None
    # the silent gap was filled, so the track length follows the wall clock
    assert stats.duration == pytest.approx(elapsed, abs=0.35)


def test_prepare_uses_the_loopback_backend_off_macos(monkeypatch, tmp_path):
    monkeypatch.undo()  # the suite disables prepare(); use the real one here
    monkeypatch.setattr(systemaudio.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        loopback, "load_soundcard", lambda: FakeSoundcard(speaker="Realtek Speakers")
    )
    source = systemaudio.prepare(tmp_path)
    assert source.backend == LOOPBACK and "Realtek Speakers" in source.name


def test_prepare_without_an_output_device(monkeypatch, tmp_path):
    monkeypatch.undo()
    monkeypatch.setattr(systemaudio.platform, "system", lambda: "Linux")
    monkeypatch.setattr(loopback, "load_soundcard", lambda: FakeSoundcard(speaker=None))
    with pytest.raises(AudioError, match="no sound output"):
        systemaudio.prepare(tmp_path)


def test_record_track_dispatch():
    from vecho.recording import make_track

    track = make_track("remote", SOURCE, __import__("pathlib").Path("x.wav"), 16000)
    assert isinstance(track, LoopbackRecorder)
