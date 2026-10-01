"""Summarizer tests run against a real local HTTP server that mimics Ollama's API."""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from vecho import transcript
from vecho.errors import SummarizationError
from vecho.session import SessionStore
from vecho.summarize import (
    OllamaClient,
    build_template,
    strip_reasoning,
    summarize_lines,
    summarize_session,
)
from vecho.transcript import Segment


class FakeOllama:
    def __init__(self):
        self.requests: list[dict] = []
        self.replies: list[str] = ["FINAL SUMMARY"]
        self.status = 200
        self.error_body = {"error": "boom"}
        self.models = ["qwen3:8b", "llama3:latest"]
        self.raw_reply: dict | None = None  # sent verbatim instead of a normal answer
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send(200, {"models": [{"name": name} for name in outer.models]})

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                outer.requests.append(json.loads(self.rfile.read(length)))
                if outer.status != 200:
                    self._send(outer.status, outer.error_body)
                    return
                if outer.raw_reply is not None:
                    self._send(200, outer.raw_reply)
                    return
                index = min(len(outer.requests), len(outer.replies)) - 1
                self._send(200, {"message": {"role": "assistant", "content": outer.replies[index]}})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    with FakeOllama() as server:
        yield server


def client_for(server, **kwargs):
    return OllamaClient(server.host, "qwen3:8b", **kwargs)


def test_strip_reasoning():
    assert strip_reasoning("<think>hmm\nmore</think>\n\nAnswer") == "Answer"
    assert strip_reasoning("plain") == "plain"


def test_chat_sends_context_window_and_disables_thinking(ollama):
    ollama.replies = ["<think>x</think>Hello"]
    result = client_for(ollama, num_ctx=4096).chat("sys", "user")
    assert result == "Hello"
    request = ollama.requests[0]
    assert request["model"] == "qwen3:8b"
    assert request["stream"] is False and request["think"] is False
    assert request["options"]["num_ctx"] == 4096
    assert [m["role"] for m in request["messages"]] == ["system", "user"]


def test_single_chunk_makes_one_call(ollama):
    progress = []
    result = summarize_lines(
        ["[00:00:01] 나: hi"],
        client_for(ollama),
        "Korean",
        1000,
        on_progress=lambda *a: progress.append(a),
    )
    assert result == "FINAL SUMMARY"
    assert len(ollama.requests) == 1
    prompt = ollama.requests[0]["messages"][1]["content"]
    assert "[00:00:01] 나: hi" in prompt
    assert "Korean" in ollama.requests[0]["messages"][0]["content"]
    assert progress == [("summary", 1, 1)]


def test_long_transcript_uses_map_reduce(ollama):
    ollama.replies = ["notes A", "notes B", "notes C", "MERGED"]
    lines = ["x" * 70, "y" * 70, "z" * 70]
    progress = []
    result = summarize_lines(
        lines, client_for(ollama), "English", 80, on_progress=lambda *a: progress.append(a)
    )
    assert result == "MERGED"
    assert len(ollama.requests) == 4
    assert "part 1 of 3" in ollama.requests[0]["messages"][1]["content"]
    final_prompt = ollama.requests[3]["messages"][1]["content"]
    assert "notes A" in final_prompt and "notes C" in final_prompt
    assert [p[0] for p in progress] == ["notes", "notes", "notes", "summary"]


def test_empty_transcript_is_rejected_without_calling_the_model(ollama):
    with pytest.raises(SummarizationError, match="empty"):
        summarize_lines(["", "  "], client_for(ollama), "Korean", 100)
    assert ollama.requests == []


def test_missing_model_gives_pull_hint(ollama):
    ollama.status = 404
    ollama.error_body = {"error": "model 'qwen3:8b' not found"}
    with pytest.raises(SummarizationError, match="ollama pull qwen3:8b"):
        client_for(ollama).chat("s", "u")


def test_server_error_is_surfaced(ollama):
    ollama.status = 500
    with pytest.raises(SummarizationError, match="HTTP 500: boom"):
        client_for(ollama).chat("s", "u")


