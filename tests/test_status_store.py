import os

import pytest

from utils.status_store import (
    NullStatusReporter,
    StatusReporter,
    StatusStore,
    status_db_path,
)


@pytest.fixture
def store(tmp_path):
    s = StatusStore(tmp_path / "status.sqlite3")
    yield s
    s.close()


def test_update_creates_and_reads_row(store):
    store.update("alice", state="recording", room_id="123", bytes_written=1024)

    row = store.get("alice")
    assert row["state"] == "recording"
    assert row["room_id"] == "123"
    assert row["bytes_written"] == 1024
    assert row["updated_at"] > 0


def test_update_upserts_existing_row(store):
    store.update("alice", state="recording", bytes_written=100)
    store.update("alice", bytes_written=200)

    row = store.get("alice")
    assert row["bytes_written"] == 200
    # untouched fields keep their previous value
    assert row["state"] == "recording"


def test_update_rejects_unknown_fields(store):
    with pytest.raises(ValueError):
        store.update("alice", nonsense=1)


def test_missing_user_returns_none(store):
    assert store.get("nobody") is None


def test_all_marks_dead_pid_as_stale(store):
    store.update("alice", state="recording", pid=os.getpid())
    store.update("bob", state="recording", pid=99999999)
    store.update("carol", state="stopped", pid=99999999)

    rows = {r["user"]: r["state"] for r in store.all()}
    assert rows["alice"] == "recording"  # our own pid is alive
    assert rows["bob"] == "stale"  # dead pid, non-terminal state
    assert rows["carol"] == "stopped"  # terminal states are never stale


def test_remove_deletes_row(store):
    store.update("alice", state="waiting")
    store.remove("alice")
    assert store.get("alice") is None


def test_reporter_writes_own_pid(tmp_path):
    db = tmp_path / "status.sqlite3"
    StatusReporter("alice", db).report(state="recording")

    store = StatusStore(db)
    try:
        assert store.get("alice")["pid"] == os.getpid()
    finally:
        store.close()


def test_reporter_swallows_write_failures(tmp_path):
    # a directory path is unopenable as a database; report() must not raise
    reporter = StatusReporter("alice", tmp_path)
    reporter.report(state="recording")


def test_null_reporter_is_a_noop():
    NullStatusReporter().report(state="recording", bytes_written=1)


def test_status_db_path_uses_output_dir(tmp_path):
    assert status_db_path(tmp_path).parent == tmp_path


def test_upsert_profile_creates_row(store):
    store.upsert_profile("alice", nickname="Alice A", avatar_url="http://a/x.jpg")

    profiles = store.profiles()
    assert profiles["alice"]["nickname"] == "Alice A"
    assert profiles["alice"]["avatar_url"] == "http://a/x.jpg"
    assert profiles["alice"]["updated_at"] > 0


def test_upsert_profile_none_never_clobbers(store):
    store.upsert_profile("alice", nickname="Alice A", avatar_url="http://a/x.jpg")
    before = store.profiles()["alice"]["updated_at"]

    # partial update: only avatar_fetched_at; nickname/avatar_url survive
    store.upsert_profile("alice", avatar_fetched_at=123.0)

    profile = store.profiles()["alice"]
    assert profile["nickname"] == "Alice A"
    assert profile["avatar_url"] == "http://a/x.jpg"
    assert profile["avatar_fetched_at"] == 123.0
    assert profile["updated_at"] >= before


def test_profiles_keyed_by_user(store):
    store.upsert_profile("alice", nickname="A")
    store.upsert_profile("bob", nickname="B")

    profiles = store.profiles()
    assert set(profiles) == {"alice", "bob"}


def test_set_paused_roundtrip(store):
    assert store.paused_users() == set()

    store.set_paused("alice", True)
    store.set_paused("bob", True)
    assert store.paused_users() == {"alice", "bob"}

    store.set_paused("alice", False)
    assert store.paused_users() == {"bob"}


