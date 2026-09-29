import pytest

from vecho.config import Config
from vecho.errors import SessionError
from vecho.transcript import (
    Segment,
    coalesce,
    format_duration,
    format_timestamp,
    load_segments,
    merge_segments,
    render_lines,
    render_markdown,
    save_segments,
    split_into_chunks,
)

LABELS = Config().label_for


def seg(start, end, role, text):
    return Segment(start, end, role, text)


def test_format_timestamp():
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(75.9) == "00:01:15"
    assert format_timestamp(3725) == "01:02:05"
    assert format_timestamp(-3) == "00:00:00"


def test_format_duration_handles_unknown():
    assert format_duration(None) == "unknown"
    assert format_duration(61) == "00:01:01"


def test_merge_interleaves_tracks_by_time():
    me = [seg(0, 2, "me", "hello"), seg(10, 12, "me", "bye")]
    remote = [seg(3, 5, "remote", "hi")]
    merged = merge_segments(me, remote)
    assert [s.text for s in merged] == ["hello", "hi", "bye"]


def test_merge_is_stable_for_simultaneous_speech():
    merged = merge_segments([seg(1, 2, "me", "a")], [seg(1, 2, "remote", "b")])
    assert [s.role for s in merged] == ["me", "remote"]


def test_coalesce_joins_close_same_speaker_only():
    segments = [
        seg(0, 2, "me", "one"),
        seg(2.5, 4, "me", "two"),
        seg(4.2, 5, "remote", "reply"),
        seg(20, 22, "me", "later"),
    ]
    joined = coalesce(segments)
    assert [(s.role, s.text) for s in joined] == [
        ("me", "one two"),
        ("remote", "reply"),
        ("me", "later"),
    ]
    assert joined[0].end == 4


def test_render_lines_uses_labels_and_omits_unknown_speaker():
    lines = render_lines([seg(65, 70, "me", "안녕하세요"), seg(80, 82, "mixed", "text")], LABELS)
    assert lines == ["[00:01:05] 나: 안녕하세요", "[00:01:20] text"]


def test_render_markdown_placeholder_when_empty():
    assert "No speech detected" in render_markdown("Title", [], LABELS)
    body = render_markdown("Title", [seg(0, 1, "me", "hey")], LABELS)
    assert body.startswith("# Title — Transcript")
    assert "[00:00:00] 나: hey" in body


def test_split_into_chunks_respects_limit_and_keeps_order():
    lines = [f"line-{i:02d}" for i in range(10)]  # 7 chars + newline each
    chunks = split_into_chunks(lines, 20)
    assert all(len(chunk) <= 20 for chunk in chunks)
    assert "\n".join(chunks).split("\n") == lines


def test_split_into_chunks_breaks_single_oversized_line():
    chunks = split_into_chunks(["x" * 25], 10)
    assert chunks == ["x" * 10, "x" * 10, "x" * 5]


def test_split_into_chunks_single_chunk_when_small():
    assert split_into_chunks(["a", "b"], 100) == ["a\nb"]
    assert split_into_chunks([], 100) == []


def test_save_and_load_roundtrip(tmp_path):
    segments = [seg(0.5, 1.5, "me", "안녕"), seg(2, 3, "remote", "hello")]
    path = tmp_path / "t.json"
    save_segments(path, segments, "ko", "small")
    assert load_segments(path) == segments


