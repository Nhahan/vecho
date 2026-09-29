"""Conversation summaries from a local Ollama model.

Short transcripts are summarized in one call. Longer ones are split into chunks
that each fit the model's context window, condensed into notes (map), and the
notes are merged into the final summary (reduce).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from . import transcript
from .config import Config
from .errors import SummarizationError
from .session import SUMMARY_MD, TRANSCRIPT_JSON, Session, now_iso, write_atomic

# (stage, step, total steps)
ProgressCallback = Callable[[str, int, int], None]

SYSTEM_PROMPT = """\
You are a careful note-taker for spoken conversations.
You receive an automatic transcript of a two-way voice conversation. Each line reads
"[HH:MM:SS] Speaker: text". The transcript may contain recognition errors, so read
charitably but never invent facts, names, numbers or commitments that are not in it.
Attribute statements to the right speaker whenever it matters.
Write everything in {language}."""

CHUNK_PROMPT = """\
This is part {index} of {total} of a longer conversation. Extract detailed notes from
this part only: topics discussed, key statements, decisions, action items (with owner
and deadline when stated) and unresolved questions. Keep names, numbers and dates exact.
Reply with concise bullet points and nothing else.

Transcript part:
{text}"""

# Section headings and the "nothing" word per language. Models often ignore an instruction to
# translate headings, so known languages get them spelled out; others fall back to English
# headings plus a translation request.
_ENGLISH = ("TL;DR", "Key points", "Decisions", "Action items", "Open questions", "None")
LOCALIZED_HEADINGS = {
    "korean": ("한 줄 요약", "핵심 내용", "결정 사항", "액션 아이템", "미해결 질문", "없음"),
}

FINAL_TEMPLATE = """\
Use exactly these Markdown sections in this order{translate_note}:

## {tldr}
One or two sentences.

## {key_points}
Bullets covering the main topics and what was said about each.

## {decisions}
Bullets for decisions that were made. Write "{none}" if there were none.

## {actions}
One line per item in the form "- [ ] task — owner (deadline if mentioned)"; do not add a second
bullet marker. Write "{none}" if there were none.

## {questions}
Bullets for unresolved questions or risks. Write "{none}" if there were none."""


def build_template(language: str) -> str:
    headings = LOCALIZED_HEADINGS.get(language.strip().lower())
    tldr, key_points, decisions, actions, questions, none = headings or _ENGLISH
    note = "" if headings else f', translating the headings and the word "None" into {language}'
    return FINAL_TEMPLATE.format(
        translate_note=note,
        tldr=tldr,
        key_points=key_points,
        decisions=decisions,
        actions=actions,
        questions=questions,
        none=none,
    )


FINAL_FROM_TRANSCRIPT = """\
Summarize the conversation below.

{template}

Transcript:
{text}"""

FINAL_FROM_NOTES = """\
The notes below were extracted, in order, from consecutive parts of one long conversation.
Merge them into a single coherent summary and remove duplicates.

{template}

Notes:
{text}"""

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Drop ``<think>`` blocks emitted by reasoning models."""
    return _THINK_BLOCK.sub("", text).strip()


class OllamaClient:
    def __init__(
        self,
        host: str,
        model: str,
        num_ctx: int = Config.llm_num_ctx,
        timeout: float = Config.llm_timeout,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.timeout = timeout

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.host + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            if exc.code == 404:
                raise SummarizationError(
                    f"Ollama has no model '{self.model}' ({detail}); "
                    f"run `ollama pull {self.model}` or choose another with --llm-model"
                ) from exc
            raise SummarizationError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SummarizationError(
                f"cannot reach Ollama at {self.host} ({exc.reason}); start it with `ollama serve`"
            ) from exc
        except TimeoutError as exc:
            raise SummarizationError(
                f"Ollama did not answer within {self.timeout:.0f}s; "
                "try a smaller model or raise VECHO_LLM_TIMEOUT"
            ) from exc
        except json.JSONDecodeError as exc:
            raise SummarizationError(f"Ollama sent an unreadable response: {exc}") from exc

    def chat(self, system: str, user: str) -> str:
        data = self._request(
            "/api/chat",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "think": False,
                "options": {"num_ctx": self.num_ctx, "temperature": 0.2},
            },
        )
        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise SummarizationError("Ollama response has no message content") from exc
        if not isinstance(content, str) or not content.strip():
            raise SummarizationError("Ollama returned an empty answer; try another model")
        return strip_reasoning(content)

    def list_models(self) -> list[str]:
        data = self._request("/api/tags")
        return [str(item.get("name", "")) for item in data.get("models", [])]

    def has_model(self) -> bool:
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        return wanted in self.list_models()


def _error_detail(exc: urllib.error.HTTPError) -> str:
    raw = exc.read().decode("utf-8", "replace")
    try:
        return str(json.loads(raw).get("error", raw))[:300]
    except (ValueError, AttributeError):
        return raw[:300]


def summarize_lines(
    lines: list[str],
    client: OllamaClient,
    language: str,
    chunk_chars: int,
    on_progress: ProgressCallback | None = None,
) -> str:
    if not any(line.strip() for line in lines):
        raise SummarizationError("the transcript is empty; there is nothing to summarize")

    system = SYSTEM_PROMPT.format(language=language)
    template = build_template(language)
    chunks = transcript.split_into_chunks(lines, chunk_chars)

    if len(chunks) == 1:
        if on_progress:
            on_progress("summary", 1, 1)
        return client.chat(system, FINAL_FROM_TRANSCRIPT.format(template=template, text=chunks[0]))

    notes = []
    for number, chunk in enumerate(chunks, start=1):
        if on_progress:
            on_progress("notes", number, len(chunks))
        notes.append(
            client.chat(system, CHUNK_PROMPT.format(index=number, total=len(chunks), text=chunk))
        )
    if on_progress:
        on_progress("summary", len(chunks), len(chunks))
    joined = "\n\n".join(f"### Part {i}\n{note}" for i, note in enumerate(notes, start=1))
    return client.chat(system, FINAL_FROM_NOTES.format(template=template, text=joined))


def summarize_session(
    session: Session,
    config: Config,
    client: OllamaClient | None = None,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Summarize a transcribed session into ``summary.md`` and return the Markdown."""
    if not session.has_transcript:
        raise SummarizationError(
            f"session {session.id} has no transcript; run `vecho transcribe {session.id}` first"
        )
    client = client or OllamaClient(
        config.llm_host, config.llm_model, config.llm_num_ctx, config.llm_timeout
    )
    segments = transcript.load_segments(session.path_for(TRANSCRIPT_JSON))
    lines = transcript.render_lines(segments, config.label_for)
    body = summarize_lines(lines, client, config.summary_language, config.chunk_chars, on_progress)

    meta = session.meta
    meta.llm_model = client.model
    meta.summarized_at = now_iso()
    header = (
        f"# {session.display_title}\n\n"
        f"> {meta.created_at} · {transcript.format_duration(meta.duration_sec)} · {client.model}\n"
    )
    markdown = f"{header}\n{body}\n"
    write_atomic(session.path_for(SUMMARY_MD), markdown)
    session.save()
    return markdown
