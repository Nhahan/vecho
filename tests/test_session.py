from datetime import datetime

import pytest

from vecho.errors import SessionError
from vecho.session import Session, SessionStore, slugify

MOMENT = datetime(2026, 9, 29, 9, 30, 0)


def test_slugify_keeps_hangul_and_removes_path_characters():
    assert slugify("주간 회의 / 9월!") == "주간-회의-9월"
    assert slugify("../../etc/passwd") == "etc-passwd"
    assert slugify("   ") == ""
    assert len(slugify("x" * 200)) <= 40


def test_create_writes_metadata(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = store.create("Weekly sync", now=MOMENT)
    assert session.id == "20260929-093000-weekly-sync"
    assert (session.dir / "session.json").is_file()
    reloaded = Session.load(session.dir)
    assert reloaded.meta.title == "Weekly sync"
    assert reloaded.meta.tracks == {}


def test_same_second_sessions_do_not_collide(tmp_path):
    store = SessionStore(tmp_path)
    first = store.create("", now=MOMENT)
    second = store.create("", now=MOMENT)
    assert first.id == "20260929-093000"
    assert second.id == "20260929-093000-2"


def test_save_roundtrip_preserves_fields(tmp_path):
    session = SessionStore(tmp_path).create("한글 제목", now=MOMENT)
    session.meta.tracks = {"me": "me.wav"}
    session.meta.duration_sec = 12.5
    session.save()
    meta = Session.load(session.dir).meta
    assert meta.title == "한글 제목"
    assert meta.tracks == {"me": "me.wav"}
    assert meta.duration_sec == 12.5
    assert not list(session.dir.glob("*.tmp"))


def test_load_ignores_unknown_metadata_keys(tmp_path):
    session = SessionStore(tmp_path).create("x", now=MOMENT)
    path = session.dir / "session.json"
    path.write_text(path.read_text("utf-8").replace("{", '{"future_field": 1,', 1), "utf-8")
    assert Session.load(session.dir).meta.title == "x"


def test_load_reports_corrupt_metadata(tmp_path):
    (tmp_path / "session.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(SessionError):
        Session.load(tmp_path)


def test_audio_path_requires_registered_track(tmp_path):
    session = SessionStore(tmp_path).create("", now=MOMENT)
    session.meta.tracks = {"me": "me.wav"}
    assert session.audio_path("me") == session.dir / "me.wav"
    with pytest.raises(SessionError, match="remote"):
        session.audio_path("remote")


def test_list_is_newest_first_and_skips_junk(tmp_path):
    store = SessionStore(tmp_path)
    store.create("a", now=datetime(2026, 1, 1, 10, 0, 0))
    store.create("b", now=datetime(2026, 1, 2, 10, 0, 0))
    (tmp_path / "not-a-session").mkdir()
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "session.json").write_text("{", encoding="utf-8")
    assert [s.meta.title for s in store.list()] == ["b", "a"]


def test_list_of_missing_root_is_empty(tmp_path):
    assert SessionStore(tmp_path / "nope").list() == []


def test_resolve_variants(tmp_path):
    store = SessionStore(tmp_path)
    old = store.create("alpha", now=datetime(2026, 1, 1, 10, 0, 0))
    new = store.create("beta", now=datetime(2026, 2, 1, 10, 0, 0))
    assert store.resolve("latest").id == new.id
    assert store.resolve(old.id).id == old.id
    assert store.resolve("20260101").id == old.id  # prefix
    assert store.resolve("beta").id == new.id  # substring


def test_resolve_ambiguous_and_missing(tmp_path):
    store = SessionStore(tmp_path)
    store.create("one", now=datetime(2026, 1, 1, 10, 0, 0))
    store.create("two", now=datetime(2026, 1, 1, 11, 0, 0))
    with pytest.raises(SessionError, match="ambiguous"):
        store.resolve("2026")
    with pytest.raises(SessionError, match="no session matches"):
        store.resolve("zzz")


def test_resolve_with_no_sessions(tmp_path):
    with pytest.raises(SessionError, match="no sessions"):
        SessionStore(tmp_path).resolve("latest")
