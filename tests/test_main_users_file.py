from types import SimpleNamespace

from core import supervisor as supervisor_mod
from core.supervisor import Supervisor, terminate_all
from utils.enums import Mode
from utils.status_store import StatusStore


class FakeProcess:
    instances = []

    def __init__(self, target=None, args=()):
        self.target = target
        self.args = args
        self.alive = False
        self.terminated = False
        self.killed = False
        self.joined = False
        self.pid = 1000 + len(FakeProcess.instances)
        FakeProcess.instances.append(self)

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True
        self.alive = False

    def kill(self):
        self.killed = True
        self.alive = False

    def join(self, timeout=None):
        self.joined = True


def _args(output=None):
    return SimpleNamespace(
        url=None,
        user=None,
        room_id=None,
        automatic_interval=5,
        proxy=None,
        output=output,
        duration=None,
        telegram=False,
        bitrate=None,
        scale=False,
        ffmpeg_path=None,
    )


def _patch_process(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(supervisor_mod.multiprocessing, "Process", FakeProcess)


def _seed(tmp_path, users):
    db = tmp_path / "status.sqlite3"
    store = StatusStore(db)
    for user in users:
        store.add_monitored(user)
    store.close()
    return db


def _run(monkeypatch, tmp_path, users, polls, between_polls=None):
    """Run supervisor.run_forever, interrupting after `polls` sleep calls."""
    _patch_process(monkeypatch)
    db = _seed(tmp_path, users)
    sleep_calls = {"n": 0}

    def fake_sleep(seconds):
        sleep_calls["n"] += 1
        if between_polls:
            between_polls(sleep_calls["n"], db)
        if sleep_calls["n"] > polls:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", fake_sleep)
    sup = Supervisor(_args(str(tmp_path)), Mode.AUTOMATIC, cookies={}, status_db=db)
    sup.sync_users()
    try:
        if polls > 0:
            sup.run_forever()
    except KeyboardInterrupt:
        pass
    return sup


def test_starts_one_process_per_user(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, ["alice", "bob"], polls=0)
    assert len(FakeProcess.instances) == 2


def test_removed_user_process_is_terminated(monkeypatch, tmp_path):
    def remove_bob(poll_number, db):
        if poll_number == 1:
            store = StatusStore(db)
            store.remove_monitored("bob")
            store.close()

    _run(monkeypatch, tmp_path, ["alice", "bob"], polls=1, between_polls=remove_bob)

    bob = FakeProcess.instances[1]
    assert bob.terminated
    assert bob.joined


def test_dead_process_restarts_with_backoff(monkeypatch, tmp_path):
    def kill_alice(poll_number, db):
        for proc in FakeProcess.instances:
            proc.alive = False

    _run(monkeypatch, tmp_path, ["alice"], polls=3, between_polls=kill_alice)

    # initial start + exactly one restart: the backoff window (>= 20s) has
    # not expired between the instantaneous fake polls
    assert len(FakeProcess.instances) == 2


class StubbornProcess:
    """Ignores SIGTERM (stays alive after terminate); only kill() stops it."""

    def __init__(self):
        self.alive = True
        self.terminated = False
        self.killed = False

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True  # ignored: still alive

    def kill(self):
        self.killed = True
        self.alive = False

    def join(self, timeout=None):
        pass


def test_terminate_all_escalates_to_kill_for_stubborn_process():
    stubborn = StubbornProcess()
    calm = FakeProcess()
    calm.start()  # alive, dies on terminate

    terminate_all({"a": stubborn, "b": calm})

    assert stubborn.terminated and stubborn.killed and not stubborn.is_alive()
    assert calm.terminated and not calm.killed and not calm.is_alive()


def test_terminate_all_accepts_a_list():
    stubborn = StubbornProcess()
    terminate_all([stubborn])
    assert stubborn.killed and not stubborn.is_alive()


def test_empty_monitored_set_starts_nothing(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, [], polls=0)
    assert FakeProcess.instances == []


def _supervisor(monkeypatch, tmp_path, users):
    _patch_process(monkeypatch)
    db = _seed(tmp_path, users)
    sup = Supervisor(_args(str(tmp_path)), Mode.AUTOMATIC, cookies={}, status_db=db)
    sup.sync_users()
    return sup


def test_stop_user_sets_stop_event_and_survives_sync(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice"])

    assert sup.stop_user("alice")
    assert sup.stop_events["alice"].is_set()
    assert not FakeProcess.instances[0].terminated

    FakeProcess.instances[0].alive = False
    sup.sync_users()
    assert len(FakeProcess.instances) == 1


def test_stop_user_force_terminates(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice"])

    sup.stop_user("alice", force=True)
    assert FakeProcess.instances[0].terminated


def test_stop_unknown_user_returns_false(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice"])
    assert not sup.stop_user("nobody")


def test_resume_user_restarts_stopped_process(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice"])

    sup.stop_user("alice")
    FakeProcess.instances[0].alive = False

    sup.resume_user("alice")
    assert len(FakeProcess.instances) == 2
    assert FakeProcess.instances[1].alive
    assert not sup.stop_events["alice"].is_set()


def test_snapshot_reports_state(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice", "bob"])
    sup.stop_user("bob")

    snap = sup.snapshot()
    assert snap["alice"] == {
        "pid": FakeProcess.instances[0].pid,
        "alive": True,
        "stopped": False,
    }
    assert snap["bob"]["stopped"] is True


def test_stop_event_reaches_recorder_config(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice"])

    config = FakeProcess.instances[0].args[0]
    assert config.stop_event is sup.stop_events["alice"]


def test_stop_all_marks_everyone_stopped(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice", "bob"])

    stopped = sup.stop_all()

    assert set(stopped) == {"alice", "bob"}
    assert sup.stopped_users == {"alice", "bob"}
    assert sup.stop_events["alice"].is_set()
    assert sup.stop_events["bob"].is_set()
    assert not any(p.terminated for p in FakeProcess.instances)


def test_resume_all_restarts_all_stopped_users(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice", "bob"])
    sup.stop_all()
    for proc in FakeProcess.instances:
        proc.alive = False

    resumed = sup.resume_all()

    assert set(resumed) == {"alice", "bob"}
    assert sup.stopped_users == set()
    assert len(FakeProcess.instances) == 4


def test_pause_sets_events_without_marking_stopped(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice", "bob"])
    sup.stop_user("bob")

    sup.pause()

    assert sup.paused
    assert sup.stop_events["alice"].is_set()
    assert "alice" not in sup.stopped_users
    assert sup.stopped_users == {"bob"}

    for proc in FakeProcess.instances:
        proc.alive = False
    sup.sync_users()
    assert len(FakeProcess.instances) == 2


def test_unpause_restarts_only_non_stopped_users(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice", "bob"])
    sup.stop_user("bob")
    sup.pause()
    for proc in FakeProcess.instances:
        proc.alive = False

    sup.unpause()

    assert not sup.paused
    assert len(FakeProcess.instances) == 3
    assert not sup.stop_events["alice"].is_set()
    assert "bob" in sup.stopped_users


def test_resume_user_while_paused_defers_start(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice"])
    sup.stop_user("alice")
    FakeProcess.instances[0].alive = False
    sup.pause()

    sup.resume_user("alice")

    assert "alice" not in sup.stopped_users
    assert len(FakeProcess.instances) == 1

    sup.unpause()
    assert len(FakeProcess.instances) == 2


def test_preseed_stopped_prevents_start(monkeypatch, tmp_path):
    _patch_process(monkeypatch)
    db = _seed(tmp_path, ["alice", "bob"])
    sup = Supervisor(_args(str(tmp_path)), Mode.AUTOMATIC, cookies={}, status_db=db)

    sup.preseed_stopped(["alice"])
    sup.sync_users()

    assert len(FakeProcess.instances) == 1
    assert set(sup.processes) == {"bob"}
    assert sup.stopped_users == {"alice"}

    sup.sync_users()
    assert len(FakeProcess.instances) == 1


def test_preseed_stopped_user_resumes_normally(monkeypatch, tmp_path):
    _patch_process(monkeypatch)
    db = _seed(tmp_path, ["alice"])
    sup = Supervisor(_args(str(tmp_path)), Mode.AUTOMATIC, cookies={}, status_db=db)
    sup.preseed_stopped(["alice"])
    sup.sync_users()
    assert FakeProcess.instances == []

    sup.resume_user("alice")
    assert len(FakeProcess.instances) == 1
    assert sup.stopped_users == set()


def test_wake_event_reaches_recorder_config(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice"])

    config = FakeProcess.instances[0].args[0]
    assert config.wake_event is sup.wake_events["alice"]


def test_check_now_sets_wake_event_for_live_user(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice"])

    assert sup.check_now("alice") is True
    assert sup.wake_events["alice"].is_set()


def test_check_now_refuses_stopped_paused_dead_or_unknown(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice", "bob"])

    assert sup.check_now("nobody") is False

    sup.stop_user("alice")
    assert sup.check_now("alice") is False

    FakeProcess.instances[1].alive = False
    assert sup.check_now("bob") is False

    FakeProcess.instances = []
    db = tmp_path / "carol.sqlite3"
    store = StatusStore(db)
    store.add_monitored("carol")
    store.close()
    _patch_process(monkeypatch)
    FakeProcess.instances = []
    sup2 = Supervisor(_args(), Mode.AUTOMATIC, cookies={}, status_db=db)
    sup2.sync_users()
    FakeProcess.instances[0].alive = True
    sup2.paused = True
    assert sup2.check_now("carol") is False
