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


class FakeTikTokAPI:
    """Stands in for TikTokAPI in /api/following tests."""

    fetches = 0

    def __init__(self, following=None, error=None):
        self.following = following or []
        self.error = error
        self.closed = False

    def get_sec_uid(self):
        return "sec-uid"

    def get_following(self, sec_uid):
        type(self).fetches += 1
        if self.error is not None:
            raise self.error
        return self.following

    def close(self):
        self.closed = True


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


def test_files_listing_and_download(env):
    client, _, _, output_dir, _ = env
    (output_dir / "TK_alice_2026.07.17_10-00-00.mp4").write_bytes(b"video")
    (output_dir / "TK_bob_2026.07.17_10-00-00_flv.mp4").write_bytes(b"raw")

    files = client.get("/api/files").json()["files"]
    by_name = {f["name"]: f for f in files}
    assert by_name["TK_alice_2026.07.17_10-00-00.mp4"]["raw"] is False
    assert by_name["TK_bob_2026.07.17_10-00-00_flv.mp4"]["raw"] is True

    resp = client.get("/files/TK_alice_2026.07.17_10-00-00.mp4")
    assert resp.status_code == 200
    assert resp.content == b"video"


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


# -- following picker -----------------------------------------------------------


def _following_app(tmp_path, api_factory):
    users_file = tmp_path / "users.txt"
    users_file.write_text("alice\n")
    app = create_app(
        supervisor=FakeSupervisor(),
        users_file=users_file,
        output_dir=tmp_path,
        auth=SessionAuth(PASSWORD),
        status_db=tmp_path / "status.sqlite3",
        api_factory=api_factory,
    )
    client = TestClient(app)
    client.post("/api/login", json={"password": PASSWORD})
    return client, users_file


def _entry(user, nickname=None):
    return {"unique_id": user, "nickname": nickname, "avatar_url": None}


def test_following_unavailable_without_factory(env):
    client, _, _, _, _ = env
    assert client.get("/api/following").status_code == 503


def test_following_filters_out_listed_users(tmp_path):
    FakeTikTokAPI.fetches = 0
    api = FakeTikTokAPI(following=[_entry("alice"), _entry("carol", "Carol C")])
    client, _ = _following_app(tmp_path, lambda: api)

    data = client.get("/api/following").json()

    assert [e["unique_id"] for e in data["following"]] == ["carol"]
    assert api.closed


def test_following_uses_cache_until_refresh(tmp_path):
    FakeTikTokAPI.fetches = 0
    client, users_file = _following_app(
        tmp_path, lambda: FakeTikTokAPI(following=[_entry("carol"), _entry("dave")])
    )

    client.get("/api/following")
    client.get("/api/following")
    assert FakeTikTokAPI.fetches == 1

    client.get("/api/following?refresh=1")
    assert FakeTikTokAPI.fetches == 2

    # a just-added user disappears without a refetch
    client.post("/api/users", json={"user": "carol"})
    data = client.get("/api/following").json()
    assert [e["unique_id"] for e in data["following"]] == ["dave"]
    assert FakeTikTokAPI.fetches == 2


def test_following_api_error_becomes_502(tmp_path):
    from utils.custom_exceptions import TikTokRecorderError

    FakeTikTokAPI.fetches = 0
    client, _ = _following_app(
        tmp_path,
        lambda: FakeTikTokAPI(error=TikTokRecorderError("session expired")),
    )

    resp = client.get("/api/following")
    assert resp.status_code == 502
    assert "session expired" in resp.json()["detail"]
