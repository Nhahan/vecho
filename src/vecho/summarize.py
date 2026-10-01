"""Conversation summaries from a local Ollama model.

Short transcripts are summarized in one call. Longer ones are split into chunks
that each fit the model's context window, condensed into notes (map), and the
notes are merged into the final summary (reduce).
"""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from . import transcript
from .config import Config
from .errors import SummarizationError
from .session import SUMMARY_MD, TRANSCRIPT_JSON, Session, now_iso, write_atomic
from .templates import (
    Template,
    TemplateStore,
    conform,
    drop_placeholders,
    remove_copied,
    strip_fences,
)

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

CONDENSE_PROMPT = """\
These are notes {index} of {total}, taken in order from one long conversation. Rewrite them
as shorter notes: keep every decision, action item (owner, deadline), number, name, date
and open question; drop repetition and small talk. Keep the order. Reply with concise bullet
points and nothing else.

Notes:
{text}"""

MAX_CONDENSE_ROUNDS = 5

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

TEMPLATE_RULES = """\
Write notes about the conversation by filling in the TEMPLATE below.

Rules:
- Keep every heading of the template with the same text, level and order. Do not add, drop,
  rename or renumber headings.
- Text after "←" in a heading is an instruction for that section: follow it, but leave it
  out of the heading you write.
- Inside each section keep the template's shape: bold labels such as "**Label**", tables
  (same columns), numbered lists and sub-bullets.
- The template may contain example content from a completely different conversation. It only
  shows the format and the level of detail. Never copy it: every fact you write must come
  from the {source} below.
- Fill in only what the {source} actually supports. When nothing fits a section, a label or
  a table, leave it empty: keep the heading or label and write nothing after it. Never write
  fillers such as "없음", "정보 없음", "(전사 기록에 해당 정보 없음)", "N/A", "-" or guesses,
  and do not add table rows you cannot fill.
- Write in {language}. Reply with the filled-in Markdown only, without a code block.

TEMPLATE:
<<<
{template}
>>>"""

FROM_TRANSCRIPT_WITH_TEMPLATE = """\
{rules}

Transcript:
{text}"""

