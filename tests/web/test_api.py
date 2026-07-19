import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from utils.status_store import StatusStore  # noqa: E402
from web.app import create_app  # noqa: E402
from web.auth import SessionAuth  # noqa: E402

PASSWORD = "hunter2"


class FakeSupervisor:
    def __init__(self):
        self.calls = []
        self.procs = {}
        self.paused = False

    def snapshot(self):
        return self.procs

    def sync_users(self):
        self.calls.append(("sync_users",))

    def stop_user(self, user, force=False):
        self.calls.append(("stop_user", user, force))
        return user in self.procs

    def resume_user(self, user):
        self.calls.append(("resume_user", user))

    def remove_user(self, user, reason=""):
        self.calls.append(("remove_user", user))

    def stop_all(self, force=False):
        self.calls.append(("stop_all", force))
        return list(self.procs)

    def resume_all(self):
        self.calls.append(("resume_all",))
        return list(self.procs)

    def pause(self):
        self.calls.append(("pause",))
        self.paused = True

    def unpause(self):
        self.calls.append(("unpause",))
        self.paused = False

    def preseed_stopped(self, users):
        self.calls.append(("preseed_stopped", tuple(users)))

    def check_now(self, user):
        self.calls.append(("check_now", user))
        return user in self.procs


@pytest.fixture
def env(tmp_path):
    users_file = tmp_path / "users.txt"
    users_file.write_text("alice\nbob\n")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    status_db = tmp_path / "status.sqlite3"

    supervisor = FakeSupervisor()
    supervisor.procs = {
        "alice": {"pid": 111, "alive": True, "stopped": False},
        "bob": {"pid": 222, "alive": True, "stopped": False},
    }

    app = create_app(
        supervisor=supervisor,
        users_file=users_file,
        output_dir=output_dir,
        auth=SessionAuth(PASSWORD),
        status_db=status_db,
    )
    client = TestClient(app)
    client.post("/api/login", json={"password": PASSWORD})
    return client, supervisor, users_file, output_dir, status_db


def test_api_requires_login(tmp_path):
    app = create_app(
        supervisor=FakeSupervisor(),
        users_file=tmp_path / "users.txt",
        output_dir=tmp_path,
        auth=SessionAuth(PASSWORD),
        status_db=tmp_path / "status.sqlite3",
    )
    client = TestClient(app)

    assert client.get("/api/status").status_code == 401
    # pages redirect to the login form instead
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 307
    assert page.headers["location"] == "/login"


def test_wrong_password_rejected(tmp_path):
    (tmp_path / "users.txt").write_text("")
    app = create_app(
        supervisor=FakeSupervisor(),
        users_file=tmp_path / "users.txt",
        output_dir=tmp_path,
        auth=SessionAuth(PASSWORD),
        status_db=tmp_path / "status.sqlite3",
    )
    client = TestClient(app)
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.get("/api/status").status_code == 401


def test_status_merges_process_and_store(env):
    client, supervisor, _, _, status_db = env

    store = StatusStore(status_db)
    store.update(
        "alice", state="recording", bytes_written=4096, room_id="7", pid=os.getpid()
    )
    store.close()

    data = client.get("/api/status").json()
    by_user = {e["user"]: e for e in data["recordings"]}

    assert by_user["alice"]["state"] == "recording"
    assert by_user["alice"]["bytes_written"] == 4096
    assert by_user["alice"]["alive"] is True
    # bob has a process but no status row yet
    assert by_user["bob"]["state"] == "starting"


def test_add_user_appends_and_syncs(env):
    client, supervisor, users_file, _, _ = env

    resp = client.post("/api/users", json={"user": "@carol"})
    assert resp.status_code == 200
    assert "carol" in users_file.read_text()
    assert ("sync_users",) in supervisor.calls


def test_add_duplicate_user_conflicts(env):
    client, _, _, _, _ = env
    assert client.post("/api/users", json={"user": "alice"}).status_code == 409


def test_add_invalid_user_rejected(env):
    client, _, _, _, _ = env
    assert client.post("/api/users", json={"user": "has space"}).status_code == 422


def test_delete_user_rewrites_file_and_removes_process(env):
    client, supervisor, users_file, _, _ = env

    resp = client.delete("/api/users/bob")
    assert resp.status_code == 200
    assert "bob" not in users_file.read_text()
    assert ("remove_user", "bob") in supervisor.calls


def test_stop_and_resume_call_supervisor(env):
    client, supervisor, _, _, _ = env

    assert client.post("/api/recordings/alice/stop").status_code == 200
    assert ("stop_user", "alice", False) in supervisor.calls

    assert (
        client.post("/api/recordings/alice/stop", json={"force": True}).status_code
        == 200
    )
    assert ("stop_user", "alice", True) in supervisor.calls

    assert client.post("/api/recordings/alice/resume").status_code == 200
    assert ("resume_user", "alice") in supervisor.calls


def test_stop_unknown_user_404(env):
    client, _, _, _, _ = env
    assert client.post("/api/recordings/ghost/stop").status_code == 404


