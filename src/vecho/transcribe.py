"""Speech-to-text, run per track and merged into one timeline.

Two engines run the same Whisper model:

* ``mlx``: MLX on the Apple Silicon GPU. Roughly 9x faster than the CPU there.
* ``faster-whisper``: CTranslate2 on the CPU (or CUDA). Used everywhere else.
"""

from __future__ import annotations

import importlib.util
import math
import platform
import wave
from collections.abc import Callable, Iterator
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
        speech_filter: bool = True,
    ) -> None:
        self.model_name = model_name
        self.compute_type = compute_type
        self._factory = model_factory or _default_model_factory
        self._model: Any = None
        # Our own speech joining (see speech_windows); faster-whisper's built-in VAD joins
        # chunks without a gap, which detaches a sentence's first word from it.
        self._speech_filter = speech_filter

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
        if self._speech_filter:
            return self._transcribe_speech(model, path, role, language, on_progress)
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

    def _transcribe_speech(
        self,
        model: Any,
        path: Path,
        role: str,
        language: str | None,
        on_progress: ProgressCallback | None,
    ) -> TrackTranscription:
        try:
            duration = _duration(path)
            if on_progress:
                on_progress(role, 0.0, duration)
            windows = speech_windows(path)
            segments: list[Segment] = []
            detected = language
            for joined, timeline in windows:
                raw_segments, info = model.transcribe(
                    joined,
                    language=detected,
                    beam_size=5,
                    vad_filter=False,  # only speech is left
                    condition_on_previous_text=False,
                    word_timestamps=True,
                )
                for raw in raw_segments:
                    words = [(float(w.start), float(w.end), w.word) for w in raw.words or []]
                    segments += _placed(timeline, raw.start, raw.end, raw.text, words, role)
                detected = detected or getattr(info, "language", None)
                if on_progress:
                    on_progress(role, min(timeline.reached, duration), duration)
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"transcribing {path.name} failed: {exc}") from exc
        if on_progress:
            on_progress(role, duration, duration)
        return TrackTranscription(segments, detected if windows else None, duration)


def _placed(
    timeline: SpeechTimeline,
    start: float,
    end: float,
    text: str,
    words: list[tuple[float, float, str]],
    role: str,
) -> list[Segment]:
    """One recognized segment of joined speech, mapped back to the recording."""
    placed = timeline.place_words(words)
    if placed:
        first, last = placed[0].start, placed[-1].end
    else:
        first, last = timeline.to_original(float(start), float(end))
    return split_at_pauses(SimpleNamespace(start=first, end=last, text=text, words=placed), role)


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


# Silence put between speech chunks so Whisper's word alignment sees where one ends. Whisper
# often times the first word of a chunk up to ~1.3 s early; a shorter gap let that word land
# in the previous chunk, detached from its sentence and placed seconds to minutes too early.
CHUNK_GAP_SEC = 1.5
# Long recordings are transcribed in windows of joined speech, which also gives progress.
WINDOW_SEC = 600.0


