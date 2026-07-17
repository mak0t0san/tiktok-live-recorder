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
