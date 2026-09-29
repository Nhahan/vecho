"""The web app's HTTP API, exercised over real HTTP with fake audio and a stubbed pipeline."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import numpy as np
import pytest

from vecho import audio, jobs, systemaudio
from vecho.audio import InputDevice
from vecho.errors import SummarizationError
from vecho.server import App, VechoHTTPServer, summary_body, tldr_of
from vecho.session import SessionStore
from vecho.systemaudio import SystemAudioSource
from vecho.transcript import Segment, save_segments

MIC = InputDevice(0, "Built-in Mic", 1, 48000.0, is_default=True)
FAKE_TAP = str(Path(__file__).with_name("fake_tap.py"))
SUMMARY = "# t\n\n> meta\n\n## 한 줄 요약\n예산 확정.\n\n## 액션 아이템\n- [ ] 보고서 — 상대방\n"


class Stream:
    def __init__(self, callback, channels):
        self.callback, self.channels = callback, channels

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def mic(monkeypatch):
    streams = {}

    def factory(device, samplerate, channels, callback):
        streams[device] = Stream(callback, channels)
        return streams[device]

    monkeypatch.setattr(audio, "_default_stream_factory", factory)
    monkeypatch.setattr(audio, "list_input_devices", lambda: [MIC])
    monkeypatch.setattr(
        systemaudio,
        "prepare",
        lambda bin_dir: SystemAudioSource(command=(sys.executable, FAKE_TAP, "48000", "loud")),
    )

    def speak(amplitude=4000, frames=16000):
        stream = streams[0]
        stream.callback(np.full((frames, 1), amplitude, dtype=np.int16), frames, None, None)

    return speak


def fake_transcribe(session, config, transcriber=None, on_progress=None):
    if on_progress:
        on_progress(next(iter(session.meta.tracks)), 1.0, 2.0)
    save_segments(
        session.path_for("transcript.json"),
        [
            Segment(0, 1, "me", "안녕하세요"),
            Segment(1.2, 2, "me", "반갑습니다"),
            Segment(3, 4, "remote", "네 안녕하세요"),
        ],
        "ko",
        "tiny",
    )


def fake_summarize(session, config, on_progress=None):
    if session.meta.title == "boom":
        raise SummarizationError("cannot reach Ollama")
    session.path_for("summary.md").write_text(SUMMARY, encoding="utf-8")


class Client:
    def __init__(self, server: VechoHTTPServer, token: str):
        self.base = server.url.rstrip("/")
        self.token = token

    def call(self, method, path, body=None, token=True, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        path = urllib.parse.quote(path, safe="/?=&")  # like encodeURIComponent in the UI
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            request.add_header("X-Vecho-Token", self.token)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read()
                status, response_headers = response.status, response.headers
        except urllib.error.HTTPError as exc:
            payload, status, response_headers = exc.read(), exc.code, exc.headers
        kind = response_headers.get("Content-Type", "")
        return status, json.loads(payload) if "json" in kind else payload, response_headers


@pytest.fixture
def served(config, mic):
    processor = jobs.Processor(config, transcribe=fake_transcribe, summarize=fake_summarize)
    app = App(config, processor)
    server = VechoHTTPServer(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield app, Client(server, app.token)
    app.shutdown()
    server.shutdown()
    server.server_close()


def wait_until(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


def make_session(app, title="회의", tracks=("me",), summary=True):
    session = app.store.create(title)
    for role in tracks:
        with wave.open(str(session.path_for(f"{role}.wav")), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(np.full(16000, 1000 if role == "me" else 2000, dtype="<i2").tobytes())
        session.meta.tracks[role] = f"{role}.wav"
    session.meta.duration_sec = 1.0
    session.save()
    if summary:
        fake_transcribe(session, app.config)
        fake_summarize(session, app.config)
    return session


# ---- page and security ----------------------------------------------------------------


def test_index_embeds_the_token_and_is_locked_down(served):
    app, client = served
    status, body, headers = client.call("GET", "/", token=False)
    assert status == 200 and app.token.encode() in body
    assert b"__VECHO_TOKEN__" not in body
    csp = headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "default-src 'none'" in csp


def test_ui_assets_are_served_from_an_allowlist(served):
    _, client = served
    status, body, headers = client.call("GET", "/assets/app.js", token=False)
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")
    status, body, headers = client.call("GET", "/assets/PretendardVariable.woff2", token=False)
    assert status == 200 and headers["Content-Type"] == "font/woff2" and len(body) > 100_000
    assert client.call("GET", "/assets/index.html", token=False)[0] == 404
    assert client.call("GET", "/assets/../server.py", token=False)[0] == 404


def test_api_requires_the_token(served):
    _, client = served
    status, body, _ = client.call("GET", "/api/state", token=False)
    assert status == 403 and "token" in body["error"]
    client.token = "wrong"
    assert client.call("GET", "/api/state")[0] == 403


def test_foreign_host_header_is_rejected(served):
    """Blocks DNS-rebinding pages from talking to the local server."""
    _, client = served
    status, _, _ = client.call("GET", "/api/state", headers={"Host": "evil.example:80"})
    assert status == 403


def test_unknown_routes_and_bad_ids(served):
    _, client = served
    assert client.call("GET", "/api/nope")[0] == 404
    assert client.call("GET", "/api/sessions/..")[0] == 404
    assert client.call("GET", "/api/sessions/does-not-exist")[0] == 404
    assert client.call("GET", "/static/x.js", token=False)[0] == 404


# ---- recording --------------------------------------------------------------------------


def test_record_start_stop_then_process_automatically(served, mic):
    app, client = served
    status, state, _ = client.call("POST", "/api/record/start", {"title": "주간 회의"})
    assert status == 200
    rec = state["recording"]
    assert rec["title"] == "주간 회의"
    assert set(rec["sources"]) == {"me", "remote"}
    assert rec["sources"]["remote"]["name"] == "System audio (all apps)"

    assert client.call("POST", "/api/record/start", {})[0] == 409  # only one at a time
    mic()
    time.sleep(0.3)
    status, state, _ = client.call("GET", "/api/state")
    assert state["recording"]["levels"]["me"] > 0

    status, result, _ = client.call("POST", "/api/record/stop")
    assert status == 200 and result["processing"] is True
    session_id = result["session_id"]
    assert wait_until(
        lambda: client.call("GET", f"/api/sessions/{session_id}")[1]["status"] == "summarized"
    )

    _, detail, _ = client.call("GET", f"/api/sessions/{session_id}")
    assert detail["summary"].startswith("## 한 줄 요약")  # file header stripped
    assert [s["text"] for s in detail["segments"]] == ["안녕하세요 반갑습니다", "네 안녕하세요"]
    assert detail["segments"][0]["label"] == "나"
    assert client.call("GET", "/api/state")[1]["recording"] is None


def test_stop_without_recording_is_a_conflict(served):
    assert served[1].call("POST", "/api/record/stop")[0] == 409


def test_very_short_recording_is_not_processed(served, mic, monkeypatch):
    monkeypatch.setattr(
        systemaudio,
        "prepare",
        lambda bin_dir: SystemAudioSource(command=(sys.executable, FAKE_TAP, "48000", "silent")),
    )
    _, client = served
    client.call("POST", "/api/record/start", {"mic_only": True})
    mic(frames=100)
    status, result, _ = client.call("POST", "/api/record/stop")
    assert status == 200 and result["processing"] is False
    codes = {issue["code"] for issue in result["issues"]}
    assert codes == {"too_short"}
    reloaded = App(served[0].config, jobs.Processor(served[0].config))  # survives a restart
    assert reloaded.session_detail(result["session_id"])["issues"] == result["issues"]
    reloaded.shutdown()


def test_start_failure_is_reported_and_leaves_nothing_behind(served, monkeypatch):
    app, client = served
    monkeypatch.setattr(audio, "list_input_devices", lambda: [])
    status, body, _ = client.call("POST", "/api/record/start", {})
    assert status == 400 and "no audio input" in body["error"]
    assert app.store.list() == []


def test_shutdown_keeps_a_running_recording(served, mic):
    app, client = served
    _, state, _ = client.call("POST", "/api/record/start", {"mic_only": True})
    mic()
    app.shutdown()
    session = app.store.list()[0]
    with wave.open(str(session.audio_path("me"))) as wav:
        assert wav.getnframes() == 16000  # the WAV was finalized


# ---- sessions -------------------------------------------------------------------------------


def test_list_shows_status_and_tldr(served):
    app, client = served
    make_session(app, "완료된 회의")
    make_session(app, "아직", summary=False)
    _, items, _ = client.call("GET", "/api/sessions")
    by_title = {item["title"]: item for item in items}
    assert by_title["완료된 회의"]["status"] == "summarized"
    assert by_title["완료된 회의"]["tldr"] == "예산 확정."
    assert by_title["아직"]["status"] == "recorded"


def test_rename(served):
    app, client = served
    session = make_session(app)
    status, detail, _ = client.call("PATCH", f"/api/sessions/{session.id}", {"title": "새 제목"})
    assert status == 200 and detail["title"] == "새 제목"
    assert client.call("PATCH", f"/api/sessions/{session.id}", {"title": "  "})[0] == 400


def test_delete(served):
    app, client = served
    session = make_session(app)
    assert client.call("DELETE", f"/api/sessions/{session.id}")[0] == 200
    assert not session.dir.exists()


def test_cannot_delete_while_recording(served, mic):
    app, client = served
    _, state, _ = client.call("POST", "/api/record/start", {"mic_only": True})
    session_id = state["recording"]["session_id"]
    assert client.call("DELETE", f"/api/sessions/{session_id}")[0] == 409
    client.call("POST", "/api/record/stop")


def test_failed_processing_is_shown_and_can_be_retried(served):
    app, client = served
    session = make_session(app, "boom", summary=False)
    client.call("POST", f"/api/sessions/{session.id}/process", {"step": "all"})
    assert wait_until(
        lambda: client.call("GET", f"/api/sessions/{session.id}")[1]["status"] == "error"
    )
    _, detail, _ = client.call("GET", f"/api/sessions/{session.id}")
    assert "cannot reach Ollama" in detail["job"]["error"]
    assert detail["segments"]  # the transcript part still succeeded

    client.call("PATCH", f"/api/sessions/{session.id}", {"title": "fixed"})
    client.call("POST", f"/api/sessions/{session.id}/process", {"step": "summarize"})
    assert wait_until(
        lambda: client.call("GET", f"/api/sessions/{session.id}")[1]["status"] == "summarized"
    )


def test_summarize_without_transcript_runs_everything(served):
    app, client = served
    session = make_session(app, summary=False)
    status, detail, _ = client.call(
        "POST", f"/api/sessions/{session.id}/process", {"step": "summarize"}
    )
    assert status == 200, detail
    assert detail["job"]["step"] == "all"


def test_unknown_step_is_rejected(served):
    app, client = served
    session = make_session(app)
    assert client.call("POST", f"/api/sessions/{session.id}/process", {"step": "nope"})[0] == 400


# ---- audio ------------------------------------------------------------------------------------


def test_audio_mixes_both_tracks_and_supports_ranges(served):
    app, client = served
    session = make_session(app, tracks=("me", "remote"))
    path = f"/api/sessions/{session.id}/audio?t={app.token}"
    status, body, headers = client.call("GET", path, token=False)
    assert status == 200 and headers["Content-Type"] == "audio/x-wav"
    assert headers["Accept-Ranges"] == "bytes"
    with wave.open(str(session.path_for("mix.wav"))) as wav:
        mixed = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    assert set(mixed.tolist()) == {3000}  # 1000 + 2000

    status, part, headers = client.call("GET", path, token=False, headers={"Range": "bytes=0-99"})
    assert status == 206 and len(part) == 100
    assert headers["Content-Range"] == f"bytes 0-99/{len(body)}"
    status, _, _ = client.call("GET", path, token=False, headers={"Range": "bytes=999999999-"})
    assert status == 416


def test_audio_needs_the_token(served):
    app, client = served
    session = make_session(app)
    assert client.call("GET", f"/api/sessions/{session.id}/audio", token=False)[0] == 403


def test_single_track_is_served_directly(served):
    app, client = served
    session = make_session(app, tracks=("me",))
    client.call("GET", f"/api/sessions/{session.id}/audio")
    assert not session.path_for("mix.wav").exists()


# ---- import ---------------------------------------------------------------------------------


def test_import_uploads_and_processes(served):
    app, client = served
    data = b"RIFF" + b"\0" * 2000
    status, result, _ = client.call(
        "POST",
        "/api/import",
        raw=data,
        headers={
            "X-Filename": "%ED%9A%8C%EC%9D%98.m4a",
            "Content-Type": "application/octet-stream",
        },
    )
    assert status == 200
    session = app.store.resolve(result["session_id"])
    assert session.meta.title == "회의" and session.meta.tracks == {"mixed": "mixed.m4a"}
    assert session.path_for("mixed.m4a").read_bytes() == data
    assert wait_until(
        lambda: client.call("GET", f"/api/sessions/{session.id}")[1]["status"] == "summarized"
    )


def test_import_rejects_other_files(served):
    app, client = served
    status, body, _ = client.call(
        "POST", "/api/import", raw=b"x", headers={"X-Filename": "notes.txt"}
    )
    assert status == 400 and ".txt" in body["error"]
    assert app.store.list() == []


# ---- doctor ------------------------------------------------------------------------------------


def test_doctor_reports_each_part(served, monkeypatch):
    from vecho import server

    monkeypatch.setattr(server.OllamaClient, "has_model", lambda self: False)
    _, client = served
    _, checks, _ = client.call("GET", "/api/doctor")
    by_key = {c["key"]: c for c in checks}
    assert by_key["microphone"]["status"] == "ok"
    assert by_key["system_audio"]["status"] == "ok"
    assert by_key["llm"]["status"] == "fail" and "ollama pull" in by_key["llm"]["fix"]


# ---- helpers ---------------------------------------------------------------------------


def test_summary_helpers():
    assert summary_body(SUMMARY).startswith("## 한 줄 요약")
    assert tldr_of(SUMMARY) == "예산 확정."
    assert tldr_of("") == ""


def test_sessions_are_listed_across_restarts(config, mic):
    """A second App on the same data directory sees earlier sessions."""
    first = App(
        config, jobs.Processor(config, transcribe=fake_transcribe, summarize=fake_summarize)
    )
    make_session(first, "old")
    first.shutdown()
    second = App(config, jobs.Processor(config))
    assert [s["title"] for s in second.list_sessions()] == ["old"]
    second.shutdown()
    assert SessionStore(config.sessions_dir).list()


def test_silence_is_not_summarized_and_reads_as_empty(served, monkeypatch):
    app, client = served

    def silent_transcribe(session, config, transcriber=None, on_progress=None):
        save_segments(session.path_for("transcript.json"), [], "ko", "tiny")

    monkeypatch.setattr(app.processor, "_transcribe", silent_transcribe)
    session = make_session(app, summary=False)
    client.call("POST", f"/api/sessions/{session.id}/process", {"step": "all"})
    assert wait_until(
        lambda: client.call("GET", f"/api/sessions/{session.id}")[1]["status"] == "empty"
    )
    assert app.processor.state(session.id).stage == jobs.DONE  # not an error
    assert not session.has_summary


def test_a_silent_other_side_is_reported_to_the_app(served, mic, monkeypatch):
    monkeypatch.setattr(
        systemaudio,
        "prepare",
        lambda bin_dir: SystemAudioSource(command=(sys.executable, FAKE_TAP, "48000", "silent")),
    )
    app, client = served
    client.call("POST", "/api/record/start", {})
    mic()
    time.sleep(0.3)
    _, result, _ = client.call("POST", "/api/record/stop")
    assert {"code": "silent", "role": "remote"}.items() <= result["issues"][0].items()
    _, detail, _ = client.call("GET", f"/api/sessions/{result['session_id']}")
    assert detail["issues"][0]["code"] == "silent"
