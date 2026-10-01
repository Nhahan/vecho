"""Speech-to-text, run per track and merged into one timeline.

Two engines run the same Whisper model:

* ``mlx``: MLX on the Apple Silicon GPU. Roughly 9x faster than the CPU there.
* ``faster-whisper``: CTranslate2 on the CPU (or CUDA). Used everywhere else.
"""

from __future__ import annotations

import importlib.util
import math
import platform
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import transcript
from .config import Config
from .errors import TranscriptionError
from .session import TRANSCRIPT_JSON, TRANSCRIPT_MD, Session, now_iso, write_atomic
from .transcript import Segment

# (role, seconds transcribed so far, total seconds)
ProgressCallback = Callable[[str, float, float], None]
ModelFactory = Callable[[str, str], Any]


@dataclass(frozen=True)
class TrackTranscription:
    segments: list[Segment]
    language: str | None
    duration: float


def _default_model_factory(name: str, compute_type: str) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(name, device="auto", compute_type=compute_type)


# A pause this long inside one Whisper segment means the speaker stopped (typically while the
# other party talked on the other track), so the text is split there.
PAUSE_SPLIT_SEC = 0.8


def split_at_pauses(raw: Any, role: str, pause: float = PAUSE_SPLIT_SEC) -> list[Segment]:
    """Turn one Whisper segment into one or more segments, cut where the speaker paused.

    Whisper happily returns a single segment spanning half a minute of silence between two of
    your sentences. On a per-speaker track that silence is where the other side spoke, so
    keeping it would put your later sentence before their reply in the merged transcript.
    """
    words = [w for w in (getattr(raw, "words", None) or []) if w.word.strip()]
    if not words:
        text = raw.text.strip()
        return [Segment(float(raw.start), float(raw.end), role, text)] if text else []
    pieces: list[list[Any]] = [[words[0]]]
    for previous, word in zip(words, words[1:], strict=False):
        if float(word.start) - float(previous.end) >= pause:
            pieces.append([])
        pieces[-1].append(word)
    return [
        Segment(
            float(piece[0].start),
            float(piece[-1].end),
            role,
            "".join(w.word for w in piece).strip(),
        )
        for piece in pieces
    ]


class Transcriber:
    """Lazily loads a Whisper model (downloaded on first use) and transcribes audio files."""

    def __init__(
        self,
        model_name: str,
        compute_type: str = "int8",
        model_factory: ModelFactory | None = None,
    ) -> None:
        self.model_name = model_name
        self.compute_type = compute_type
        self._factory = model_factory or _default_model_factory
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                self._model = self._factory(self.model_name, self.compute_type)
            except Exception as exc:
                raise TranscriptionError(
                    f"cannot load Whisper model '{self.model_name}': {exc}"
                ) from exc
        return self._model

    def transcribe(
        self,
        path: Path,
        role: str,
        language: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TrackTranscription:
        model = self._load()
        try:
            raw_segments, info = model.transcribe(
                str(path),
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                # Feeding earlier text back in is the main cause of repetition loops.
                condition_on_previous_text=False,
                # Needed to split segments at pauses (see split_at_pauses).
                word_timestamps=True,
            )
            duration = float(getattr(info, "duration", 0.0) or 0.0)
            segments: list[Segment] = []
            for raw in raw_segments:  # lazy generator: decoding happens while iterating
                segments.extend(split_at_pauses(raw, role))
                if on_progress:
                    on_progress(role, float(raw.end), duration)
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"transcribing {path.name} failed: {exc}") from exc
        return TrackTranscription(segments, getattr(info, "language", None), duration)


SAMPLE_RATE = 16000

# faster-whisper model names -> MLX conversions on the Hugging Face hub.
MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
}


def mlx_repo(model_name: str) -> str:
    if "/" in model_name:
        return model_name  # already a hub repo or a local path
    return MLX_REPOS.get(model_name, f"mlx-community/whisper-{model_name}-mlx")


