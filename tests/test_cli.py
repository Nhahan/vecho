import wave
from datetime import datetime

import numpy as np
import pytest

from vecho import audio, cli
from vecho.audio import InputDevice
from vecho.errors import SummarizationError, TranscriptionError
from vecho.session import SessionStore
from vecho.transcript import Segment, save_segments

MIC = InputDevice(0, "Built-in Mic", 1, 48000.0, is_default=True)
LINE_IN = InputDevice(2, "USB Audio 2ch", 2, 48000.0)


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
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC, LINE_IN])
    assert cli.main(["devices"]) == 0
    out = capsys.readouterr().out
    assert "Built-in Mic" in out and "default" in out
    assert "USB Audio 2ch" in out


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

            def abort(self):

                self.stop()

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
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC, LINE_IN])
    return rig


def test_record_saves_both_tracks(rig, isolated_home, monkeypatch, capsys):
    def speak_then_stop(recorder, config):
        rig.speak(0, 4000)
        rig.speak(2, 2000)

    monkeypatch.setattr(cli, "_wait_for_stop", speak_then_stop)
    calls = stub_pipeline(monkeypatch)
    assert cli.main(["record", "-t", "sync", "--remote", "2"]) == 0

    (session,) = store_for(isolated_home).list()
    assert session.meta.tracks == {"me": "me.wav", "remote": "remote.wav"}
    assert session.meta.duration_sec == pytest.approx(1.0)
    with wave.open(str(session.audio_path("remote"))) as wav:
        assert wav.getnframes() == 16000
    assert calls == ["transcribe", "summarize"]