class SpeechTimeline:
    """Where each speech chunk sits in the joined audio and in the original recording."""

    def __init__(self, chunks: list[dict[str, int]], rate: int = SAMPLE_RATE, gap: float = 0.0):
        self.spans: list[tuple[float, float, float]] = []  # (joined start, joined end, original)
        position = 0.0
        for chunk in chunks:
            length = (chunk["end"] - chunk["start"]) / rate
            self.spans.append((position, position + length, chunk["start"] / rate))
            position += length + gap

    @property
    def reached(self) -> float:
        """Where in the recording the last chunk ends."""
        joined_start, joined_end, original = self.spans[-1]
        return original + joined_end - joined_start

    def chunk_of(self, start: float, end: float) -> tuple[int, bool]:
        """The chunk holding the span's midpoint (or the nearest one), and whether it was in a gap.

        Mapping each end on its own would send a word starting right at a boundary to the
        previous chunk, gluing sentences said half a minute apart together.
        """
        middle = (start + end) / 2

        def distance(index: int) -> float:
            span = self.spans[index]
            if span[0] <= middle <= span[1]:
                return 0.0
            return min(abs(middle - span[0]), abs(middle - span[1]))

        index = min(range(len(self.spans)), key=distance)
        return index, distance(index) > 0

    def to_original(
        self, start: float, end: float, chunk: int | None = None
    ) -> tuple[float, float]:
        """Map a span of joined audio back to the recording, clamped to its chunk."""
        if chunk is None:
            chunk = self.chunk_of(start, end)[0]
        joined_start, joined_end, original = self.spans[chunk]
        start = min(max(start, joined_start), joined_end)
        end = min(max(end, start), joined_end)
        return round(original + start - joined_start, 2), round(original + end - joined_start, 2)

    def place_words(self, words: list[tuple[float, float, str]]) -> list[SimpleNamespace]:
        """Map words back to the recording, keeping a sentence's first word with its sentence.

        A word whose midpoint falls in the silence between two chunks is an opener timed too
        early when the next word starts the following chunk; otherwise it ends the previous one.
        """
        chunks = [self.chunk_of(start, end) for start, end, _ in words]
        placed = []
        for i, ((start, end, text), (chunk, in_gap)) in enumerate(zip(words, chunks, strict=True)):
            if in_gap and i + 1 < len(words) and chunks[i + 1][0] > chunk:
                chunk = chunks[i + 1][0]
            first, last = self.to_original(start, end, chunk)
            placed.append(SimpleNamespace(start=first, end=last, word=text))
        return placed


VAD_BLOCK_SEC = 600.0  # audio is read and searched for speech in blocks this long


def _audio_blocks(source: Any, seconds: float = VAD_BLOCK_SEC) -> Iterator[Any]:
    """16 kHz float32 audio in blocks: from an array, a 16 kHz mono WAV (read as it goes) or
    any other file (decoded first)."""
    import numpy as np

    size = int(seconds * SAMPLE_RATE)
    if isinstance(source, np.ndarray):
        for first in range(0, len(source), size):
            yield source[first : first + size]
        return
    path = Path(source)
    try:
        with wave.open(str(path)) as wav:
            plain = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (
                SAMPLE_RATE,
                1,
                2,
            )
            while plain:
                frames = wav.readframes(size)
                if not frames:
                    return
                yield np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    except (wave.Error, EOFError):
        pass  # not a plain WAV (an imported recording): decode it as a whole
    from faster_whisper import decode_audio

    yield from _audio_blocks(decode_audio(str(path), sampling_rate=SAMPLE_RATE), seconds)


def _detect_speech(audio: Any) -> list[dict[str, int]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=500, speech_pad_ms=400))


def speech_windows(
    source: Any, gap: float = CHUNK_GAP_SEC, window: float = WINDOW_SEC
) -> list[tuple[Any, SpeechTimeline]]:
    """Only the speech in ``source`` (Silero VAD), joined into windows, with maps back to time.

    Whisper invents text for silence ("감사합니다", "Thank you."), and on a per-speaker track
    one side is silent most of the time. Joining just the speech removes the silence Whisper
    would hallucinate on while keeping the sentences together for context. Each window holds
    about ``window`` seconds of speech (whole chunks), so long recordings report progress.

    ``source`` is an array or an audio file; files are searched block by block and only the
    speech is kept, so memory does not grow with the length of the recording.
    """
    import numpy as np

    found: list[tuple[dict[str, int], Any]] = []  # (chunk in original samples, its audio)
    carry = np.zeros(0, dtype=np.float32)  # speech running into the next block
    offset = 0  # original position of the current block's first sample
    edge = int(0.5 * SAMPLE_RATE)
    blocks = _audio_blocks(source)
    block = next(blocks, None)
    while block is not None:
        upcoming = next(blocks, None)
        audio = np.concatenate([carry, block]) if len(carry) else block
        chunks = _detect_speech(audio) if len(audio) else []
        carry = np.zeros(0, dtype=np.float32)
        if (
            upcoming is not None
            and chunks
            and chunks[-1]["end"] >= len(audio) - edge
            and chunks[-1]["start"] > 0  # never carry a whole block on
        ):
            last = chunks.pop()  # unfinished: searched again together with the next block
            carry = audio[last["start"] :].copy()
        for chunk in chunks:
            found.append(
                (
                    {"start": offset + chunk["start"], "end": offset + chunk["end"]},
                    audio[chunk["start"] : chunk["end"]].copy(),
                )
            )
        offset += len(audio) - len(carry)
        block = upcoming

    groups: list[list[tuple[dict[str, int], Any]]] = []
    length = 0.0
    for chunk, audio in found:
        size = len(audio) / SAMPLE_RATE + gap
        if not groups or length + size > window:
            groups.append([])
            length = 0.0
        groups[-1].append((chunk, audio))
        length += size
    silence = np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32)
    windows = []
    for group in groups:
        pieces = []
        for _, audio in group:
            pieces += [audio, silence]
        timeline = SpeechTimeline([chunk for chunk, _ in group], SAMPLE_RATE, gap)
        windows.append((np.concatenate(pieces[:-1]), timeline))
    return windows