def mlx_available() -> bool:
    """Apple Silicon with mlx-whisper installed (checked without importing it: that is slow)."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False
    return importlib.util.find_spec("mlx_whisper") is not None


# Silence put between speech chunks so Whisper's word alignment sees where one ends.
CHUNK_GAP_SEC = 0.6


class SpeechTimeline:
    """Where each speech chunk sits in the joined audio and in the original recording."""

    def __init__(self, chunks: list[dict[str, int]], rate: int = SAMPLE_RATE, gap: float = 0.0):
        self.spans: list[tuple[float, float, float]] = []  # (joined start, joined end, original)
        position = 0.0
        for chunk in chunks:
            length = (chunk["end"] - chunk["start"]) / rate
            self.spans.append((position, position + length, chunk["start"] / rate))
            position += length + gap

    def to_original(self, start: float, end: float) -> tuple[float, float]:
        """Map a span of joined audio back to the recording.

        The span belongs to the chunk holding its midpoint (or the nearest one when it falls in
        a gap), and is clamped to that chunk. Mapping each end on its own would send a word
        starting right at a boundary to the previous chunk, gluing sentences said half a
        minute apart together.
        """
        middle = (start + end) / 2

        def distance(span: tuple[float, float, float]) -> float:
            if span[0] <= middle <= span[1]:
                return 0.0
            return min(abs(middle - span[0]), abs(middle - span[1]))

        joined_start, joined_end, original = min(self.spans, key=distance)
        start = min(max(start, joined_start), joined_end)
        end = min(max(end, start), joined_end)
        return round(original + start - joined_start, 2), round(original + end - joined_start, 2)


def speech_only(audio: Any, gap: float = CHUNK_GAP_SEC) -> tuple[Any, SpeechTimeline] | None:
    """Keep only the speech in ``audio`` (Silero VAD), with a map back to original times.

    Whisper invents text for silence ("감사합니다", "Thank you."), and on a per-speaker track
    one side is silent most of the time. Joining just the speech removes the silence Whisper
    would hallucinate on while keeping the sentences together for context.
    """
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    chunks = get_speech_timestamps(
        audio, VadOptions(min_silence_duration_ms=500, speech_pad_ms=400)
    )
    if not chunks:
        return None
    silence = np.zeros(int(gap * SAMPLE_RATE), dtype=audio.dtype)
    pieces = []
    for chunk in chunks:
        pieces += [audio[chunk["start"] : chunk["end"]], silence]
    joined = np.concatenate(pieces[:-1])
    return joined, SpeechTimeline(chunks, SAMPLE_RATE, gap)


class MlxTranscriber:
    """Same interface as :class:`Transcriber`, running on the Apple Silicon GPU via MLX."""

    def __init__(self, model_name: str, transcribe_fn: Callable[..., Any] | None = None) -> None:
        self.model_name = model_name
        self.repo = mlx_repo(model_name)
        self._transcribe_fn = transcribe_fn

    def _fn(self) -> Callable[..., Any]:
        if self._transcribe_fn is None:
            try:
                import mlx_whisper
            except Exception as exc:
                raise TranscriptionError(f"mlx-whisper is unavailable: {exc}") from exc
            self._transcribe_fn = mlx_whisper.transcribe
        return self._transcribe_fn

    def transcribe(
        self,
        path: Path,
        role: str,
        language: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TrackTranscription:
        try:
            from faster_whisper import decode_audio

            audio = decode_audio(str(path), sampling_rate=SAMPLE_RATE)
            duration = len(audio) / SAMPLE_RATE
            if on_progress:
                on_progress(role, 0.0, duration)
            speech = speech_only(audio)
            if speech is None:  # nothing but silence: skip the model entirely
                if on_progress:
                    on_progress(role, duration, duration)
                return TrackTranscription([], None, duration)
            joined, timeline = speech
            result = self._fn()(
                joined,
                path_or_hf_repo=self.repo,
                language=language,
                condition_on_previous_text=False,
                word_timestamps=True,
                verbose=None,
            )
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"transcribing {path.name} failed: {exc}") from exc

        segments: list[Segment] = []
        for raw in result.get("segments", []):
            words = []
            for w in raw.get("words") or []:
                start, end = timeline.to_original(float(w["start"]), float(w["end"]))
                words.append(SimpleNamespace(start=start, end=end, word=str(w["word"])))
            start, end = timeline.to_original(float(raw["start"]), float(raw["end"]))
            segments.extend(
                split_at_pauses(
                    SimpleNamespace(start=start, end=end, text=raw["text"], words=words), role
                )
            )
        if on_progress:
            on_progress(role, duration, duration)
        return TrackTranscription(segments, result.get("language"), duration)


def make_transcriber(config: Config) -> Transcriber | MlxTranscriber:
    """The fastest engine this computer supports (see ``whisper_backend`` in the config)."""
    backend = config.whisper_backend
    if backend == "mlx" or (backend == "auto" and mlx_available()):
        return MlxTranscriber(config.whisper_model)
    return Transcriber(config.whisper_model, config.whisper_compute_type)


def engine_label(config: Config) -> str:
    """Human-readable engine and model, e.g. for ``vecho doctor``."""
    engine = make_transcriber(config)
    where = (
        "mlx, Apple Silicon GPU" if isinstance(engine, MlxTranscriber) else "faster-whisper, CPU"
    )
    return f"{config.whisper_model} ({where})"


def transcribe_session(
    session: Session,
    config: Config,
    transcriber: Transcriber | MlxTranscriber | None = None,
    on_progress: ProgressCallback | None = None,
) -> list[Segment]:
    """Transcribe every track of a session and write ``transcript.json`` / ``transcript.md``."""
    if not session.meta.tracks:
        raise TranscriptionError(f"session {session.id} has no audio tracks")
    session.refresh()  # a rename made while this job waited belongs in the transcript header

    transcriber = transcriber or make_transcriber(config)
    groups: list[list[Segment]] = []
    detected: tuple[float, str | None] = (-1.0, None)
    longest = 0.0
    for role in session.meta.tracks:
        path = session.audio_path(role)
        if not path.is_file():
            raise TranscriptionError(f"audio file is missing: {path}")
        result = transcriber.transcribe(path, role, config.language, on_progress)
        shift = float(session.meta.offsets.get(role, 0.0))
        groups.append(
            [
                Segment(
                    round(max(0.0, s.start) + shift, 3),
                    round(max(s.start, s.end, 0.0) + shift, 3),
                    s.role,
                    s.text,
                )
                for s in result.segments
                if math.isfinite(s.start) and math.isfinite(s.end)  # a decoder glitch: no time
            ]
        )
        longest = max(longest, result.duration)
        # the track with the most speech decides; a silent track still "detects" a language
        speech = sum(max(0.0, s.end - s.start) for s in result.segments)
        if result.language and speech > detected[0]:
            detected = (speech, result.language)

    segments = transcript.remove_echo(transcript.merge_segments(*groups))
    language = config.language or detected[1]
    session.refresh()  # a rename made during transcription goes into the header
    # render before writing anything, so a failure leaves the previous transcript whole
    markdown = transcript.render_markdown(session.display_title, segments, config.label_for)
    transcript.save_segments(
        session.path_for(TRANSCRIPT_JSON), segments, language, transcriber.model_name
    )
    write_atomic(session.path_for(TRANSCRIPT_MD), markdown)

    meta = session.meta
    meta.language = language
    meta.whisper_model = transcriber.model_name
    meta.transcribed_at = now_iso()
    if meta.duration_sec is None and longest > 0:
        meta.duration_sec = longest
    session.save()
    return segments
