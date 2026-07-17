from utils.recording_lock import FileLock, pid_is_running, recording_lock


def test_second_acquire_fails_while_held_and_succeeds_after_release(tmp_path):
    path = tmp_path / "a.lock"
    first = FileLock(path)
    second = FileLock(path)

    assert first.acquire() is True
    assert second.acquire() is False, "a held lock must not be re-acquired"

    first.release()
    assert second.acquire() is True
    second.release()


def test_release_is_idempotent(tmp_path):
    lock = FileLock(tmp_path / "a.lock")
    assert lock.acquire() is True
    lock.release()
    lock.release()  # must not raise
    assert not (tmp_path / "a.lock").exists()


def test_stale_lock_with_dead_pid_is_stolen(tmp_path):
    path = tmp_path / "a.lock"
    # A PID that is essentially guaranteed not to be running.
    dead_pid = 2**31 - 1
    path.write_text(str(dead_pid))

    assert not pid_is_running(dead_pid)
    assert FileLock(path).acquire() is True, "a stale lock should be stolen"


def test_malformed_lock_file_is_treated_as_stale(tmp_path):
    path = tmp_path / "a.lock"
    path.write_text("not-a-pid")

    assert FileLock(path).acquire() is True


def test_recording_lock_is_case_insensitive(tmp_path):
    assert (
        recording_lock("Vuilu695", tmp_path).path
        == recording_lock("vuilu695", tmp_path).path
    )


def test_recording_lock_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert recording_lock("creator").path == tmp_path / ".tiktok-rec.creator.lock"