def _paused_users(status_db):
    store = StatusStore(status_db)
    try:
        return store.paused_users()
    finally:
        store.close()


def test_stop_persists_pause_and_resume_clears_it(env):
    client, _, _, _, status_db = env

    client.post("/api/recordings/alice/stop")
    assert _paused_users(status_db) == {"alice"}
    by_user = {e["user"]: e for e in client.get("/api/status").json()["recordings"]}
    assert by_user["alice"]["paused"] is True
    assert by_user["bob"]["paused"] is False

    client.post("/api/recordings/alice/resume")
    assert _paused_users(status_db) == set()
    by_user = {e["user"]: e for e in client.get("/api/status").json()["recordings"]}
    assert by_user["alice"]["paused"] is False


def test_stop_user_without_process_still_persists(env):
    client, supervisor, users_file, _, status_db = env
    users_file.write_text("alice\nbob\ncarol\n")

    # carol is in the users file but has no process (e.g. paused at startup)
    resp = client.post("/api/recordings/carol/stop")
    assert resp.status_code == 200
    assert _paused_users(status_db) == {"carol"}
    assert ("preseed_stopped", ("carol",)) in supervisor.calls


def test_stop_all_and_resume_all_persist_pause(env):
    client, _, _, _, status_db = env

    client.post("/api/recordings/stop-all")
    assert _paused_users(status_db) == {"alice", "bob"}

    client.post("/api/recordings/resume-all")
    assert _paused_users(status_db) == set()


def test_delete_user_clears_persisted_state(env):
    client, _, _, _, status_db = env

    store = StatusStore(status_db)
    store.set_paused("bob", True)
    store.add_history("bob", started_at=1.0, ended_at=2.0)
    store.close()

    client.delete("/api/users/bob")

    store = StatusStore(status_db)
    try:
        assert store.paused_users() == set()
        assert store.latest_history() == {}
    finally:
        store.close()


def test_check_now_endpoint(env):
    client, supervisor, _, _, _ = env

    resp = client.post("/api/recordings/alice/check-now")
    assert resp.status_code == 200
    assert ("check_now", "alice") in supervisor.calls

    assert client.post("/api/recordings/ghost/check-now").status_code == 409


def test_status_includes_last_recording_history(env):
    client, _, _, _, status_db = env

    store = StatusStore(status_db)
    store.add_history("alice", started_at=100.0, ended_at=160.0, bytes_written=9)
    store.close()

    by_user = {e["user"]: e for e in client.get("/api/status").json()["recordings"]}
    assert by_user["alice"]["last_recorded_at"] == 160.0
    assert by_user["alice"]["last_duration"] == 60.0
    assert by_user["bob"]["last_recorded_at"] is None
    assert by_user["bob"]["last_duration"] is None


def test_files_listing_and_download(env):
    client, _, _, output_dir, status_db = env
    (output_dir / "TK_alice_2026.07.17_10-00-00.mp4").write_bytes(b"video")
    (output_dir / "TK_bob_2026.07.17_10-00-00_flv.mp4").write_bytes(b"raw")
    active_raw = output_dir / "TK_carol_2026.07.17_10-00-00_flv.mp4"
    active_raw.write_bytes(b"raw-active")

    store = StatusStore(status_db)
    store.update(
        "carol",
        state="recording",
        pid=os.getpid(),
        output_path=str(active_raw),
        bytes_written=1024,
    )
    store.close()

    files = client.get("/api/files").json()["files"]
    by_name = {f["name"]: f for f in files}
    assert by_name["TK_alice_2026.07.17_10-00-00.mp4"]["raw"] is False
    assert by_name["TK_alice_2026.07.17_10-00-00.mp4"]["convertible"] is False
    assert by_name["TK_bob_2026.07.17_10-00-00_flv.mp4"]["raw"] is True
    assert by_name["TK_bob_2026.07.17_10-00-00_flv.mp4"]["convertible"] is True
    assert by_name["TK_carol_2026.07.17_10-00-00_flv.mp4"]["raw"] is True
    assert by_name["TK_carol_2026.07.17_10-00-00_flv.mp4"]["convertible"] is False

    resp = client.get("/files/TK_alice_2026.07.17_10-00-00.mp4")
    assert resp.status_code == 200
    assert resp.content == b"video"


