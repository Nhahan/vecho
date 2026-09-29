import json
from types import SimpleNamespace

import pytest

from vecho.errors import TranscriptionError
from vecho.session import SessionStore
from vecho.transcribe import Transcriber, transcribe_session


class FakeModel:
    def __init__(self, scripts, language="ko", duration=10.0):
        self.scripts = scripts  # file name -> [(start, end, text)]
        self.language = language
        self.duration = duration
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        name = path.rsplit("/", 1)[-1]
        segments = (SimpleNamespace(start=s, end=e, text=t) for s, e, t in self.scripts[name])
        return segments, SimpleNamespace(language=self.language, duration=self.duration)


def make_session(tmp_path, tracks):
    session = SessionStore(tmp_path).create("Sync")
    for role, name in tracks.items():
        (session.dir / name).write_bytes(b"audio")
        session.meta.tracks[role] = name
    session.save()
    return session


def transcriber_for(model):
    return Transcriber("tiny", "int8", model_factory=lambda name, compute: model)


def test_transcribe_session_merges_tracks_and_writes_artifacts(tmp_path, config):
    session = make_session(tmp_path, {"me": "me.wav", "remote": "remote.wav"})
    model = FakeModel(
        {
            "me.wav": [(0.0, 2.0, " 안녕하세요 "), (8.0, 9.0, "감사합니다")],
            "remote.wav": [(3.0, 5.0, "네 안녕하세요"), (5.0, 5.5, "   ")],
        }
    )
    segments = transcribe_session(session, config, transcriber_for(model))

    assert [(s.role, s.text) for s in segments] == [
        ("me", "안녕하세요"),
        ("remote", "네 안녕하세요"),
        ("me", "감사합니다"),
    ]
    data = json.loads(session.path_for("transcript.json").read_text("utf-8"))
    assert data["language"] == "ko" and data["model"] == "tiny"
    markdown = session.path_for("transcript.md").read_text("utf-8")
    assert "[00:00:03] 상대방: 네 안녕하세요" in markdown

    assert session.meta.language == "ko"
    assert session.meta.whisper_model == "tiny"
    assert session.meta.transcribed_at
    assert session.meta.duration_sec == 10.0


def test_transcribe_passes_vad_and_language(tmp_path, config):
    session = make_session(tmp_path, {"me": "me.wav"})
    model = FakeModel({"me.wav": [(0, 1, "hi")]})
    transcribe_session(session, config.with_overrides(language="en"), transcriber_for(model))
    kwargs = model.calls[0][1]
    assert kwargs["language"] == "en"
    assert kwargs["vad_filter"] is True
    assert kwargs["condition_on_previous_text"] is False


def test_recorded_duration_is_not_overwritten(tmp_path, config):
    session = make_session(tmp_path, {"me": "me.wav"})
    session.meta.duration_sec = 99.0
    model = FakeModel({"me.wav": [(0, 1, "hi")]}, duration=10.0)
    transcribe_session(session, config, transcriber_for(model))
    assert session.meta.duration_sec == 99.0


def test_progress_callback_receives_positions(tmp_path, config):
    session = make_session(tmp_path, {"me": "me.wav"})
    model = FakeModel({"me.wav": [(0, 4, "a"), (4, 8, "b")]}, duration=8.0)
    seen = []
    transcribe_session(
        session, config, transcriber_for(model), on_progress=lambda *args: seen.append(args)
    )
    assert seen == [("me", 4.0, 8.0), ("me", 8.0, 8.0)]


def test_session_without_tracks_is_rejected(tmp_path, config):
    session = SessionStore(tmp_path).create("empty")
    with pytest.raises(TranscriptionError, match="no audio tracks"):
        transcribe_session(session, config, transcriber_for(FakeModel({})))


def test_missing_audio_file_is_reported(tmp_path, config):
    session = make_session(tmp_path, {"me": "me.wav"})
    (session.dir / "me.wav").unlink()
    with pytest.raises(TranscriptionError, match="missing"):
        transcribe_session(session, config, transcriber_for(FakeModel({})))


def test_model_load_failure_is_wrapped(tmp_path):
    def broken(name, compute):
        raise RuntimeError("no such model")

    transcriber = Transcriber("nope", "int8", model_factory=broken)
    with pytest.raises(TranscriptionError, match="cannot load Whisper model 'nope'"):
        transcriber.transcribe(tmp_path / "x.wav", "me")


def test_decoding_failure_is_wrapped(tmp_path):
    class Exploding:
        def transcribe(self, path, **kwargs):
            raise ValueError("corrupt audio")

    transcriber = Transcriber("tiny", model_factory=lambda name, compute: Exploding())
    with pytest.raises(TranscriptionError, match="corrupt audio"):
        transcriber.transcribe(tmp_path / "x.wav", "me")


def test_model_is_loaded_once(tmp_path):
    loads = []

    def factory(name, compute):
        loads.append(name)
        return FakeModel({"a.wav": [], "b.wav": []})

    transcriber = Transcriber("tiny", model_factory=factory)
    transcriber.transcribe(tmp_path / "a.wav", "me")
    transcriber.transcribe(tmp_path / "b.wav", "remote")
    assert loads == ["tiny"]


def test_microphone_echo_of_the_remote_track_is_dropped(tmp_path, config):
    session = make_session(tmp_path, {"me": "me.wav", "remote": "remote.wav"})
    model = FakeModel(
        {
            "me.wav": [
                (0.2, 3.0, "오늘 주간 회의를 시작하겠습니다"),
                (8.0, 9.0, "네 알겠습니다 감사합니다"),
            ],
            "remote.wav": [(0.0, 3.0, "오늘 주간 회의를 시작하겠습니다.")],
        }
    )
    segments = transcribe_session(session, config, transcriber_for(model))
    assert [(s.role, s.text) for s in segments] == [
        ("remote", "오늘 주간 회의를 시작하겠습니다."),
        ("me", "네 알겠습니다 감사합니다"),
    ]


def words(*items):
    return [SimpleNamespace(start=a, end=b, word=w) for a, b, w in items]


def test_segments_are_split_where_the_speaker_paused():
    from vecho.transcribe import split_at_pauses

    raw = SimpleNamespace(
        start=0.1,
        end=38.8,
        text=" 안녕하세요 오늘은 좋네요 그럼",
        words=words(
            (0.1, 0.6, " 안녕하세요"),
            (0.7, 1.2, " 오늘은"),
            (15.0, 15.5, " 좋네요"),
            (15.6, 16.0, " 그럼"),
        ),
    )
    parts = split_at_pauses(raw, "me")
    assert [(p.start, p.end, p.text) for p in parts] == [
        (0.1, 1.2, "안녕하세요 오늘은"),
        (15.0, 16.0, "좋네요 그럼"),
    ]


def test_segments_without_word_timings_are_kept_whole():
    from vecho.transcribe import split_at_pauses

    raw = SimpleNamespace(start=1.0, end=2.0, text="  hello ", words=None)
    assert [(p.start, p.text) for p in split_at_pauses(raw, "remote")] == [(1.0, "hello")]
    assert split_at_pauses(SimpleNamespace(start=0, end=1, text="  ", words=[]), "me") == []


def test_word_timestamps_are_requested(tmp_path, config):
    session = make_session(tmp_path, {"me": "me.wav"})
    model = FakeModel({"me.wav": [(0, 1, "hi")]})
    transcribe_session(session, config, transcriber_for(model))
    assert model.calls[0][1]["word_timestamps"] is True
