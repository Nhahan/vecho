"""The web app's HTTP API, exercised over real HTTP with fake audio and a stubbed pipeline."""

from __future__ import annotations

import json
import shutil
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

from vecho import audio, jobs, recording, systemaudio
from vecho.audio import InputDevice
from vecho.errors import SummarizationError
from vecho.server import App, VechoHTTPServer, summary_body, tldr_of
from vecho.session import Session, SessionStore
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

    def abort(self):

        self.stop()

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
        path = urllib.parse.quote(path, safe="/?=&%")  # like encodeURIComponent in the UI
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
    ok = wait_until(
        lambda: client.call("GET", f"/api/sessions/{session_id}")[1]["status"] == "summarized"
    )
    detail = client.call("GET", f"/api/sessions/{session_id}")[1]
    assert ok, (detail["status"], detail["job"], detail["issues"])

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


# ---- templates --------------------------------------------------------------------------------

TEMPLATE = "## 현황\n\n- **면접**\n    - 총 3회 진행\n\n## 숙제\n\n1. 이력서 완성\n"


def test_templates_crud_and_default(served):
    app, client = served
    _, data, _ = client.call("GET", "/api/templates")
    assert data["default"] == "기본 요약" and data["templates"][0]["builtin"] is True

    status, data, _ = client.call("PUT", "/api/templates/멘토링", {"body": TEMPLATE})
    assert status == 200
    saved = next(t for t in data["templates"] if t["name"] == "멘토링")
    assert saved["sections"] == ["현황", "숙제"]

    _, data, _ = client.call("POST", "/api/templates/멘토링/default")
    assert data["default"] == "멘토링"

    # rename keeps it the default
    _, data, _ = client.call(
        "PUT", "/api/templates/멘토링 노트", {"body": TEMPLATE, "previous": "멘토링"}
    )
    assert [t["name"] for t in data["templates"]] == ["기본 요약", "멘토링 노트"]
    assert data["default"] == "멘토링 노트"

    _, data, _ = client.call("DELETE", "/api/templates/멘토링 노트")
    assert data["default"] == "기본 요약"


def test_template_errors(served):
    _, client = served
    assert client.call("PUT", "/api/templates/x", {"body": "제목 없음"})[0] == 400
    assert client.call("PUT", "/api/templates/기본 요약", {"body": TEMPLATE})[0] == 400
    assert client.call("DELETE", "/api/templates/없음")[0] == 400
    assert client.call("POST", "/api/record/start", {"template": "없음"})[0] == 404


def test_recording_and_processing_remember_the_template(served, mic):
    app, client = served
    client.call("PUT", "/api/templates/멘토링", {"body": TEMPLATE})
    _, state, _ = client.call("POST", "/api/record/start", {"template": "멘토링", "mic_only": True})
    session_id = state["recording"]["session_id"]
    mic()
    client.call("POST", "/api/record/stop")
    assert app.store.resolve(session_id).meta.template == "멘토링"

    session = make_session(app, "다시", summary=False)
    client.call(
        "POST", f"/api/sessions/{session.id}/process", {"step": "all", "template": "멘토링"}
    )
    assert wait_until(
        lambda: client.call("GET", f"/api/sessions/{session.id}")[1]["template"] == "멘토링"
    )


def test_import_with_a_template(served):
    app, client = served
    client.call("PUT", "/api/templates/멘토링", {"body": TEMPLATE})
    _, result, _ = client.call(
        "POST",
        "/api/import",
        raw=b"RIFF" + b"\0" * 100,
        headers={"X-Filename": "call.m4a", "X-Template": "%EB%A9%98%ED%86%A0%EB%A7%81"},
    )
    assert app.store.resolve(result["session_id"]).meta.template == "멘토링"


def test_preview_is_plain_text_for_template_summaries():
    md = "# t\n\n> meta\n\n## 1. 현황\n\n- **지원 현황**\n    - 원티드 **150개** 지원\n"
    assert tldr_of(md) == "원티드 150개 지원"
    assert tldr_of("# t\n\n> m\n\n## A\n\n| a | b |\n| --- | --- |\n\n---\n") == ""


