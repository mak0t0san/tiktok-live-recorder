from types import SimpleNamespace

import main
from utils.enums import Mode


class FakeProcess:
    instances = []

    def __init__(self, target=None, args=()):
        self.target = target
        self.args = args
        self.alive = False
        self.terminated = False
        self.joined = False
        FakeProcess.instances.append(self)

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True
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


def _run(monkeypatch, users_file, polls, between_polls=None):
    """Run run_recordings_from_file, interrupting after `polls` sleep calls."""
    FakeProcess.instances = []
    monkeypatch.setattr(main.multiprocessing, "Process", FakeProcess)

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


def test_empty_users_file_exits_without_processes(monkeypatch, tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("# nobody yet\n")

    _run(monkeypatch, users_file, polls=0)

    assert FakeProcess.instances == []
