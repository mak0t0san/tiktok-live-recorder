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