def test_setting_roundtrip_and_default(store):
    assert store.get_setting("missing") is None
    assert store.get_setting("missing", "fallback") == "fallback"

    store.set_setting("greeting", "hi")
    assert store.get_setting("greeting") == "hi"

    store.set_setting("greeting", "bye")
    assert store.get_setting("greeting") == "bye"


def test_scale_setting_roundtrip(store):
    # unset: falls back to the caller-supplied default
    assert store.scale_enabled() is False
    assert store.scale_enabled(default=True) is True

    store.set_scale(True)
    assert store.scale_enabled() is True
    assert store.scale_enabled(default=False) is True  # stored value wins

    store.set_scale(False)
    assert store.scale_enabled(default=True) is False


def test_add_history_upsert_is_idempotent(store):
    store.add_history(
        "alice", started_at=100.0, ended_at=200.0, bytes_written=10, output_path="a.flv"
    )
    store.add_history(
        "alice", started_at=100.0, ended_at=205.0, bytes_written=20, output_path="a.mp4"
    )

    latest = store.latest_history()["alice"]
    assert latest["started_at"] == 100.0
    assert latest["ended_at"] == 205.0
    assert latest["duration"] == 105.0
    assert latest["bytes_written"] == 20
    assert latest["output_path"] == "a.mp4"


def test_latest_history_picks_newest_per_user(store):
    store.add_history("alice", started_at=100.0, ended_at=200.0)
    store.add_history("alice", started_at=300.0, ended_at=450.0)
    store.add_history("bob", started_at=50.0, ended_at=60.0)

    latest = store.latest_history()
    assert latest["alice"]["ended_at"] == 450.0
    assert latest["alice"]["duration"] == 150.0
    assert latest["bob"]["ended_at"] == 60.0


def test_remove_clears_settings_and_history(store):
    store.update("alice", state="waiting")
    store.set_paused("alice", True)
    store.add_history("alice", started_at=1.0, ended_at=2.0)

    store.remove("alice")

    assert store.get("alice") is None
    assert store.paused_users() == set()
    assert store.latest_history() == {}


def test_reopening_pre_feature_db_migrates(tmp_path):
    import sqlite3

    # simulate a DB created before user_settings/recording_history existed
    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE recordings (user TEXT PRIMARY KEY, state TEXT NOT NULL, "
        "pid INTEGER, room_id TEXT, output_path TEXT, "
        "bytes_written INTEGER NOT NULL DEFAULT 0, started_at REAL, "
        "updated_at REAL NOT NULL, error TEXT)"
    )
    conn.commit()
    conn.close()

    store = StatusStore(db)
    try:
        store.set_paused("alice", True)
        store.add_history("alice", started_at=1.0, ended_at=2.0)
        assert store.paused_users() == {"alice"}
    finally:
        store.close()


def test_reporter_record_session_writes_history(tmp_path):
    db = tmp_path / "status.sqlite3"
    StatusReporter("alice", db).record_session(
        started_at=100.0, ended_at=160.0, bytes_written=5, output_path="x.mp4"
    )

    store = StatusStore(db)
    try:
        latest = store.latest_history()["alice"]
        assert latest["duration"] == 60.0
        assert latest["output_path"] == "x.mp4"
    finally:
        store.close()


def test_reporter_record_session_defaults_ended_at_to_now(tmp_path):
    import time

    db = tmp_path / "status.sqlite3"
    before = time.time()
    StatusReporter("alice", db).record_session(started_at=before - 10)

    store = StatusStore(db)
    try:
        assert store.latest_history()["alice"]["ended_at"] >= before
    finally:
        store.close()


def test_reporter_record_session_swallows_failures(tmp_path):
    StatusReporter("alice", tmp_path).record_session(started_at=1.0)


def test_null_reporter_record_session_is_a_noop():
    NullStatusReporter().record_session(started_at=1.0)