def test_clients_dropping_connections_are_not_logged(served, capsys):
    import socket

    app, client = served
    host, port = client.base.replace("http://", "").split(":")
    for _ in range(3):
        with socket.create_connection((host, int(port))) as sock:
            sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00"
            )
    time.sleep(0.3)
    assert "Traceback" not in capsys.readouterr().err


def test_processing_twice_is_a_conflict_and_keeps_the_template(served):
    import threading as th

    app, client = served
    gate = th.Event()
    original = app.processor._transcribe

    def slow(*args, **kwargs):
        gate.wait(5)
        return original(*args, **kwargs)

    app.processor._transcribe = slow
    client.call("PUT", "/api/templates/멘토링", {"body": TEMPLATE})
    session = make_session(app, "느림", summary=False)
    client.call("POST", f"/api/sessions/{session.id}/process", {"step": "all"})
    status, _, _ = client.call(
        "POST", f"/api/sessions/{session.id}/process", {"step": "all", "template": "멘토링"}
    )
    assert status == 409
    gate.set()
    assert wait_until(
        lambda: client.call("GET", f"/api/sessions/{session.id}")[1]["status"] == "summarized"
    )
    assert app.store.resolve(session.id).meta.template != "멘토링"


# ---- regressions found in review ----------------------------------------------------------


def test_concurrent_audio_requests_all_get_the_full_mix(served):
    from concurrent.futures import ThreadPoolExecutor

    app, client = served
    session = make_session(app, tracks=("me", "remote"))
    path = f"/api/sessions/{session.id}/audio?t={app.token}"
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: client.call("GET", path, token=False), range(6)))
    sizes = {len(body) for status, body, _ in results if status == 200}
    assert [r[0] for r in results] == [200] * 6 and len(sizes) == 1 and sizes.pop() > 1000


