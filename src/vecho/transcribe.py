"""Speech-to-text with faster-whisper, run per track and merged into one timeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import transcript
from .config import Config
from .errors import TranscriptionError
from .session import TRANSCRIPT_JSON, TRANSCRIPT_MD, Session, now_iso
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
            )
            duration = float(getattr(info, "duration", 0.0) or 0.0)
            segments: list[Segment] = []
            for raw in raw_segments:  # lazy generator: decoding happens while iterating
                text = raw.text.strip()
                if text:
                    segments.append(Segment(float(raw.start), float(raw.end), role, text))
                if on_progress:
                    on_progress(role, float(raw.end), duration)
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"transcribing {path.name} failed: {exc}") from exc
        return TrackTranscription(segments, getattr(info, "language", None), duration)


def transcribe_session(
    session: Session,
    config: Config,
    transcriber: Transcriber | None = None,
    on_progress: ProgressCallback | None = None,
) -> list[Segment]:
    """Transcribe every track of a session and write ``transcript.json`` / ``transcript.md``."""
    if not session.meta.tracks:
        raise TranscriptionError(f"session {session.id} has no audio tracks")

    transcriber = transcriber or Transcriber(config.whisper_model, config.whisper_compute_type)
    groups: list[list[Segment]] = []
    detected: tuple[float, str | None] = (-1.0, None)
    longest = 0.0
    for role in session.meta.tracks:
        path = session.audio_path(role)
        if not path.is_file():
            raise TranscriptionError(f"audio file is missing: {path}")
        result = transcriber.transcribe(path, role, config.language, on_progress)
        groups.append(result.segments)
        longest = max(longest, result.duration)
        if result.language and result.duration > detected[0]:
            detected = (result.duration, result.language)

    segments = transcript.remove_echo(transcript.merge_segments(*groups))
    language = config.language or detected[1]
    transcript.save_segments(
        session.path_for(TRANSCRIPT_JSON), segments, language, transcriber.model_name
    )
    session.path_for(TRANSCRIPT_MD).write_text(
        transcript.render_markdown(session.display_title, segments, config.label_for),
        encoding="utf-8",
    )

    meta = session.meta
    meta.language = language
    meta.whisper_model = transcriber.model_name
    meta.transcribed_at = now_iso()
    if meta.duration_sec is None and longest > 0:
        meta.duration_sec = longest
    session.save()
    return segments
