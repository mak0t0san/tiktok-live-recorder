from types import SimpleNamespace

import main
from core import supervisor as supervisor_mod
from core.supervisor import Supervisor, terminate_all
from utils.enums import Mode


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


def _args(users_file):
    return SimpleNamespace(
        users_file=str(users_file),
        url=None,
        user=None,
        room_id=None,
        automatic_interval=5,
        proxy=None,
        output=None,
        duration=None,
        telegram=False,
        bitrate=None,
        ffmpeg_path=None,
    )


def _patch_process(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(supervisor_mod.multiprocessing, "Process", FakeProcess)


def _run(monkeypatch, users_file, polls, between_polls=None):
    """Run run_recordings_from_file, interrupting after `polls` sleep calls."""
    _patch_process(monkeypatch)

    sleep_calls = {"n": 0}

    def fake_sleep(seconds):
        sleep_calls["n"] += 1
        if between_polls:
            between_polls(sleep_calls["n"])
        if sleep_calls["n"] > polls:
            raise KeyboardInterrupt()

    monkeypatch.setattr("time.sleep", fake_sleep)

    main.run_recordings_from_file(_args(users_file), Mode.AUTOMATIC, cookies={})


def test_starts_one_process_per_user(monkeypatch, tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("alice\nbob\n")

    _run(monkeypatch, users_file, polls=0)

    assert len(FakeProcess.instances) == 2


def test_removed_user_process_is_terminated(monkeypatch, tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("alice\nbob\n")

    def remove_bob(poll_number):
        if poll_number == 1:
            users_file.write_text("alice\n")

    _run(monkeypatch, users_file, polls=1, between_polls=remove_bob)

    bob = FakeProcess.instances[1]
    assert bob.terminated
    assert bob.joined


def test_dead_process_restarts_with_backoff(monkeypatch, tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("alice\n")

    def kill_alice(poll_number):
        for proc in FakeProcess.instances:
            proc.alive = False

    _run(monkeypatch, users_file, polls=3, between_polls=kill_alice)

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


def test_empty_users_file_exits_without_processes(monkeypatch, tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("# nobody yet\n")

    _run(monkeypatch, users_file, polls=0)

    assert FakeProcess.instances == []


def _supervisor(monkeypatch, tmp_path, users):
    _patch_process(monkeypatch)
    users_file = tmp_path / "users.txt"
    users_file.write_text("".join(f"{u}\n" for u in users))
    sup = Supervisor(_args(users_file), Mode.AUTOMATIC, cookies={})
    sup.sync_users()
    return sup


def test_stop_user_sets_stop_event_and_survives_sync(monkeypatch, tmp_path):
    sup = _supervisor(monkeypatch, tmp_path, ["alice"])

    assert sup.stop_user("alice")
    assert sup.stop_events["alice"].is_set()
    # cooperative: the process is not terminated, it exits on its own
    assert not FakeProcess.instances[0].terminated

    # once the process exits, sync must NOT restart a manually stopped user
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
    # the replacement child gets a fresh, unset stop event
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