FROM_NOTES_WITH_TEMPLATE = """\
{rules}

The notes below were extracted, in order, from consecutive parts of one long conversation.
Use them as the source and remove duplicates.

Notes:
{text}"""

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Drop ``<think>`` blocks emitted by reasoning models, even a cut-off or half-tagged one."""
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text:  # the opening tag was left out: everything before is reasoning
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:  # never closed: the answer ran out while still reasoning
        text = text.split("<think>", 1)[0]
    return text.strip()


class OllamaClient:
    def __init__(
        self,
        host: str,
        model: str,
        num_ctx: int = Config.llm_num_ctx,
        timeout: float = Config.llm_timeout,
    ) -> None:
        host = host.strip().rstrip("/")
        self.host = host if "://" in host else f"http://{host}"  # "localhost:11434" works too
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
            deadline = time.monotonic() + self.timeout  # for the whole answer, not each read
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                chunks = []
                while chunk := response.read1(1 << 16):
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise TimeoutError
                return json.loads(b"".join(chunks))
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            if exc.code == 404:
                raise SummarizationError(
                    f"Ollama has no model '{self.model}' ({detail}); "
                    f"run `ollama pull {self.model}`, or set another model (llm_model in "
                    "~/.vecho/config.toml, or --llm-model on the command line)"
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
        except (http.client.HTTPException, OSError) as exc:  # e.g. Ollama crashed mid-answer
            raise SummarizationError(f"lost the connection to Ollama: {exc}") from exc

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
        answer = strip_reasoning(content) if isinstance(content, str) else ""
        if not answer:  # checked after the reasoning is gone: never replace a summary with nothing
            raise SummarizationError("Ollama returned an empty answer; try another model")
        return answer

    def list_models(self) -> list[str]:
        data = self._request("/api/tags")
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise SummarizationError("Ollama sent an unexpected model list")
        return [str(item.get("name", "")) for item in models if isinstance(item, dict)]

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
    template: Template | None = None,
) -> str:
    if not any(line.strip() for line in lines):
        raise SummarizationError("the transcript is empty; there is nothing to summarize")

    system = SYSTEM_PROMPT.format(language=language)
    custom = template is not None and not template.builtin and template.body.strip()
    # The final prompt carries the template as well, so what goes into it gets what is left
    # of the budget (never less than a quarter of it). Notes are taken from full-size parts.
    budget = chunk_chars - (len(template.body) if custom and template is not None else 0)
    budget = max(chunk_chars // 4, budget)
    whole = transcript.split_into_chunks(lines, budget)

    def final(text: str, from_notes: bool) -> str:
        if not custom:
            prompt = FINAL_FROM_NOTES if from_notes else FINAL_FROM_TRANSCRIPT
            return client.chat(system, prompt.format(template=build_template(language), text=text))
        assert template is not None
        rules = TEMPLATE_RULES.format(
            template=template.body.strip(),
            language=language,
            source="notes" if from_notes else "transcript",
        )
        prompt = FROM_NOTES_WITH_TEMPLATE if from_notes else FROM_TRANSCRIPT_WITH_TEMPLATE
        answer = strip_fences(client.chat(system, prompt.format(rules=rules, text=text)))
        return conform(drop_placeholders(remove_copied(answer, template.body)), template.body)

    if len(whole) == 1:  # the transcript fits the final prompt as it is
        if on_progress:
            on_progress("summary", 1, 1)
        return final(whole[0], from_notes=False)

    chunks = transcript.split_into_chunks(lines, chunk_chars)

    notes = []
    for number, chunk in enumerate(chunks, start=1):
        if on_progress:
            on_progress("notes", number, len(chunks))
        notes.append(
            client.chat(system, CHUNK_PROMPT.format(index=number, total=len(chunks), text=chunk))
        )
    joined = "\n\n".join(f"### Part {i}\n{note}" for i, note in enumerate(notes, start=1))
    # Very long conversations: condense the notes until they fit the final prompt, as long as
    # condensing still makes them meaningfully shorter.
    rounds = 0
    while len(joined) > budget and rounds < MAX_CONDENSE_ROUNDS:
        rounds += 1
        groups = transcript.split_into_chunks(joined.splitlines(), chunk_chars)
        notes = [
            client.chat(system, CONDENSE_PROMPT.format(index=i, total=len(groups), text=group))
            for i, group in enumerate(groups, start=1)
        ]
        shorter = "\n\n".join(f"### Part {i}\n{note}" for i, note in enumerate(notes, start=1))
        if len(shorter) > 0.9 * len(joined):
            joined = shorter
            break  # the model cannot condense further; send what there is
        joined = shorter
    if on_progress:
        on_progress("summary", len(chunks), len(chunks))
    return final(joined, from_notes=True)


def summarize_session(
    session: Session,
    config: Config,
    client: OllamaClient | None = None,
    on_progress: ProgressCallback | None = None,
    template: str | None = None,
) -> str:
    """Summarize a transcribed session into ``summary.md`` and return the Markdown.

    The template is ``template`` if given, else the one chosen for the session, else the
    default template.
    """
    if not session.has_transcript:
        raise SummarizationError(
            f"session {session.id} has no transcript; run `vecho transcribe {session.id}` first"
        )
    session.refresh()  # a rename (or template choice) made while this job waited
    client = client or OllamaClient(
        config.llm_host, config.llm_model, config.llm_num_ctx, config.llm_timeout
    )
    segments = transcript.load_segments(session.path_for(TRANSCRIPT_JSON))
    lines = transcript.render_lines(segments, config.label_for)
    store = TemplateStore(config.templates_dir)
    chosen = store.get(template or session.meta.template or store.default_name())
    body = summarize_lines(
        lines, client, config.summary_language, config.chunk_chars, on_progress, chosen
    )

    session.refresh()  # a rename made while the model was writing goes into the header
    meta = session.meta
    meta.template = chosen.name
    meta.summary_template = chosen.name  # what summary.md was actually made with
    meta.llm_model = client.model
    meta.summarized_at = now_iso()
    header = (
        f"# {session.display_title}\n\n"
        f"> {meta.created_at} · {transcript.format_duration(meta.duration_sec)} · {client.model}"
        f" · {chosen.name}\n"
    )
    markdown = f"{header}\n{body}\n"
    write_atomic(session.path_for(SUMMARY_MD), markdown)
    session.save()
    return markdown
