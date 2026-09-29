"""Transcript data model, merging of per-track segments, rendering and chunking."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from . import roles
from .errors import SessionError
from .session import write_atomic

LabelFor = Callable[[str], str]

# Same-speaker segments closer together than this read as one utterance.
COALESCE_GAP_SEC = 1.5


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    role: str
    text: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        return cls(
            start=float(data["start"]),
            end=float(data["end"]),
            role=str(data["role"]),
            text=str(data["text"]),
        )


def merge_segments(*groups: Iterable[Segment]) -> list[Segment]:
    """Interleave segments from several tracks into one timeline."""
    merged = [segment for group in groups for segment in group]
    merged.sort(key=lambda s: (s.start, s.end, s.role))
    return merged


# Speaker bleed: without headphones the microphone re-records the other party's voice.
ECHO_WINDOW_SEC = 3.0
# Share of the mic text found in runs of 3+ characters. Measured on Korean speech: echoes
# with recognition differences score >= 0.80, genuine short replies <= 0.71.
ECHO_COVERAGE = 0.75
ECHO_MIN_RUN = 3
ECHO_MIN_CHARS = 6


def _normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


def _is_echo(segment: Segment, remote: Sequence[Segment]) -> bool:
    mine = _normalize(segment.text)
    if len(mine) < ECHO_MIN_CHARS:
        return False  # too short to tell an echo from a genuine "yes" or "okay"
    nearby = [
        r.text
        for r in remote
        if r.start <= segment.end + ECHO_WINDOW_SEC and r.end >= segment.start - ECHO_WINDOW_SEC
    ]
    heard = _normalize("".join(nearby))
    if not heard:
        return False
    blocks = SequenceMatcher(None, mine, heard, autojunk=False).get_matching_blocks()
    # Scattered one- or two-letter matches are just shared words ("일정", "예산"); an echo
    # repeats long runs of what was heard.
    matched = sum(block.size for block in blocks if block.size >= ECHO_MIN_RUN)
    return matched / len(mine) >= ECHO_COVERAGE


def remove_echo(segments: Sequence[Segment]) -> list[Segment]:
    """Drop microphone segments that merely repeat what the remote track said at that moment.

    The remote (loopback) track is a clean digital copy, so it wins. Coverage rather than
    whole-string similarity is used so that differing segment boundaries do not hide an echo.
    """
    remote = [s for s in segments if s.role == roles.REMOTE]
    if not remote:
        return list(segments)
    return [s for s in segments if not (s.role == roles.ME and _is_echo(s, remote))]


def coalesce(segments: Sequence[Segment], max_gap: float = COALESCE_GAP_SEC) -> list[Segment]:
    """Join consecutive same-speaker segments that follow each other closely."""
    result: list[Segment] = []
    for segment in segments:
        previous = result[-1] if result else None
        if previous and previous.role == segment.role and segment.start - previous.end <= max_gap:
            result[-1] = Segment(
                previous.start,
                max(previous.end, segment.end),
                previous.role,
                f"{previous.text} {segment.text}",
            )
        else:
            result.append(segment)
    return result


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_duration(seconds: float | None) -> str:
    return "unknown" if seconds is None else format_timestamp(seconds)


def render_lines(segments: Sequence[Segment], label_for: LabelFor) -> list[str]:
    """One ``[HH:MM:SS] Speaker: text`` line per utterance; used for the LLM and for humans."""
    lines = []
    for segment in coalesce(segments):
        label = label_for(segment.role)
        speaker = f"{label}: " if label else ""
        lines.append(f"[{format_timestamp(segment.start)}] {speaker}{segment.text}")
    return lines


def render_markdown(title: str, segments: Sequence[Segment], label_for: LabelFor) -> str:
    body = "\n\n".join(render_lines(segments, label_for)) or "_No speech detected._"
    return f"# {title} — Transcript\n\n{body}\n"


def split_into_chunks(lines: Sequence[str], max_chars: int) -> list[str]:
    """Group lines into chunks of at most ``max_chars`` so each fits the LLM context.

    Lines are never split across chunks unless a single line alone is too long.
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        pieces = [line[i : i + max_chars] for i in range(0, len(line), max_chars)] or [""]
        for piece in pieces:
            if current and size + len(piece) + 1 > max_chars:
                chunks.append("\n".join(current))
                current, size = [], 0
            current.append(piece)
            size += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def save_segments(
    path: Path, segments: Sequence[Segment], language: str | None, model: str
) -> None:
    payload = {
        "language": language,
        "model": model,
        "segments": [asdict(segment) for segment in segments],
    }
    write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def load_segments(path: Path) -> list[Segment]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Segment.from_dict(item) for item in data["segments"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SessionError(f"cannot read transcript {path}: {exc}") from exc
