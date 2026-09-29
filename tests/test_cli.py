import wave
from datetime import datetime

import numpy as np
import pytest

from vecho import audio, cli, routing
from vecho.audio import InputDevice
from vecho.errors import SummarizationError, TranscriptionError
from vecho.session import SessionStore
from vecho.transcript import Segment, save_segments

MIC = InputDevice(0, "Built-in Mic", 1, 48000.0, is_default=True)
BLACKHOLE = InputDevice(2, "BlackHole 2ch", 2, 48000.0)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VECHO_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def store_for(home):
    return SessionStore(home / "sessions")


def write_wav(path, seconds=1.0, rate=16000, amplitude=1000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(np.full(int(rate * seconds), amplitude, dtype=np.int16).tobytes())


def stub_pipeline(monkeypatch, *, transcribe=None, summarize=None):
    calls = []

    def fake_transcribe(session, config, **kwargs):
        calls.append("transcribe")
        if transcribe:
            raise transcribe
        save_segments(session.path_for("transcript.json"), [Segment(0, 1, "me", "hi")], "ko", "t")
        session.path_for("transcript.md").write_text("# transcript", encoding="utf-8")
        return []

    def fake_summarize(session, config, **kwargs):
        calls.append("summarize")
        if summarize:
            raise summarize
        session.path_for("summary.md").write_text("# SUMMARY", encoding="utf-8")
        return "# SUMMARY"

    monkeypatch.setattr(cli, "transcribe_session", fake_transcribe)
    monkeypatch.setattr(cli, "summarize_session", fake_summarize)
    return calls


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert "vecho" in capsys.readouterr().out


def test_list_when_empty(capsys):
    assert cli.main(["list"]) == 0
    assert "No sessions yet" in capsys.readouterr().out


def test_list_shows_state_and_title(isolated_home, capsys):
    store = store_for(isolated_home)
    store.create("plain", now=datetime(2026, 1, 1, 9, 0, 0))
    done = store.create("done", now=datetime(2026, 1, 2, 9, 0, 0))
    done.path_for("summary.md").write_text("s", encoding="utf-8")
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "summarized" in out and "recorded" in out and "done" in out


def test_show_summary_transcript_and_path(isolated_home, capsys):
    session = store_for(isolated_home).create("talk")
    session.path_for("summary.md").write_text("SUMMARY TEXT", encoding="utf-8")
    session.path_for("transcript.md").write_text("TRANSCRIPT TEXT", encoding="utf-8")

    assert cli.main(["show"]) == 0
    assert capsys.readouterr().out == "SUMMARY TEXT"
    assert cli.main(["show", "--transcript"]) == 0
    assert capsys.readouterr().out == "TRANSCRIPT TEXT"
    assert cli.main(["show", "--path"]) == 0
    assert capsys.readouterr().out.strip() == str(session.dir)


def test_show_missing_summary_is_an_error(isolated_home, capsys):
    store_for(isolated_home).create("talk")
    assert cli.main(["show"]) == 1
    assert "vecho summarize" in capsys.readouterr().err


def test_errors_exit_with_code_one_and_message(capsys):
    assert cli.main(["show", "nope"]) == 1
    assert capsys.readouterr().err.startswith("error:")


def test_import_creates_session_without_processing(tmp_path, isolated_home, capsys):
    me, remote = tmp_path / "Mine.WAV", tmp_path / "theirs.wav"
    write_wav(me)
    write_wav(remote)
    args = ["import", "--me", str(me), "--remote", str(remote), "-t", "Call", "--no-process"]
    code = cli.main(args)
    assert code == 0
    (session,) = store_for(isolated_home).list()
    assert session.meta.title == "Call"
    assert session.meta.tracks == {"me": "me.wav", "remote": "remote.wav"}
    assert session.audio_path("me").read_bytes() == me.read_bytes()
    assert "vecho transcribe" in capsys.readouterr().err


def test_import_defaults_title_to_file_name(tmp_path, isolated_home):
    mixed = tmp_path / "standup.m4a"
    mixed.write_bytes(b"fake")
    assert cli.main(["import", "--mixed", str(mixed), "--no-process"]) == 0
    (session,) = store_for(isolated_home).list()
    assert session.meta.title == "standup"
    assert session.meta.tracks == {"mixed": "mixed.m4a"}


def test_import_validates_input(tmp_path, isolated_home, capsys):
    assert cli.main(["import"]) == 1
    assert "at least one" in capsys.readouterr().err
    assert cli.main(["import", "--me", str(tmp_path / "ghost.wav")]) == 1
    assert "not found" in capsys.readouterr().err
    assert store_for(isolated_home).list() == []  # no half-made session


def test_import_runs_full_pipeline_and_prints_summary(tmp_path, monkeypatch, capsys):
    calls = stub_pipeline(monkeypatch)
    me = tmp_path / "me.wav"
    write_wav(me)
    assert cli.main(["import", "--me", str(me)]) == 0
    assert calls == ["transcribe", "summarize"]
    assert "# SUMMARY" in capsys.readouterr().out


def test_transcribe_failure_keeps_audio_and_suggests_retry(tmp_path, monkeypatch, capsys):
    calls = stub_pipeline(monkeypatch, transcribe=TranscriptionError("model download failed"))
    me = tmp_path / "me.wav"
    write_wav(me)
    assert cli.main(["import", "--me", str(me)]) == 1
    err = capsys.readouterr().err
    assert "model download failed" in err and "vecho transcribe" in err
    assert calls == ["transcribe"]  # summary is not attempted


def test_summarize_failure_keeps_transcript_and_suggests_retry(tmp_path, monkeypatch, capsys):
    stub_pipeline(monkeypatch, summarize=SummarizationError("cannot reach Ollama"))
    me = tmp_path / "me.wav"
    write_wav(me)
    assert cli.main(["import", "--me", str(me)]) == 1
    err = capsys.readouterr().err
    assert "cannot reach Ollama" in err and "vecho summarize" in err


def test_transcribe_and_summarize_commands_target_latest(isolated_home, monkeypatch, capsys):
    calls = stub_pipeline(monkeypatch)
    session = store_for(isolated_home).create("x")
    session.meta.tracks = {"me": "me.wav"}
    session.save()
    assert cli.main(["transcribe", "--model", "tiny", "--language", "ko"]) == 0
    assert cli.main(["summarize", "--llm-model", "other"]) == 0
    assert calls == ["transcribe", "summarize"]
    assert "# SUMMARY" in capsys.readouterr().out


def test_devices_lists_tags(monkeypatch, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC, BLACKHOLE])
    assert cli.main(["devices"]) == 0
    out = capsys.readouterr().out
    assert "Built-in Mic" in out and "default" in out
    assert "BlackHole 2ch" in out and "loopback" in out


