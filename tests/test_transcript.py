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
