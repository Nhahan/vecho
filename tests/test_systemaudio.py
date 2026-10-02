import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from vecho import systemaudio
from vecho.errors import AudioError
from vecho.systemaudio import SystemAudioRecorder, SystemAudioSource

FAKE = str(Path(__file__).with_name("fake_tap.py"))


def source(rate=48000, mode="loud"):
    return SystemAudioSource(command=(sys.executable, FAKE, str(rate), mode))


def record(tmp_path, rate=48000, mode="loud", **kwargs):
    recorder = SystemAudioRecorder("remote", source(rate, mode), tmp_path / "remote.wav", **kwargs)
    recorder.start()
    return recorder


def wait_for_frames(recorder, minimum, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while recorder._frames < minimum and time.monotonic() < deadline:
        time.sleep(0.01)


def read_wav(path):
    with wave.open(str(path), "rb") as wav:
        params = wav.getparams()
        return params, np.frombuffer(wav.readframes(params.nframes), dtype=np.int16)


def test_captures_and_downsamples_to_16khz(tmp_path):
    recorder = record(tmp_path)
    wait_for_frames(recorder, 8000)
    stats = recorder.stop()
    params, data = read_wav(tmp_path / "remote.wav")
    assert (params.nchannels, params.sampwidth, params.framerate) == (1, 2, 16000)
    assert stats.sample_rate == 16000 and stats.frames == len(data) == 8000  # 0.5 s at 16 kHz
    assert set(data.tolist()) == {16384}
    assert stats.peak == pytest.approx(0.5) and not stats.silent and stats.error is None


def test_rates_that_do_not_divide_evenly_are_kept_as_they_are(tmp_path):
    recorder = record(tmp_path, rate=44100)
    wait_for_frames(recorder, 22050)
    stats = recorder.stop()
    assert stats.sample_rate == 44100 and stats.frames == 22050


def test_samples_split_across_reads_are_reassembled(tmp_path):
    recorder = record(tmp_path, mode="split")
    wait_for_frames(recorder, 8000)
    stats = recorder.stop()
    _, data = read_wav(tmp_path / "remote.wav")
    assert stats.frames == 8000 and set(data.tolist()) == {16384}  # no corrupted samples


def test_silence_is_flagged(tmp_path):
    recorder = record(tmp_path, mode="silent")
    wait_for_frames(recorder, 8000)
    assert recorder.stop().silent


def test_level_is_available_while_recording(tmp_path):
    recorder = record(tmp_path)
    wait_for_frames(recorder, 8000)
    assert recorder.level == pytest.approx(0.5)
    recorder.stop()


def test_startup_failure_reports_the_helpers_message(tmp_path):
    recorder = SystemAudioRecorder("remote", source(mode="fail"), tmp_path / "r.wav")
    with pytest.raises(AudioError, match="cannot create the audio tap"):
        recorder.start()


def test_helper_that_never_answers_times_out(tmp_path):
    recorder = SystemAudioRecorder(
        "remote", source(mode="hang"), tmp_path / "r.wav", startup_timeout=0.5
    )
    with pytest.raises(AudioError, match="did not start in time"):
        recorder.start()


def test_helper_that_cannot_be_executed(tmp_path):
    bad = SystemAudioSource(command=("/definitely/not/a/binary",))
    with pytest.raises(AudioError, match="cannot start"):
        SystemAudioRecorder("remote", bad, tmp_path / "r.wav").start()


def test_helper_dying_mid_recording_keeps_the_audio_and_reports_it(tmp_path):
    recorder = record(tmp_path, mode="crash")
    wait_for_frames(recorder, 4000)
    import time

    time.sleep(0.3)  # let the helper exit
    stats = recorder.stop()
    assert stats.frames == 4000
    assert "the audio device disappeared" in stats.error


# ---- availability and helper build -----------------------------------------------------


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("26.5.1", True),
        ("15.0", True),
        ("14.4", True),
        ("14.3.1", False),
        ("13.6", False),
        ("", False),
    ],
)
def test_macos_version_gate(monkeypatch, version, supported):
    monkeypatch.setattr(systemaudio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(systemaudio.platform, "mac_ver", lambda: (version, ("", "", ""), ""))
    assert systemaudio.is_supported() is supported


@pytest.mark.parametrize(
    ("system", "supported"), [("Linux", True), ("Windows", True), ("FreeBSD", False)]
)
def test_support_on_other_platforms(monkeypatch, system, supported):
    monkeypatch.setattr(systemaudio.platform, "system", lambda: system)
    assert systemaudio.is_supported() is supported


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows", "FreeBSD"])
def test_install_hint_exists_for_every_platform(monkeypatch, system):
    monkeypatch.setattr(systemaudio.platform, "system", lambda: system)
    monkeypatch.setattr(systemaudio.platform, "mac_ver", lambda: ("13.0", ("", "", ""), ""))
    assert systemaudio.install_hint()


def test_prepare_explains_old_macos(monkeypatch, tmp_path):
    monkeypatch.undo()  # drop the suite-wide stub so the real function runs
    monkeypatch.setattr(systemaudio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(systemaudio.platform, "mac_ver", lambda: ("13.6", ("", "", ""), ""))
    with pytest.raises(AudioError, match="needs macOS 14.4"):
        systemaudio.prepare(tmp_path)


def make_fake_swiftc(tmp_path, ok=True):
    """A `swiftc` that copies a tiny script to the -o path (or fails)."""
    script = tmp_path / "swiftc"
    body = (
        '#!/bin/sh\nwhile [ "$1" != "-o" ]; do shift; done\n'
        'printf "#!/bin/sh\\nexit 0\\n" > "$2"; chmod +x "$2"\n'
        if ok
        else '#!/bin/sh\necho "compile error: boom" >&2\nexit 1\n'
    )
    script.write_text(body)
    script.chmod(0o755)
    return script


def test_build_helper_compiles_once_and_caches(monkeypatch, tmp_path):
    swiftc = make_fake_swiftc(tmp_path)
    monkeypatch.setattr(systemaudio.shutil, "which", lambda name: str(swiftc))
    bin_dir = tmp_path / "bin"
    first = systemaudio.build_helper(bin_dir)
    assert first.is_file() and first.name.startswith("vecho-system-audio-")
    marker = first.stat().st_mtime_ns
    assert systemaudio.build_helper(bin_dir) == first
    assert first.stat().st_mtime_ns == marker  # not rebuilt


def test_build_helper_removes_binaries_of_older_sources(monkeypatch, tmp_path):
    swiftc = make_fake_swiftc(tmp_path)
    monkeypatch.setattr(systemaudio.shutil, "which", lambda name: str(swiftc))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "vecho-system-audio-oldhash").write_text("x")
    current = systemaudio.build_helper(bin_dir)
    assert [p.name for p in bin_dir.iterdir()] == [current.name]


def test_build_helper_reports_compile_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(systemaudio, "_prebuilt", lambda digest: None)  # a changed source
    swiftc = make_fake_swiftc(tmp_path, ok=False)
    monkeypatch.setattr(systemaudio.shutil, "which", lambda name: str(swiftc))
    with pytest.raises(AudioError, match="boom"):
        systemaudio.build_helper(tmp_path / "bin")
    assert list((tmp_path / "bin").glob("vecho-system-audio-*")) == []


def test_build_helper_needs_swiftc(monkeypatch, tmp_path):
    monkeypatch.setattr(systemaudio, "_prebuilt", lambda digest: None)
    monkeypatch.setattr(systemaudio.shutil, "which", lambda name: None)
    with pytest.raises(AudioError, match="xcode-select"):
        systemaudio.build_helper(tmp_path)


def test_swift_helper_ships_with_the_package():
    text = systemaudio._script_text()
    assert "AudioHardwareCreateProcessTap" in text and "RATE " in text


# ---- Decimator ---------------------------------------------------------------------------


def tone(frequency, seconds=1.0, rate=48000, amplitude=10000):
    t = np.arange(int(rate * seconds)) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.int16)