# ---- record -------------------------------------------------------------------------


class Rig:
    """Fake audio backend: hands out streams and lets a test speak into them."""

    def __init__(self):
        self.streams = {}

    def factory(self, device, samplerate, channels, callback):
        rig = self

        class Stream:
            closed = False

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                self.closed = True

        stream = Stream()
        stream.callback = callback
        stream.channels = channels
        rig.streams[device] = stream
        return stream

    def speak(self, device, amplitude, frames=16000):
        stream = self.streams[device]
        block = np.full((frames, stream.channels), amplitude, dtype=np.int16)
        stream.callback(block, frames, None, None)


@pytest.fixture
def rig(monkeypatch):
    rig = Rig()
    monkeypatch.setattr(audio, "_default_stream_factory", rig.factory)
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC, BLACKHOLE])
    return rig


def test_record_saves_both_tracks(rig, isolated_home, monkeypatch, capsys):
    def speak_then_stop(recorder, config):
        rig.speak(0, 4000)
        rig.speak(2, 2000)

    monkeypatch.setattr(cli, "_wait_for_stop", speak_then_stop)
    calls = stub_pipeline(monkeypatch)
    assert cli.main(["record", "-t", "sync"]) == 0

    (session,) = store_for(isolated_home).list()
    assert session.meta.tracks == {"me": "me.wav", "remote": "remote.wav"}
    assert session.meta.duration_sec == pytest.approx(1.0)
    with wave.open(str(session.audio_path("remote"))) as wav:
        assert wav.getnframes() == 16000
    assert calls == ["transcribe", "summarize"]