def test_unreachable_server_suggests_ollama_serve():
    client = OllamaClient("http://127.0.0.1:9", "m", timeout=2)
    with pytest.raises(SummarizationError, match="ollama serve"):
        client.chat("s", "u")


@pytest.mark.parametrize("payload", [{"message": {"content": None}}, {"message": {}}, {}])
def test_malformed_reply_is_reported(ollama, payload):
    ollama.raw_reply = payload
    with pytest.raises(SummarizationError):
        client_for(ollama).chat("s", "u")


def test_blank_answer_is_rejected(ollama):
    ollama.raw_reply = {"message": {"content": "   "}}
    with pytest.raises(SummarizationError, match="empty answer"):
        client_for(ollama).chat("s", "u")


def test_has_model_matches_implicit_latest(ollama):
    assert client_for(ollama).has_model()
    ollama.models = ["llama3:latest"]
    assert not client_for(ollama).has_model()
    assert OllamaClient(ollama.host, "llama3").has_model()


def test_summarize_session_writes_summary_and_metadata(tmp_path, config, ollama):
    session = SessionStore(tmp_path).create("Weekly sync")
    session.meta.duration_sec = 125
    transcript.save_segments(
        session.path_for("transcript.json"),
        [Segment(0, 2, "me", "예산 얘기 해요"), Segment(3, 5, "remote", "좋아요")],
        "ko",
        "tiny",
    )
    markdown = summarize_session(session, config, client_for(ollama))

    assert markdown.startswith("# Weekly sync")
    assert "00:02:05" in markdown and "qwen3:8b" in markdown
    assert markdown.rstrip().endswith("FINAL SUMMARY")
    assert session.path_for("summary.md").read_text("utf-8") == markdown
    assert session.meta.llm_model == "qwen3:8b" and session.meta.summarized_at
    prompt = ollama.requests[0]["messages"][1]["content"]
    assert "나: 예산 얘기 해요" in prompt and "상대방: 좋아요" in prompt


def test_summarize_session_requires_transcript(tmp_path, config, ollama):
    session = SessionStore(tmp_path).create("x")
    with pytest.raises(SummarizationError, match="vecho transcribe"):
        summarize_session(session, config, client_for(ollama))


def test_korean_template_spells_out_headings():
    template = build_template("Korean")
    for heading in ("한 줄 요약", "핵심 내용", "결정 사항", "액션 아이템", "미해결 질문"):
        assert f"## {heading}" in template
    assert '"없음"' in template
    assert "translating" not in template


def test_other_languages_get_english_headings_and_translation_request():
    template = build_template("Japanese")
    assert "## Action items" in template
    assert "translating the headings" in template and "Japanese" in template
    assert "do not add a second" in template


# ---- templates ------------------------------------------------------------------------------

TEMPLATE_BODY = """## 1. 현황

- **지원 현황**
    - 사람인 628개 지원

## 2. 숙제

1. 이력서 완성

## 3. 다짐

-
"""


def test_custom_template_is_filled_conformed_and_cleaned(ollama):
    from vecho.templates import Template

    ollama.replies = [
        "```markdown\n## 2. 숙제\n\n1. 금요일까지 이력서 수정\n\n## 1. 현황\n\n- **지원 현황**\n"
        "    - 사람인 628개 지원\n    - 원티드 150개\n```"
    ]
    result = summarize_lines(
        ["[00:00:01] 상대방: 원티드로 150개 넣었어요"],
        client_for(ollama),
        "Korean",
        10000,
        template=Template("멘토링", TEMPLATE_BODY),
    )
    prompt = ollama.requests[0]["messages"][1]["content"]
    assert "Never copy it" in prompt and "사람인 628개" in prompt  # template sent as an example
    assert "628" not in result  # copied example fact removed
    assert result.index("## 1. 현황") < result.index("## 2. 숙제") < result.index("## 3. 다짐")
    assert "원티드 150개" in result and "금요일까지 이력서 수정" in result