def rms(x):
    x = x.astype(np.float64)
    return float(np.sqrt(np.mean(x**2)))


def test_decimator_keeps_speech_frequencies_and_removes_what_would_alias():
    out_speech = systemaudio.Decimator(3).process(tone(1000))
    out_alias = systemaudio.Decimator(3).process(tone(12000))  # would fold to 4 kHz unfiltered
    assert len(out_speech) == 16000
    assert rms(out_speech[500:]) == pytest.approx(rms(tone(1000)), rel=0.03)
    assert rms(out_alias[500:]) < 0.02 * rms(tone(12000))


def test_decimator_output_does_not_depend_on_how_the_stream_is_cut():
    signal = tone(700) + tone(3100, amplitude=4000)
    whole = systemaudio.Decimator(3).process(signal)
    pieces = systemaudio.Decimator(3)
    chunks = [
        signal[i : i + 1001] for i in range(0, len(signal), 1001)
    ]  # 1001 is not a multiple of 3
    streamed = np.concatenate([pieces.process(chunk) for chunk in chunks])
    assert np.array_equal(whole, streamed)


def test_decimator_passes_through_when_no_conversion_is_needed():
    data = tone(500, rate=16000)
    assert np.array_equal(systemaudio.Decimator(1).process(data), data)


def test_decimator_holds_a_constant_level_without_a_startup_ramp():
    out = systemaudio.Decimator(3).process(np.full(4800, 16384, dtype=np.int16))
    assert set(out.tolist()) == {16384}


def test_two_threads_building_at_once_both_get_the_helper(monkeypatch, tmp_path):
    import threading

    script = tmp_path / "swiftc"
    script.write_text(
        '#!/bin/sh\nsleep 0.3\nwhile [ "$1" != "-o" ]; do shift; done\n'
        'printf "#!/bin/sh\\nexit 0\\n" > "$2"\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(systemaudio.shutil, "which", lambda name: str(script))
    results, errors = [], []

    def build():
        try:
            results.append(systemaudio.build_helper(tmp_path / "bin"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=build) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(set(results)) == 1
    import os

    assert os.access(results[0], os.X_OK)


def test_the_shipped_helper_matches_its_source():
    """Run scripts/build-helper.sh after changing system_audio.swift."""
    import hashlib
    from importlib import resources

    folder = resources.files("vecho").joinpath("resources")
    source = folder.joinpath("system_audio.swift").read_text("utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    assert folder.joinpath("bin", "system-audio.digest").read_text("utf-8").strip() == digest


def test_the_shipped_helper_is_used_without_a_compiler(tmp_path, monkeypatch):
    import os

    from vecho import systemaudio

    monkeypatch.setattr(systemaudio.shutil, "which", lambda name: None)  # no swiftc
    binary = systemaudio.build_helper(tmp_path)
    assert os.access(binary, os.X_OK) and binary.read_bytes()[:4] == b"\xca\xfe\xba\xbe"


def test_the_shipped_launcher_matches_its_source():
    import hashlib
    from importlib import resources

    folder = resources.files("vecho").joinpath("resources")
    source = folder.joinpath("launcher.swift").read_text("utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    assert folder.joinpath("bin", "launcher.digest").read_text("utf-8").strip() == digest