def test_convert_raw_file_uses_configured_ffmpeg_path(tmp_path, monkeypatch):
    users_file = tmp_path / "users.txt"
    users_file.write_text("")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    status_db = tmp_path / "status.sqlite3"
    raw = output_dir / "TK_alice_2026.07.17_10-00-00_flv.mp4"
    raw.write_bytes(b"raw")

    called = {}

    def _fake_convert(file, bitrate=None, ffmpeg_path=None):
        called["file"] = file
        called["ffmpeg_path"] = ffmpeg_path
        converted = str(Path(file).with_name("TK_alice_2026.07.17_10-00-00.mp4"))
        Path(converted).write_bytes(b"converted")
        Path(file).unlink()
        return converted

    monkeypatch.setattr("web.app.VideoManagement.convert_flv_to_mp4", _fake_convert)

    app = create_app(
        supervisor=FakeSupervisor(),
        users_file=users_file,
        output_dir=output_dir,
        auth=SessionAuth(PASSWORD),
        status_db=status_db,
        ffmpeg_path="/custom/ffmpeg",
    )
    client = TestClient(app)
    client.post("/api/login", json={"password": PASSWORD})

    resp = client.post(f"/api/files/{raw.name}/convert")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "name": "TK_alice_2026.07.17_10-00-00.mp4"}
    assert called["file"] == str(raw)
    assert called["ffmpeg_path"] == "/custom/ffmpeg"
    assert not raw.exists()
    assert (output_dir / "TK_alice_2026.07.17_10-00-00.mp4").is_file()


def test_convert_rejects_non_raw_file(env):
    client, _, _, output_dir, _ = env
    file = output_dir / "TK_alice_2026.07.17_10-00-00.mp4"
    file.write_bytes(b"video")

    resp = client.post(f"/api/files/{file.name}/convert")
    assert resp.status_code == 409
    assert "Only raw" in resp.json()["detail"]


def test_convert_rejects_active_raw_file(env):
    client, _, _, output_dir, status_db = env
    raw = output_dir / "TK_alice_2026.07.17_10-00-00_flv.mp4"
    raw.write_bytes(b"raw")

    store = StatusStore(status_db)
    store.update(
        "alice",
        state="recording",
        pid=os.getpid(),
        output_path=str(raw),
        bytes_written=10,
    )
    store.close()

    resp = client.post(f"/api/files/{raw.name}/convert")
    assert resp.status_code == 409
    assert "still being recorded or converted" in resp.json()["detail"]


def test_download_blocks_path_traversal(env):
    client, _, _, output_dir, _ = env
    secret = output_dir.parent / "secret.mp4"
    secret.write_bytes(b"nope")

    assert client.get("/files/..%2Fsecret.mp4").status_code == 404
    assert client.get("/files/%2e%2e%2fsecret.mp4").status_code == 404


# -- global stop / resume / pause ---------------------------------------------


def test_stop_all_and_resume_all(env):
    client, supervisor, _, _, _ = env

    resp = client.post("/api/recordings/stop-all")
    assert resp.status_code == 200
    assert set(resp.json()["stopped"]) == {"alice", "bob"}
    assert ("stop_all", False) in supervisor.calls

    resp = client.post("/api/recordings/stop-all", json={"force": True})
    assert ("stop_all", True) in supervisor.calls

    resp = client.post("/api/recordings/resume-all")
    assert resp.status_code == 200
    assert ("resume_all",) in supervisor.calls


def test_pause_and_resume_monitoring(env):
    client, supervisor, _, _, _ = env

    assert client.get("/api/status").json()["paused"] is False

    resp = client.post("/api/monitoring/pause")
    assert resp.status_code == 200
    assert resp.json()["paused"] is True
    assert ("pause",) in supervisor.calls
    assert client.get("/api/status").json()["paused"] is True

    resp = client.post("/api/monitoring/resume")
    assert resp.json()["paused"] is False
    assert ("unpause",) in supervisor.calls
    assert client.get("/api/status").json()["paused"] is False


# -- profiles / avatars ---------------------------------------------------------


def test_status_includes_profile_fields(env):
    client, _, _, output_dir, status_db = env

    store = StatusStore(status_db)
    store.upsert_profile("alice", nickname="Alice A", avatar_url="http://cdn/a.jpg")
    store.close()
    avatar_dir = output_dir / ".tlr-avatar-cache"
    avatar_dir.mkdir()
    (avatar_dir / "alice.jpg").write_bytes(b"jpeg")

    by_user = {e["user"]: e for e in client.get("/api/status").json()["recordings"]}

    assert by_user["alice"]["nickname"] == "Alice A"
    assert by_user["alice"]["avatar"] == "/api/avatar/alice"
    # bob has no profile row and no cached avatar
    assert by_user["bob"]["nickname"] is None
    assert by_user["bob"]["avatar"] is None


def test_avatar_served_from_cache(env):
    client, _, _, output_dir, _ = env
    avatar_dir = output_dir / ".tlr-avatar-cache"
    avatar_dir.mkdir()
    (avatar_dir / "alice.jpg").write_bytes(b"jpeg-bytes")

    resp = client.get("/api/avatar/alice")
    assert resp.status_code == 200
    assert resp.content == b"jpeg-bytes"
    assert client.get("/api/avatar/nobody").status_code == 404


def test_avatar_blocks_path_traversal(env):
    client, _, _, output_dir, _ = env
    (output_dir / ".tlr-avatar-cache").mkdir()
    secret = output_dir / "secret.jpg"
    secret.write_bytes(b"nope")

    assert client.get("/api/avatar/..%2Fsecret").status_code == 404
    assert client.get("/api/avatar/%2e%2e%2fsecret").status_code == 404