def test_load_segments_rejects_garbage(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('{"segments": [{"start": 1}]}', encoding="utf-8")
    with pytest.raises(SessionError):
        load_segments(path)
    with pytest.raises(SessionError):
        load_segments(tmp_path / "missing.json")


# ---- speaker-bleed removal ------------------------------------------------------------


def test_echo_of_remote_speech_is_removed_from_the_mic_track():
    from vecho.transcript import remove_echo

    segments = [
        seg(1, 4, "remote", "안녕하세요. 오늘 주간 회의를 시작하겠습니다."),
        seg(1, 4, "me", "안녕하세요 오늘 주간 회의를 시작하겠습니다"),
        seg(6, 8, "me", "네, 좋습니다. 시작하시죠."),
    ]
    kept = remove_echo(segments)
    assert [(s.role, s.text) for s in kept] == [
        ("remote", "안녕하세요. 오늘 주간 회의를 시작하겠습니다."),
        ("me", "네, 좋습니다. 시작하시죠."),
    ]


def test_echo_is_detected_across_different_segment_boundaries():
    from vecho.transcript import remove_echo

    segments = [
        seg(0, 2, "remote", "첫 번째 안건은 신규 앱 출시 일정입니다."),
        seg(2, 4, "remote", "개발팀은 다음 달 십오일까지 완성해주세요."),
        seg(
            0.5,
            4,
            "me",
            "첫 번째 안건은 신규 앱 출시 일정입니다 개발팀은 다음 달 십오일까지 완성해주세요",
        ),
    ]
    assert [s.role for s in remove_echo(segments)] == ["remote", "remote"]


def test_same_words_far_apart_in_time_are_not_an_echo():
    from vecho.transcript import remove_echo

    segments = [
        seg(0, 2, "remote", "마케팅 예산은 오백만 원입니다."),
        seg(60, 62, "me", "마케팅 예산은 오백만 원입니다."),
    ]
    assert len(remove_echo(segments)) == 2


def test_short_replies_and_different_speech_are_kept():
    from vecho.transcript import remove_echo

    segments = [
        seg(0, 2, "remote", "네 알겠습니다"),
        seg(0, 1, "me", "네"),
        seg(1, 3, "remote", "내일 오전에 다시 연락드리겠습니다."),
        seg(1, 3, "me", "그때 결제 모듈 이야기도 같이 하죠."),
    ]
    assert [s.text for s in remove_echo(segments) if s.role == "me"] == [
        "네",
        "그때 결제 모듈 이야기도 같이 하죠.",
    ]


def test_echo_removal_needs_a_remote_track_and_ignores_mixed():
    from vecho.transcript import remove_echo

    only_me = [
        seg(0, 2, "me", "안녕하세요 오늘 회의를 시작합니다"),
        seg(0, 2, "me", "안녕하세요 오늘 회의를 시작합니다"),
    ]
    assert remove_echo(only_me) == only_me
    mixed = [
        seg(0, 2, "remote", "안녕하세요 오늘 회의를 시작합니다"),
        seg(0, 2, "mixed", "안녕하세요 오늘 회의를 시작합니다"),
    ]
    assert remove_echo(mixed) == mixed


def test_short_replies_sharing_words_with_the_other_side_are_kept():
    from vecho.transcript import remove_echo

    remote = seg(
        0, 30, "remote", "다음 주 일정은 마케팅 예산 확정 후에 다시 이야기하죠 그때 정하면 됩니다"
    )
    for text in ("일정 다시 잡죠", "마케팅 예산 얘기죠", "그때 이야기하죠"):
        kept = remove_echo([remote, seg(10, 12, "me", text)])
        assert [s.role for s in kept] == ["remote", "me"], text


@pytest.mark.parametrize(
    ("heard", "echo"),
    [
        (
            "이번 분기 매출이 좀 떨어졌는데 원인을 파악해봐야 될 것 같아요",
            "이번 분기 매출이 좀 떨어졌는데요 원인을 파악해 봐야 할 것 같아요",
        ),
        (
            "다음 달에 새 매장을 오픈하고 이벤트를 시작할 예정입니다",
            "다음 달에 새 매장을 오픈하구요 이벤트를 시작할 예정이에요",
        ),
        ("네 알겠습니다 그럼 그렇게 진행하시죠", "네 알겠습니다 그러면 그렇게 진행하시죠"),
    ],
)
def test_echoes_with_recognition_differences_are_removed(heard, echo):
    from vecho.transcript import remove_echo

    kept = remove_echo([seg(0, 5, "remote", heard), seg(0.3, 5.2, "me", echo)])
    assert [s.role for s in kept] == ["remote"]
