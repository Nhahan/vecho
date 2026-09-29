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


# ---- MLX engine --------------------------------------------------------------------------


def write_speech_wav(path, rate=16000):
    """1 s of silence, 1.5 s of a voice-like tone, 1 s of silence — enough for the VAD."""
    import wave

    import numpy as np

    t = np.arange(int(rate * 1.5)) / rate
    voice = 0.4 * np.sin(2 * np.pi * 180 * t) * (1 + 0.5 * np.sin(2 * np.pi * 4 * t))
    for harmonic in (2, 3, 4):
        voice += 0.15 / harmonic * np.sin(2 * np.pi * 180 * harmonic * t)
    pcm = np.concatenate([np.zeros(rate), voice, np.zeros(rate)])
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())


def test_mlx_repo_names():
    from vecho.transcribe import mlx_repo

    assert mlx_repo("large-v3-turbo") == "mlx-community/whisper-large-v3-turbo"
    assert mlx_repo("small") == "mlx-community/whisper-small-mlx"
    assert mlx_repo("someone/custom-whisper") == "someone/custom-whisper"


def test_mlx_skips_silent_tracks_without_calling_the_model(tmp_path):
    import wave

    from vecho.transcribe import MlxTranscriber

    path = tmp_path / "silent.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000 * 5)

    def never(*args, **kwargs):
        raise AssertionError("the model must not run on silence")

    result = MlxTranscriber("large-v3-turbo", transcribe_fn=never).transcribe(path, "me")
    assert result.segments == [] and result.duration == pytest.approx(5.0)


def test_mlx_maps_times_back_and_splits_at_pauses(tmp_path, monkeypatch):
    from vecho import transcribe as tr

    path = tmp_path / "me.wav"
    write_speech_wav(path)

    from vecho.transcribe import SpeechTimeline

    # one speech chunk that really began at 10 s
    timeline = SpeechTimeline([{"start": 160000, "end": 240000}], 16000)
    monkeypatch.setattr(tr, "speech_only", lambda audio: (audio[:100], timeline))
    seen = {}

    def fake_mlx(audio, **kwargs):
        seen.update(kwargs)
        return {
            "language": "ko",
            "segments": [
                {
                    "start": 0.0,
                    "end": 5.0,
                    "text": " 안녕하세요 반갑습니다",
                    "words": [
                        {"start": 0.0, "end": 0.5, "word": " 안녕하세요"},
                        {"start": 3.0, "end": 3.5, "word": " 반갑습니다"},
                    ],
                }
            ],
        }

    progress = []
    result = tr.MlxTranscriber("large-v3-turbo", transcribe_fn=fake_mlx).transcribe(
        path, "me", "ko", on_progress=lambda *a: progress.append(a)
    )
    assert [(s.start, s.end, s.text) for s in result.segments] == [
        (10.0, 10.5, "안녕하세요"),
        (13.0, 13.5, "반갑습니다"),
    ]
    assert result.language == "ko"
    assert seen["path_or_hf_repo"] == "mlx-community/whisper-large-v3-turbo"
    assert seen["word_timestamps"] is True and seen["condition_on_previous_text"] is False
    assert progress[0][1] == 0.0 and progress[-1][1] == progress[-1][2]


def test_mlx_errors_are_wrapped(tmp_path, monkeypatch):
    from vecho import transcribe as tr

    path = tmp_path / "me.wav"
    write_speech_wav(path)
    monkeypatch.setattr(tr, "speech_only", lambda audio: (audio, None))

    def broken(audio, **kwargs):
        raise RuntimeError("metal device lost")

    with pytest.raises(TranscriptionError, match="metal device lost"):
        tr.MlxTranscriber("large-v3-turbo", transcribe_fn=broken).transcribe(path, "me")


@pytest.mark.parametrize(
    ("backend", "available", "expected"),
    [
        ("auto", True, "MlxTranscriber"),
        ("auto", False, "Transcriber"),
        ("faster-whisper", True, "Transcriber"),
        ("mlx", False, "MlxTranscriber"),
    ],
)
def test_backend_selection(monkeypatch, config, backend, available, expected):
    from vecho import transcribe as tr

    monkeypatch.setattr(tr, "mlx_available", lambda: available)
    engine = tr.make_transcriber(config.with_overrides(whisper_backend=backend))
    assert type(engine).__name__ == expected


def test_words_at_a_chunk_boundary_stay_in_their_own_chunk():
    """Two sentences 30 s apart become adjacent in the speech-only audio; keep them apart."""
    from vecho.transcribe import SpeechTimeline

    rate = 16000
    chunks = [{"start": 0, "end": 2 * rate}, {"start": 32 * rate, "end": 34 * rate}]
    timeline = SpeechTimeline(chunks, rate, gap=0.6)  # 2nd chunk starts at 2.6 s when joined
    assert timeline.to_original(1.0, 1.5) == (1.0, 1.5)
    assert timeline.to_original(2.6, 3.2) == (32.0, 32.6)
    # a word Whisper places slightly early, straddling the gap, still belongs to chunk 2
    assert timeline.to_original(2.4, 3.2) == (32.0, 32.6)
    # a span inside the gap goes to the nearest chunk
    assert timeline.to_original(2.05, 2.15) == (2.0, 2.0)


def test_speech_only_skips_pure_silence():
    import numpy as np

    from vecho import transcribe as tr

    assert tr.speech_only(np.zeros(16000 * 40, dtype=np.float32)) is None


def test_track_start_offsets_shift_the_later_track(tmp_path, config):
    session = make_session(tmp_path, {"me": "me.wav", "remote": "remote.wav"})
    session.meta.offsets = {"remote": 0.4}
    session.save()
    model = FakeModel({"me.wav": [(1.0, 2.0, "하나")], "remote.wav": [(0.8, 1.5, "둘")]})
    segments = transcribe_session(session, config, transcriber_for(model))
    assert [(s.role, s.start) for s in segments] == [("me", 1.0), ("remote", 1.2)]


def test_segments_without_a_real_time_are_dropped(tmp_path, config):
    session = make_session(tmp_path, {"me": "me.wav"})
    model = FakeModel(
        {"me.wav": [(float("nan"), 1.0, "유령"), (2.0, float("inf"), "유령"), (3.0, 4.0, "진짜")]}
    )
    segments = transcribe_session(session, config, transcriber_for(model))
    assert [s.text for s in segments] == ["진짜"]
    json.loads(session.path_for("transcript.json").read_text("utf-8"), parse_constant=pytest.fail)