def test_builtin_template_keeps_the_standard_summary(ollama):
    from vecho.templates import BUILTIN_NAME, Template

    summarize_lines(
        ["[00:00:01] 나: hi"],
        client_for(ollama),
        "Korean",
        1000,
        template=Template(BUILTIN_NAME, "", builtin=True),
    )
    assert "## 한 줄 요약" in ollama.requests[0]["messages"][1]["content"]


def test_summarize_session_uses_the_sessions_template(tmp_path, config, ollama):
    from vecho.templates import TemplateStore

    TemplateStore(config.templates_dir).save("멘토링", TEMPLATE_BODY)
    session = SessionStore(tmp_path).create("x")
    session.meta.template = "멘토링"
    transcript.save_segments(
        session.path_for("transcript.json"), [Segment(0, 1, "me", "hi")], "ko", "t"
    )
    ollama.replies = ["## 1. 현황\n\n- **지원 현황**\n    - 없음 확인"]
    markdown = summarize_session(session, config, client_for(ollama))
    assert "## 3. 다짐" in markdown and "· 멘토링" in markdown
    assert session.meta.template == "멘토링"


def test_a_dropped_connection_is_a_summarization_error():
    import socket
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def accept_and_close():
        conn, _ = server.accept()
        conn.recv(65536)
        conn.close()

    threading.Thread(target=accept_and_close, daemon=True).start()
    client = OllamaClient(f"http://127.0.0.1:{server.getsockname()[1]}", "m", timeout=5)
    with pytest.raises(SummarizationError, match="lost the connection"):
        client.chat("s", "u")
    server.close()


def test_notes_too_long_for_the_final_prompt_are_condensed_again(ollama):
    long_note = "n" * 60
    ollama.replies = [long_note, long_note, long_note, "short 1", "short 2", "FINAL"]
    result = summarize_lines(["x" * 70, "y" * 70, "z" * 70], client_for(ollama), "English", 80)
    assert result == "FINAL"
    final_prompt = ollama.requests[-1]["messages"][1]["content"]
    assert "short 1" in final_prompt and long_note not in final_prompt


def test_a_transcript_that_no_longer_fits_beside_the_template_goes_through_notes(ollama):
    from vecho.templates import Template

    ollama.replies = ["notes"] * 10 + ["## A\n- x"]
    template = Template("긴 템플릿", "## A\n" + "- 예시\n" * 20)  # ~100 chars
    summarize_lines(["x" * 70] * 2, client_for(ollama), "Korean", 200, template=template)
    # with the template the transcript no longer fits one prompt: notes first, from a full part
    assert "part 1 of 1" in ollama.requests[0]["messages"][1]["content"]


def test_strip_reasoning_handles_cut_off_and_half_tagged_blocks():
    assert strip_reasoning("<think>still thinking when the answer ran out") == ""
    assert strip_reasoning("reasoning without an opening tag</think>\nAnswer") == "Answer"


def test_a_reasoning_only_answer_is_an_error(ollama):
    ollama.replies = ["<think>I should summarize this.</think>"]
    with pytest.raises(SummarizationError, match="empty answer"):
        client_for(ollama).chat("system", "user")


def test_the_timeout_covers_the_whole_answer():
    """A server that keeps sending a byte now and then must not hold the job forever."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def drip():
        conn, _ = listener.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n")
            conn.sendall(b"Content-Length: 1000\r\n\r\n")
            with contextlib.suppress(OSError):
                for _ in range(40):
                    conn.sendall(b" ")
                    time.sleep(0.1)

    threading.Thread(target=drip, daemon=True).start()
    client = OllamaClient(f"http://127.0.0.1:{listener.getsockname()[1]}", "m", timeout=0.5)
    started = time.monotonic()
    with pytest.raises(SummarizationError, match="did not answer"):
        client.chat("system", "user")
    assert time.monotonic() - started < 2
    listener.close()


def test_condensing_stops_when_the_notes_do_not_get_shorter(ollama):
    ollama.replies = ["y" * 150] * 50  # a model that never shortens anything
    summarize_lines(["x" * 150] * 6, client_for(ollama), "Korean", 200)
    # 6 notes, then one condensing round that did not help, then the summary
    assert len(ollama.requests) <= 6 + 6 + 1