def test_record_warns_about_silent_track(rig, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_wait_for_stop", lambda recorder, config: rig.speak(0, 4000))
    assert cli.main(["record", "--no-process", "--remote", "2"]) == 0
    err = capsys.readouterr().err
    assert "상대방 track is silent" in err and "USB Audio 2ch" in err


def test_record_mic_only_needs_no_second_source(rig, isolated_home, monkeypatch):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    monkeypatch.setattr(cli, "_wait_for_stop", lambda recorder, config: rig.speak(0, 4000))
    assert cli.main(["record", "--mic-only", "--no-process"]) == 0
    (session,) = store_for(isolated_home).list()
    assert list(session.meta.tracks) == ["me"]


def test_record_rejects_same_device_for_both_sides(rig, isolated_home, capsys):
    assert cli.main(["record", "--mic", "0", "--remote", "0"]) == 1
    assert "different devices" in capsys.readouterr().err
    assert store_for(isolated_home).list() == []


def test_record_cleans_up_when_device_cannot_open(rig, isolated_home, monkeypatch, capsys):
    def refuse(device, samplerate, channels, callback):
        raise OSError("device busy")

    monkeypatch.setattr(audio, "_default_stream_factory", refuse)
    assert cli.main(["record", "--remote", "2"]) == 1
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


# ---- driverless system audio ---------------------------------------------------------


def fake_source(mode="loud", rate=48000):
    import sys
    from pathlib import Path

    from vecho.systemaudio import SystemAudioSource

    script = str(Path(__file__).with_name("fake_tap.py"))
    return SystemAudioSource(command=(sys.executable, script, str(rate), mode))


def wait_for_remote_audio(recorder, config):
    import time

    time.sleep(0.3)  # let the fake helper deliver its half second of audio


def test_record_uses_system_audio_by_default_without_touching_the_output(
    rig, isolated_home, monkeypatch, capsys
):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    monkeypatch.setattr(cli.systemaudio, "prepare", lambda bin_dir: fake_source())

    def speak(recorder, config):
        rig.speak(0, 4000)
        wait_for_remote_audio(recorder, config)

    monkeypatch.setattr(cli, "_wait_for_stop", speak)
    calls = stub_pipeline(monkeypatch)
    assert cli.main(["record", "-t", "tap"]) == 0

    (session,) = store_for(isolated_home).list()
    assert session.meta.tracks == {"me": "me.wav", "remote": "remote.wav"}
    with wave.open(str(session.audio_path("remote"))) as wav:
        assert wav.getframerate() == 16000 and wav.getnframes() == 8000
    assert "System audio (all apps)" in capsys.readouterr().err
    assert calls == ["transcribe", "summarize"]


def test_system_audio_silence_points_to_the_permission(rig, monkeypatch, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    monkeypatch.setattr(cli.systemaudio, "prepare", lambda bin_dir: fake_source("silent"))

    def speak(recorder, config):
        rig.speak(0, 4000)
        wait_for_remote_audio(recorder, config)

    monkeypatch.setattr(cli, "_wait_for_stop", speak)
    assert cli.main(["record", "--no-process"]) == 0
    err = capsys.readouterr().err
    assert "상대방 track is silent" in err and "Screen & System Audio Recording" in err


def test_system_audio_helper_crash_is_reported_but_audio_is_kept(
    rig, isolated_home, monkeypatch, capsys
):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    monkeypatch.setattr(cli.systemaudio, "prepare", lambda bin_dir: fake_source("crash"))

    def speak(recorder, config):
        rig.speak(0, 4000)
        import time

        time.sleep(0.5)

    monkeypatch.setattr(cli, "_wait_for_stop", speak)
    assert cli.main(["record", "--no-process"]) == 0
    assert "stopped early: the audio device disappeared" in capsys.readouterr().err


def test_remote_system_reports_the_capture_error(rig, monkeypatch, capsys):
    assert cli.main(["record", "--remote", "system", "--no-process"]) == 1
    assert "disabled in tests" in capsys.readouterr().err


def test_record_without_system_audio_explains_the_options(rig, isolated_home, monkeypatch, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    assert cli.main(["record"]) == 1
    err = capsys.readouterr().err
    assert "disabled in tests" in err and "--mic-only" in err and "--remote" in err
    assert store_for(isolated_home).list() == []


def test_devices_lists_system_audio_first(monkeypatch, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    assert cli.main(["devices"]) == 0
    out = capsys.readouterr().out
    assert out.index("System audio") < out.index("Built-in Mic")


def test_doctor_prefers_the_builtin_capture(monkeypatch, capsys):
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    monkeypatch.setattr(cli.OllamaClient, "has_model", lambda self: True)
    monkeypatch.setattr(cli.systemaudio, "prepare", lambda bin_dir: fake_source())
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "system audio capture: built in" in out
    assert "BlackHole" not in out and "Multi-Output" not in out


# ---- templates -------------------------------------------------------------------------------


def test_templates_command_lifecycle(tmp_path, capsys):
    source = tmp_path / "mentoring.md"
    source.write_text("## 현황\n\n- **면접**\n\n## 숙제\n\n1.\n", encoding="utf-8")
    assert cli.main(["templates", "add", "멘토링", str(source)]) == 0
    assert "현황, 숙제" in capsys.readouterr().out
    assert cli.main(["templates", "default", "멘토링"]) == 0
    assert cli.main(["templates"]) == 0
    out = capsys.readouterr().out
    assert "* 멘토링" in out and "기본 요약" in out
    assert cli.main(["templates", "show", "멘토링"]) == 0
    assert "## 숙제" in capsys.readouterr().out
    assert cli.main(["templates", "remove", "멘토링"]) == 0
    assert cli.main(["templates", "show", "멘토링"]) == 1


def test_templates_errors(tmp_path, capsys):
    assert cli.main(["templates", "add", "x"]) == 1
    bad = tmp_path / "bad.md"
    bad.write_text("no headings here", encoding="utf-8")
    assert cli.main(["templates", "add", "x", str(bad)]) == 1
    assert "heading" in capsys.readouterr().err


def test_import_and_summarize_accept_a_template(tmp_path, isolated_home, monkeypatch):
    from vecho.templates import TemplateStore

    TemplateStore(isolated_home / "templates").save("멘토링", "## 현황\n")
    seen = {}

    def fake_summarize(session, config, **kwargs):
        seen["template"] = kwargs.get("template") or session.meta.template
        session.path_for("summary.md").write_text("# s", encoding="utf-8")
        return "# s"

    stub_pipeline(monkeypatch)
    monkeypatch.setattr(cli, "summarize_session", fake_summarize)
    me = tmp_path / "me.wav"
    write_wav(me)
    assert cli.main(["import", "--me", str(me), "--template", "멘토링"]) == 0
    assert seen["template"] == "멘토링"
    assert cli.main(["summarize", "--template", "기본 요약"]) == 0
    assert seen["template"] == "기본 요약"
    assert cli.main(["summarize", "--template", "없는 것"]) == 1


# ---- second review --------------------------------------------------------------------------


def test_record_with_an_unknown_template_leaves_nothing_behind(rig, isolated_home, capsys):
    assert cli.main(["record", "--mic-only", "--template", "없는 템플릿"]) == 1
    assert "no template named" in capsys.readouterr().err
    assert store_for(isolated_home).list() == []


def test_a_too_short_cli_recording_explains_itself_in_the_app(rig, isolated_home, monkeypatch):
    monkeypatch.setattr(cli, "_wait_for_stop", lambda r, c: rig.speak(0, 4000, frames=100))
    assert cli.main(["record", "--mic-only"]) == 0
    (session,) = store_for(isolated_home).list()
    assert [i["code"] for i in session.meta.issues] == ["too_short"]


def test_list_shows_sessions_without_speech(isolated_home, capsys):
    session = store_for(isolated_home).create("silent")
    save_segments(session.path_for("transcript.json"), [], "ko", "t")
    assert cli.main(["list"]) == 0
    assert "no speech" in capsys.readouterr().out