def speech_only(audio: Any, gap: float = CHUNK_GAP_SEC) -> tuple[Any, SpeechTimeline] | None:
    """All speech in ``audio`` as one window (see :func:`speech_windows`)."""
    windows = speech_windows(audio, gap, window=float("inf"))
    return windows[0] if windows else None


def _duration(path: Path) -> float:
    try:
        with wave.open(str(path)) as wav:
            return wav.getnframes() / wav.getframerate()
    except (wave.Error, EOFError, OSError):
        pass
    try:  # an imported recording: its container knows (decoding it just for this is costly)
        import av

        with av.open(str(path)) as container:
            if container.duration:
                return container.duration / 1_000_000
    except Exception:
        pass
    from faster_whisper.audio import decode_audio

    return len(decode_audio(str(path), sampling_rate=SAMPLE_RATE)) / SAMPLE_RATE


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
            duration = _duration(path)
            if on_progress:
                on_progress(role, 0.0, duration)
            windows = speech_windows(path)
            segments: list[Segment] = []
            detected = language
            for joined, timeline in windows:
                result = self._fn()(
                    joined,
                    path_or_hf_repo=self.repo,
                    language=detected,
                    condition_on_previous_text=False,
                    word_timestamps=True,
                    verbose=None,
                )
                detected = detected or result.get("language")  # the same for every window
                segments += self._segments(result, timeline, role)
                if on_progress:
                    on_progress(role, min(timeline.reached, duration), duration)
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"transcribing {path.name} failed: {exc}") from exc
        if on_progress:
            on_progress(role, duration, duration)
        return TrackTranscription(segments, detected if windows else None, duration)

    @staticmethod
    def _segments(result: dict[str, Any], timeline: SpeechTimeline, role: str) -> list[Segment]:
        segments: list[Segment] = []
        for raw in result.get("segments", []):
            words = [
                (float(w["start"]), float(w["end"]), str(w["word"])) for w in raw.get("words") or []
            ]
            segments += _placed(timeline, raw["start"], raw["end"], raw["text"], words, role)
        return segments


def make_transcriber(config: Config) -> Transcriber | MlxTranscriber:
    """The fastest engine this computer supports (see ``whisper_backend`` in the config)."""
    backend = config.whisper_backend
    if backend == "mlx" or (backend == "auto" and mlx_available()):
        return MlxTranscriber(config.whisper_model)
    return Transcriber(config.whisper_model, config.whisper_compute_type)


def model_cached(config: Config) -> bool:
    """Whether the speech model is already on disk (the first run downloads ~1.5 GB)."""
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        engine = make_transcriber(config)
        if isinstance(engine, MlxTranscriber):
            repo = engine.repo
        else:
            from faster_whisper.utils import _MODELS

            repo = _MODELS.get(config.whisper_model, config.whisper_model)
        if Path(repo).exists():  # a local model folder
            return True
        snapshots = Path(HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "snapshots"
        return any(snapshots.iterdir())
    except Exception:
        return True  # unknown: better not to promise a download that will not happen


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