def test_mix_of_tracks_with_different_rates(tmp_path):
    from vecho.server import _mix_wavs

    for name, rate, value in (("a.wav", 16000, 1000), ("b.wav", 48000, 2000)):
        with wave.open(str(tmp_path / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(np.full(rate, value, dtype="<i2").tobytes())
    _mix_wavs([tmp_path / "a.wav", tmp_path / "b.wav"], tmp_path / "mix.wav")
    with wave.open(str(tmp_path / "mix.wav")) as w:
        assert w.getframerate() == 48000
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert abs(int(np.median(data)) - 3000) <= 1


def test_corrupt_session_files_do_not_break_the_list(served):
    app, client = served
    make_session(app, "정상")
    for name, raw in (
        ("a-bad-utf8", b"\xff\xfe"),
        ("b-list", b"[]"),
        ("c-missing", b'{"title":"x"}'),
    ):
        (app.store.root / name).mkdir(parents=True)
        (app.store.root / name / "session.json").write_bytes(raw)
    status, items, _ = client.call("GET", "/api/sessions")
    assert status == 200 and [i["title"] for i in items] == ["정상"]


def test_bad_content_length_gets_an_answer(served):
    import http.client

    app, client = served
    host, port = client.base.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("PATCH", "/api/sessions/x")
    conn.putheader("X-Vecho-Token", app.token)
    conn.putheader("Content-Length", "abc")
    conn.endheaders()
    assert conn.getresponse().status == 400


def test_a_rejected_upload_does_not_poison_the_connection(served):
    import http.client

    app, client = served
    host, port = client.base.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.request(
        "POST",
        "/api/import",
        body=b"x" * 100,
        headers={"X-Vecho-Token": app.token, "X-Filename": "notes.txt"},
    )
    response = conn.getresponse()
    assert response.status == 400 and response.getheader("Connection") == "close"
    response.read()


def test_a_session_being_finalized_cannot_be_deleted(served):
    app, client = served
    session = make_session(app)
    app._stopping.add(session.id)
    assert client.call("DELETE", f"/api/sessions/{session.id}")[0] == 409
    app._stopping.discard(session.id)


def test_detail_says_which_renderer_the_summary_needs(served):
    app, client = served
    plain = make_session(app, "기본")
    _, detail, _ = client.call("GET", f"/api/sessions/{plain.id}")
    assert detail["template_summary"] is False
    plain.meta.template = "멘토링"
    plain.save()
    app.templates.save("멘토링", TEMPLATE)
    app.templates.delete("멘토링")  # deleting the template must not change old summaries
    _, detail, _ = client.call("GET", f"/api/sessions/{plain.id}")
    assert detail["template_summary"] is True


def test_mix_shifts_a_track_that_started_late(tmp_path):
    from vecho.server import _mix_wavs

    for name in ("me.wav", "remote.wav"):
        with wave.open(str(tmp_path / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(np.full(16000, 1000, dtype="<i2").tobytes())
    _mix_wavs(
        [tmp_path / "me.wav", tmp_path / "remote.wav"],
        tmp_path / "mix.wav",
        {tmp_path / "remote.wav": 0.5},
    )
    with wave.open(str(tmp_path / "mix.wav")) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert len(data) == 24000  # 1 s + 0.5 s delay
    assert data[:8000].tolist() == [1000] * 8000
    assert data[8000:16000].tolist() == [2000] * 8000
    assert data[16000:].tolist() == [1000] * 8000


def test_a_failed_other_side_keeps_the_microphone_recording(served, mic, monkeypatch):
    app, client = served
    monkeypatch.setattr(
        systemaudio,
        "prepare",
        lambda bin_dir: SystemAudioSource(command=(sys.executable, FAKE_TAP, "48000", "crash")),
    )
    client.call("POST", "/api/record/start", {})
    mic()
    time.sleep(0.6)  # the fake helper dies
    status, result, _ = client.call("POST", "/api/record/stop")
    assert status == 200 and result["processing"] is True
    assert any(i["code"] == "stopped" and i["role"] == "remote" for i in result["issues"])
    session = app.store.resolve(result["session_id"])
    assert session.meta.duration_sec >= 1.0


# ---- second review --------------------------------------------------------------------------


def test_a_title_typed_while_recording_stays_in_the_recorder(served, mic):
    app, client = served
    _, state, _ = client.call("POST", "/api/record/start", {"mic_only": True})
    sid = state["recording"]["session_id"]
    client.call("PATCH", f"/api/sessions/{sid}", {"title": "Typed"})
    assert client.call("GET", "/api/state")[1]["recording"]["title"] == "Typed"
    mic()
    client.call("POST", "/api/record/stop")
    assert app.store.resolve(sid).meta.title == "Typed"


def test_a_failed_resummarize_keeps_the_renderer_of_the_summary_on_disk(served):
    app, client = served
    client.call("PUT", "/api/templates/멘토링", {"body": TEMPLATE})
    session = make_session(app, "boom", summary=False)  # its summarize will fail
    fake_transcribe(session, app.config)
    session.path_for("summary.md").write_text(SUMMARY, encoding="utf-8")  # built-in summary
    session.meta.summary_template = "기본 요약"
    session.save()
    client.call(
        "POST", f"/api/sessions/{session.id}/process", {"step": "summarize", "template": "멘토링"}
    )
    assert wait_until(
        lambda: client.call("GET", f"/api/sessions/{session.id}")[1]["status"] == "error"
    )
    _, detail, _ = client.call("GET", f"/api/sessions/{session.id}")
    assert detail["template"] == "멘토링" and detail["template_summary"] is False


def test_playback_mixes_24_bit_tracks(served):
    app, client = served
    session = app.store.create("24bit")
    for role, value in (("me", 1000), ("remote", 2000)):
        with wave.open(str(session.path_for(f"{role}.wav")), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(3)
            w.setframerate(16000)
            sample = int(value * 256).to_bytes(3, "little", signed=True)
            w.writeframes(sample * 16000)
        session.meta.tracks[role] = f"{role}.wav"
    session.save()
    status, _, _ = client.call("GET", f"/api/sessions/{session.id}/audio")
    assert status == 200
    with wave.open(str(session.path_for("mix.wav"))) as w:
        assert w.getsampwidth() == 2
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert abs(int(np.median(data)) - 3000) <= 2
    assert not list(session.dir.glob(".decoded.*"))  # temporary decodes are cleaned up


def test_template_names_with_a_slash_can_be_managed(served):
    _, client = served
    assert client.call("PUT", "/api/templates/a%2Fb", {"body": "## x\n"})[0] == 200
    assert client.call("POST", "/api/templates/a%2Fb/default")[1]["default"] == "a/b"
    assert client.call("DELETE", "/api/templates/a%2Fb")[0] == 200


def test_quitting_waits_for_a_stop_that_is_finishing(served):
    import threading as th

    app, _ = served
    app._stopping.add("finishing")

    def finish():
        time.sleep(0.4)
        with app._settled:
            app._stopping.discard("finishing")
            app._settled.notify_all()

    th.Thread(target=finish).start()
    started = time.monotonic()
    app.shutdown(wait=5)
    assert 0.3 <= time.monotonic() - started < 3


def test_unexpected_errors_still_get_an_answer(served, monkeypatch):
    app, client = served
    monkeypatch.setattr(app, "list_sessions", lambda: 1 / 0)
    status, body, _ = client.call("GET", "/api/sessions")
    assert status == 500 and "unexpected error" in body["error"]


def test_errors_carry_a_code_for_translated_messages(served):
    _, client = served
    client.call("PUT", "/api/templates/A", {"body": "## a\n"})
    client.call("PUT", "/api/templates/B", {"body": "## b\n"})
    status, body, _ = client.call("PUT", "/api/templates/B", {"body": "## x\n", "previous": "A"})
    assert status == 400 and body["code"] == "template_exists" and "already exists" in body["error"]
    assert client.call("POST", "/api/record/stop")[1]["code"] == "not_recording"


def test_creating_a_template_never_overwrites_an_existing_one(served):
    app, client = served
    client.call("PUT", "/api/templates/주간 회의", {"body": "## 원래\n"})
    status, body, _ = client.call("PUT", "/api/templates/주간 회의", {"body": "## 새것\n"})
    assert status == 400 and body["code"] == "template_exists"
    assert app.templates.get("주간 회의").body == "## 원래\n"
    # editing it (previous = its name) still works
    assert (
        client.call(
            "PUT", "/api/templates/주간 회의", {"body": "## 수정\n", "previous": "주간 회의"}
        )[0]
        == 200
    )


# ---- faults ---------------------------------------------------------------------------------


def test_a_failed_save_at_start_stops_the_microphone(served, monkeypatch):
    app, client = served
    stopped = []
    real_stop = audio.Recorder.stop
    monkeypatch.setattr(audio.Recorder, "stop", lambda self: stopped.append(1) or real_stop(self))

    real_save = Session.save

    def full(self):
        if self.meta.tracks:  # the save right after the microphone started
            raise OSError(28, "No space left on device")
        real_save(self)

    monkeypatch.setattr(Session, "save", full)
    status, body, _ = client.call("POST", "/api/record/start", {"mic_only": True})
    assert status == 500 and body["code"] == "disk_full"
    assert stopped and app.store.list() == []
    assert client.call("GET", "/api/state")[1]["recording"] is None


def test_damaged_files_never_hide_a_session(served):
    app, client = served
    good = make_session(app, "좋은 회의")
    bad = make_session(app, "망가진 회의")
    bad.path_for("summary.md").write_bytes(b"# t\n\n## \xff\xfe\n")
    bad.path_for("transcript.json").write_text('{"segments": [{"start": 0}]}', "utf-8")
    status, listed, _ = client.call("GET", "/api/sessions")
    assert status == 200 and {s["id"] for s in listed} == {good.id, bad.id}
    status, detail, _ = client.call("GET", f"/api/sessions/{bad.id}")
    assert status == 200 and detail["segments"] == []
    assert {"code": "bad_transcript", "role": "", "hint": ""} in detail["issues"]


def test_a_copied_session_folder_is_its_own_session(served):
    app, client = served
    original = make_session(app, "원본")
    shutil.copytree(original.dir, app.store.root / "사본")
    _, listed, _ = client.call("GET", "/api/sessions")
    assert sorted(s["id"] for s in listed) == sorted([original.id, "사본"])
    assert client.call("GET", "/api/sessions/사본")[0] == 200


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/api/record/start", {"template": {"name": "x"}}),
        ("POST", "/api/record/start", {"title": ["a"]}),
        ("PUT", "/api/templates/a", {"body": "## A", "previous": 5}),
    ],
)
def test_values_of_the_wrong_type_are_refused(served, method, path, body):
    status, answer, _ = served[1].call(method, path, body)
    assert status == 400 and answer["error"] == "expected text"


def test_malformed_requests_get_a_clear_answer(served):
    _, client = served
    assert client.call("GET", "/api/sessions/" + "a" * 5000)[0] == 404
    status, answer, _ = client.call("PATCH", "/api/sessions/x", raw=b"[" * 100000)
    assert status == 400 and answer["error"] == "invalid JSON"
    status, answer, _ = client.call("PUT", "/api/templates/a", raw=b" " * (3 << 20))
    assert status == 400 and answer["code"] == "request_too_large"  # not a connection reset


# ---- races and state ------------------------------------------------------------------------


def test_quitting_while_a_recording_starts_still_saves_it(served, mic, monkeypatch):
    app, client = served
    entered, release = threading.Event(), threading.Event()
    real_start = recording.LiveRecording.start

    def slow_start(self):
        entered.set()
        release.wait(5)
        real_start(self)

    monkeypatch.setattr(recording.LiveRecording, "start", slow_start)
    starter = threading.Thread(target=lambda: client.call("POST", "/api/record/start", {}))
    starter.start()
    assert entered.wait(5)
    session_id = app.store.list()[0].id
    assert client.call("DELETE", f"/api/sessions/{session_id}")[0] == 409  # still starting
    quitter = threading.Thread(target=app.shutdown)
    quitter.start()
    time.sleep(0.1)
    release.set()
    starter.join(5)
    quitter.join(10)
    assert app._live is None  # nothing left capturing
    session = app.store.list()[0]
    assert session.meta.duration_sec is not None  # stopped and saved, not abandoned


def test_quitting_mid_recording_keeps_its_issues(served, mic):
    app, client = served
    client.call("POST", "/api/record/start", {"mic_only": True})
    app.shutdown()  # nothing was said: silent and too short
    codes = {i["code"] for i in app.store.list()[0].meta.issues}
    assert {"silent", "too_short"} <= codes


def test_a_failed_save_does_not_leave_the_session_busy(served, monkeypatch):
    app, client = served
    session = make_session(app, summary=False)
    app.templates.save("회의록", "## 요약\n")

    def full(self):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Session, "save", full)
    status, _, _ = client.call(
        "POST", f"/api/sessions/{session.id}/process", {"template": "회의록"}
    )
    assert status == 500
    monkeypatch.undo()
    assert client.call("DELETE", f"/api/sessions/{session.id}")[0] == 200


def test_reprocessing_without_speech_drops_the_old_summary(served, monkeypatch):
    app, client = served
    session = make_session(app)

    def silent(session, config, transcriber=None, on_progress=None):
        save_segments(session.path_for("transcript.json"), [], "ko", "tiny")

    monkeypatch.setattr(app.processor, "_transcribe", silent)
    client.call("POST", f"/api/sessions/{session.id}/process", {"step": "all"})
    assert app.processor.wait_idle()
    _, detail, _ = client.call("GET", f"/api/sessions/{session.id}")
    assert detail["summary"] == "" and detail["status"] == "empty"


def test_renaming_a_template_keeps_it_for_sessions_that_chose_it(served):
    app, client = served
    app.templates.save("회의록", "## 요약\n")
    session = make_session(app, summary=False)
    session.meta.template = "회의록"
    session.save()
    client.call("PUT", "/api/templates/주간 회의록", {"body": "## 요약\n", "previous": "회의록"})
    assert Session.load(session.dir).meta.template == "주간 회의록"


def test_a_session_folder_with_spaces_can_be_opened_and_deleted(served):
    app, client = served
    original = make_session(app, "원본")
    shutil.copytree(original.dir, app.store.root / f"{original.id} copy")
    assert client.call("GET", f"/api/sessions/{original.id} copy")[0] == 200
    assert client.call("DELETE", f"/api/sessions/{original.id} copy")[0] == 200