def test_record_warns_about_silent_track(rig, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_wait_for_stop", lambda recorder, config: rig.speak(0, 4000))
    assert cli.main(["record", "--no-process"]) == 0
    err = capsys.readouterr().err
    assert "상대방 track is silent" in err and "BlackHole 2ch" in err


def test_record_mic_only_needs_no_loopback(rig, isolated_home, monkeypatch):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    monkeypatch.setattr(cli, "_wait_for_stop", lambda recorder, config: rig.speak(0, 4000))
    assert cli.main(["record", "--mic-only", "--no-process"]) == 0
    (session,) = store_for(isolated_home).list()
    assert list(session.meta.tracks) == ["me"]


def test_record_without_loopback_explains_setup(rig, isolated_home, monkeypatch, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    assert cli.main(["record"]) == 1
    err = capsys.readouterr().err
    assert "blackhole-2ch" in err and "--mic-only" in err
    assert store_for(isolated_home).list() == []


def test_record_rejects_same_device_for_both_sides(rig, isolated_home, capsys):
    assert cli.main(["record", "--mic", "0", "--remote", "0"]) == 1
    assert "different devices" in capsys.readouterr().err
    assert store_for(isolated_home).list() == []


def test_record_cleans_up_when_device_cannot_open(rig, isolated_home, monkeypatch, capsys):
    def refuse(device, samplerate, channels, callback):
        raise OSError("device busy")

    monkeypatch.setattr(audio, "_default_stream_factory", refuse)
    assert cli.main(["record"]) == 1
    assert "device busy" in capsys.readouterr().err
    assert store_for(isolated_home).list() == []


def test_interrupt_before_stop_handler_still_finalizes_audio(
    rig, isolated_home, monkeypatch, capsys
):
    def interrupted(recorder, config):
        rig.speak(0, 4000)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_wait_for_stop", interrupted)
    assert cli.main(["record", "--mic-only", "--no-process"]) == 130

    (session,) = store_for(isolated_home).list()
    assert rig.streams[0].closed
    with wave.open(str(session.audio_path("me"))) as wav:  # header was finalized on close
        assert wav.getnframes() == 16000


def test_very_short_recording_skips_processing(rig, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_wait_for_stop", lambda r, c: rig.speak(0, 4000, frames=100))
    calls = stub_pipeline(monkeypatch)
    assert cli.main(["record", "--mic-only"]) == 0
    assert calls == []
    assert "too short" in capsys.readouterr().err


def test_meter_scales_with_level():
    assert cli._meter(0.0) == "░" * 8
    assert cli._meter(1.0) == "█" * 8
    assert 0 < cli._meter(0.02).count("█") < 8


# ---- output routing & setup -----------------------------------------------------------


def test_record_follows_the_current_output_and_restores(rig, switcher, multi, monkeypatch):
    switcher._outputs.append("AirPods")
    switcher._current = "AirPods"  # not the speakers: whatever the user listens on
    seen = {}

    def speak(recorder, config):
        seen["during"] = switcher.current()
        rig.speak(0, 4000)
        rig.speak(2, 2000)

    monkeypatch.setattr(cli, "_wait_for_stop", speak)
    stub_pipeline(monkeypatch)
    assert cli.main(["record"]) == 0
    assert seen["during"] == routing.MULTI_OUTPUT_NAME
    assert multi.created == ["AirPods"]
    assert switcher.current() == "AirPods"  # back to normal before processing starts


def test_record_restores_output_even_when_interrupted(rig, switcher, monkeypatch):
    def interrupted(recorder, config):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_wait_for_stop", interrupted)
    assert cli.main(["record", "--no-process"]) == 130
    assert switcher.current() == "Speakers"


@pytest.mark.parametrize("flags", [["--no-routing"], ["--mic-only"]])
def test_record_can_skip_routing(rig, switcher, multi, monkeypatch, flags):
    monkeypatch.setattr(cli, "_wait_for_stop", lambda r, c: rig.speak(0, 4000))
    assert cli.main(["record", "--no-process", *flags]) == 0
    assert switcher.calls == [] and multi.created == []


def test_record_warns_but_records_when_routing_is_impossible(
    rig, switcher, multi, monkeypatch, capsys
):
    multi.fail = True
    monkeypatch.setattr(cli, "_wait_for_stop", lambda r, c: rig.speak(0, 4000))
    assert cli.main(["record", "--no-process"]) == 0
    err = capsys.readouterr().err
    assert "could not capture audio played through 'Speakers'" in err
    assert "Saved" in err


def test_setup_builds_the_device_for_the_chosen_output(monkeypatch, multi, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC, BLACKHOLE])
    assert cli.main(["setup", "--output", "Speakers"]) == 0
    out = capsys.readouterr().out
    assert multi.created == ["Speakers"] and "Ready" in out


def test_setup_is_idempotent_unless_forced(monkeypatch, switcher, multi, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC, BLACKHOLE])
    switcher._outputs.append(routing.MULTI_OUTPUT_NAME)
    assert cli.main(["setup"]) == 0
    assert "already exists" in capsys.readouterr().out and multi.created == []
    assert cli.main(["setup", "--force"]) == 0
    assert len(multi.created) == 1


def test_setup_needs_blackhole_first(monkeypatch, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    assert cli.main(["setup"]) == 1
    assert "blackhole-2ch" in capsys.readouterr().err


def test_setup_needs_the_switch_tool(monkeypatch, switcher, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC, BLACKHOLE])
    switcher.available = False
    assert cli.main(["setup"]) == 1
    assert "switchaudio-osx" in capsys.readouterr().err


def test_setup_remove(monkeypatch, switcher, multi, capsys):
    switcher._outputs.append(routing.MULTI_OUTPUT_NAME)
    assert cli.main(["setup", "--remove"]) == 0
    assert multi.removed and "Removed" in capsys.readouterr().out


def test_doctor_reports_the_output_and_app_pinning_caveat(monkeypatch, switcher, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC, BLACKHOLE])
    monkeypatch.setattr(cli.OllamaClient, "has_model", lambda self: True)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "sound output: Speakers" in out
    assert "Discord" in out and "Default" in out


def test_doctor_flags_a_virtual_sound_output(monkeypatch, switcher, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC, BLACKHOLE])
    monkeypatch.setattr(cli.OllamaClient, "has_model", lambda self: True)
    switcher._outputs.append("BlackHole 2ch")
    switcher._current = "BlackHole 2ch"
    cli.main(["doctor"])
    assert "virtual" in capsys.readouterr().out
