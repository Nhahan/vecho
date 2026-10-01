"""Background transcription and summarization for the app, one session at a time.

Models are large, so jobs run sequentially on a single worker thread and the Whisper model
stays loaded between jobs.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from . import transcript
from .config import Config
from .errors import VechoError
from .session import SUMMARY_MD, TRANSCRIPT_JSON, Session
from .summarize import summarize_session
from .transcribe import MlxTranscriber, Transcriber, make_transcriber, transcribe_session

QUEUED = "queued"
TRANSCRIBING = "transcribing"
SUMMARIZING = "summarizing"
DONE = "done"
ERROR = "error"

ACTIVE = (QUEUED, TRANSCRIBING, SUMMARIZING)

STEPS = ("all", "transcribe", "summarize")


@dataclass
class JobState:
    session_id: str
    step: str
    stage: str = QUEUED
    progress: float = 0.0  # 0..1 within the current stage
    error: str = ""
    finished_at: float | None = None

    @property
    def active(self) -> bool:
        return self.stage in ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def has_speech(session: Session) -> bool:
    path = session.path_for(TRANSCRIPT_JSON)
    try:
        return path.is_file() and bool(transcript.load_segments(path))
    except VechoError:
        return False


def _forget_summary(session: Session) -> None:
    session.path_for(SUMMARY_MD).unlink(missing_ok=True)
    if session.meta.summarized_at or session.meta.summary_template:
        session.meta.summarized_at = None
        session.meta.summary_template = None
        session.save()


class Processor:
    def __init__(
        self,
        config: Config,
        transcribe: Callable[..., Any] = transcribe_session,
        summarize: Callable[..., Any] = summarize_session,
        on_finish: Callable[[JobState], None] | None = None,
    ) -> None:
        self.config = config
        self._transcribe = transcribe
        self._summarize = summarize
        self._on_finish = on_finish
        self._transcriber: Transcriber | MlxTranscriber | None = None
        self._states: dict[str, JobState] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[Session, JobState] | None] = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="vecho-jobs", daemon=True)
        self._worker.start()

    def submit(self, session: Session, step: str = "all", template: str | None = None) -> JobState:
        """Queue processing; ``template`` (a template name) is remembered for the session."""
        if step not in STEPS:
            raise VechoError(f"unknown processing step '{step}'")
        with self._lock:
            current = self._states.get(session.id)
            if current is not None and current.active:
                return current  # already queued or running: its template stays as it was
            state = JobState(session.id, step)
            self._states[session.id] = state
        try:
            if template is not None and template != session.meta.template:
                session.meta.template = template
                session.save()
        except BaseException:
            with self._lock:  # never leave a job that will not run marked as active
                if self._states.get(session.id) is state:
                    del self._states[session.id]
            raise
        self._queue.put((session, state))
        return state

    def state(self, session_id: str) -> JobState | None:
        with self._lock:
            return self._states.get(session_id)

    def forget(self, session_id: str) -> None:
        with self._lock:
            state = self._states.get(session_id)
            if state is not None and not state.active:
                del self._states[session_id]

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {sid: state.to_dict() for sid, state in self._states.items()}

    def is_busy(self, session_id: str) -> bool:
        state = self.state(session_id)
        return state is not None and state.active

    def shutdown(self) -> None:
        self._queue.put(None)

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """For tests and orderly shutdown: wait until no job is queued or running."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not any(state.active for state in self._states.values()):
                    return True
            time.sleep(0.02)
        return False

    # -- worker ---------------------------------------------------------------------------

    def _set(self, state: JobState, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(state, key, value)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            session, state = item
            try:
                self._process(session, state)
                self._set(state, stage=DONE, progress=1.0, finished_at=time.time())
            except VechoError as exc:
                self._set(state, stage=ERROR, error=str(exc), finished_at=time.time())
            except Exception as exc:  # never let one bad file kill the worker
                self._set(
                    state, stage=ERROR, error=f"unexpected error: {exc}", finished_at=time.time()
                )
            if self._on_finish is not None:
                with contextlib.suppress(Exception):  # notifications are best-effort
                    self._on_finish(state)

    def _process(self, session: Session, state: JobState) -> None:
        if state.step in ("all", "transcribe"):
            self._set(state, stage=TRANSCRIBING, progress=0.0)
            order = list(session.meta.tracks)

            def transcribe_progress(role: str, position: float, total: float) -> None:
                index = order.index(role) if role in order else 0
                within = min(1.0, position / total) if total else 0.0
                self._set(state, progress=(index + within) / max(1, len(order)))

            if (
                self._transcriber is None
                or self._transcriber.model_name != self.config.whisper_model
            ):
                self._transcriber = make_transcriber(self.config)
            self._transcribe(
                session,
                self.config,
                transcriber=self._transcriber,
                on_progress=transcribe_progress,
            )
        if state.step == "all" and not has_speech(session):
            _forget_summary(session)  # nothing was said: an older summary no longer applies
            return
        if state.step in ("all", "summarize"):
            self._set(state, stage=SUMMARIZING, progress=0.0)

            def summary_progress(stage: str, step: int, total: int) -> None:
                done = step - 1 if stage == "notes" else total
                self._set(state, progress=done / (total + 1))

            self._summarize(session, self.config, on_progress=summary_progress)
