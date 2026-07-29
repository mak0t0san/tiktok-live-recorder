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
        self.scale = False
        self.cookies = {}

    def set_scale(self, enabled):
        self.calls.append(("set_scale", enabled))
        self.scale = bool(enabled)

    def update_cookies(self, cookies):
        self.calls.append(("update_cookies", dict(cookies)))
        self.cookies = dict(cookies)

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
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    status_db = tmp_path / "status.sqlite3"

    store = StatusStore(status_db)
    store.add_monitored("alice")
    store.add_monitored("bob")
    store.close()

    supervisor = FakeSupervisor()
    supervisor.procs = {
        "alice": {"pid": 111, "alive": True, "stopped": False},
        "bob": {"pid": 222, "alive": True, "stopped": False},
    }

    app = create_app(
        supervisor=supervisor,
        output_dir=output_dir,
        auth=SessionAuth(PASSWORD),
        status_db=status_db,
    )
    client = TestClient(app)
    client.post("/api/login", json={"password": PASSWORD})
    return client, supervisor, status_db, output_dir


def test_api_requires_login(tmp_path):
    app = create_app(
        supervisor=FakeSupervisor(),
        output_dir=tmp_path,
        auth=SessionAuth(PASSWORD),
        status_db=tmp_path / "status.sqlite3",
    )
    client = TestClient(app)

    assert client.get("/api/status").status_code == 401
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 307
    assert page.headers["location"] == "/login"


def test_wrong_password_rejected(tmp_path):
    app = create_app(
        supervisor=FakeSupervisor(),
        output_dir=tmp_path,
        auth=SessionAuth(PASSWORD),
        status_db=tmp_path / "status.sqlite3",
    )
    client = TestClient(app)
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.get("/api/status").status_code == 401


def test_status_merges_process_and_store(env):
    client, supervisor, status_db, _ = env

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
    assert by_user["bob"]["state"] == "starting"


def test_add_user_appends_and_syncs(env):
    client, supervisor, status_db, _ = env

    resp = client.post("/api/users", json={"user": "@carol"})
    assert resp.status_code == 200
    store = StatusStore(status_db)
    try:
        assert "carol" in store.list_monitored()
    finally:
        store.close()
    assert ("sync_users",) in supervisor.calls


def test_add_duplicate_user_conflicts(env):
    client, _, _, _ = env
    assert client.post("/api/users", json={"user": "alice"}).status_code == 409


def test_add_invalid_user_rejected(env):
    client, _, _, _ = env
    assert client.post("/api/users", json={"user": "has space"}).status_code == 422


def test_delete_user_removes_and_stops_process(env):
    client, supervisor, status_db, _ = env

    resp = client.delete("/api/users/bob")
    assert resp.status_code == 200
    store = StatusStore(status_db)
    try:
        assert "bob" not in store.list_monitored()
    finally:
        store.close()
    assert ("remove_user", "bob") in supervisor.calls


def test_export_and_import_users(env):
    client, supervisor, status_db, _ = env

    exported = client.get("/api/users/export")
    assert exported.status_code == 200
    assert exported.text == "alice\nbob\n"

    resp = client.post(
        "/api/users/import",
        json={"text": "carol\n# comment\n@dave\n", "mode": "merge"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["added"]) == {"carol", "dave"}
    assert set(body["users"]) == {"alice", "bob", "carol", "dave"}
    assert ("sync_users",) in supervisor.calls

    resp = client.post(
        "/api/users/import",
        json={"text": "erin\n", "mode": "replace"},
    )
    assert resp.status_code == 200
    assert resp.json()["users"] == ["erin"]
    store = StatusStore(status_db)
    try:
        assert store.list_monitored() == ["erin"]
    finally:
        store.close()


def test_stop_and_resume_call_supervisor(env):
    client, supervisor, _, _ = env

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
    client, _, _, _ = env
    assert client.post("/api/recordings/ghost/stop").status_code == 404


def _paused_users(status_db):
    store = StatusStore(status_db)
    try:
        return store.paused_users()
    finally:
        store.close()


def test_stop_persists_pause_and_resume_clears_it(env):
    client, _, status_db, _ = env

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
    client, supervisor, status_db, _ = env
    store = StatusStore(status_db)
    store.add_monitored("carol")
    store.close()

    resp = client.post("/api/recordings/carol/stop")
    assert resp.status_code == 200
    assert _paused_users(status_db) == {"carol"}
    assert ("preseed_stopped", ("carol",)) in supervisor.calls


def test_stop_all_and_resume_all_persist_pause(env):
    client, _, status_db, _ = env

    client.post("/api/recordings/stop-all")
    assert _paused_users(status_db) == {"alice", "bob"}

    client.post("/api/recordings/resume-all")
    assert _paused_users(status_db) == set()


def test_delete_user_clears_persisted_state(env):
    client, _, status_db, _ = env

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
    client, supervisor, _, _ = env

    resp = client.post("/api/recordings/alice/check-now")
    assert resp.status_code == 200
    assert ("check_now", "alice") in supervisor.calls

    assert client.post("/api/recordings/ghost/check-now").status_code == 409


def test_status_includes_last_recording_history(env):
    client, _, status_db, _ = env

    store = StatusStore(status_db)
    store.add_history("alice", started_at=100.0, ended_at=160.0, bytes_written=9)
    store.close()

    by_user = {e["user"]: e for e in client.get("/api/status").json()["recordings"]}
    assert by_user["alice"]["last_recorded_at"] == 160.0
    assert by_user["alice"]["last_duration"] == 60.0
    assert by_user["bob"]["last_recorded_at"] is None
    assert by_user["bob"]["last_duration"] is None


def test_files_listing_and_download(env):
    client, _, status_db, output_dir = env
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
    assert by_name["TK_alice_2026.07.17_10-00-00.mp4"]["user"] is None

    resp = client.get("/files", params={"name": "TK_alice_2026.07.17_10-00-00.mp4"})
    assert resp.status_code == 200
    assert resp.content == b"video"


def test_files_listing_walks_per_user_folders(env):
    client, _, _, output_dir = env
    (output_dir / "TK_legacy_2026.07.17_10-00-00.mp4").write_bytes(b"flat")
    alice_dir = output_dir / "alice"
    alice_dir.mkdir()
    (alice_dir / "TK_alice_2026.07.18_10-00-00.mp4").write_bytes(b"nested")
    (alice_dir / "TK_alice_2026.07.18_11-00-00_flv.mp4").write_bytes(b"nested-raw")

    files = client.get("/api/files").json()["files"]
    by_name = {f["name"]: f for f in files}

    assert by_name["alice/TK_alice_2026.07.18_10-00-00.mp4"]["user"] == "alice"
    assert by_name["alice/TK_alice_2026.07.18_11-00-00_flv.mp4"]["convertible"] is True
    assert by_name["TK_legacy_2026.07.17_10-00-00.mp4"]["user"] is None


def test_download_nested_file_by_relative_path(env):
    client, _, _, output_dir = env
    alice_dir = output_dir / "alice"
    alice_dir.mkdir()
    (alice_dir / "TK_alice_2026.07.18_10-00-00.mp4").write_bytes(b"nested")

    resp = client.get(
        "/files", params={"name": "alice/TK_alice_2026.07.18_10-00-00.mp4"}
    )
    assert resp.status_code == 200
    assert resp.content == b"nested"


def test_convert_nested_raw_file(env):
    client, _, _, output_dir = env
    alice_dir = output_dir / "alice"
    alice_dir.mkdir()
    raw = alice_dir / "TK_alice_2026.07.18_10-00-00_flv.mp4"
    raw.write_bytes(b"raw")

    resp = client.post(
        "/api/files/convert",
        params={"name": "alice/TK_alice_2026.07.18_10-00-00_flv.mp4"},
    )
    assert resp.status_code != 404


def test_convert_returns_an_output_relative_name(tmp_path, monkeypatch):
    output_dir = tmp_path / "out"
    (output_dir / "alice").mkdir(parents=True)
    raw = output_dir / "alice" / "TK_alice_2026.07.18_10-00-00_flv.mp4"
    raw.write_bytes(b"raw")

    def _fake_convert(file, bitrate=None, ffmpeg_path=None, scale=False):
        converted = str(Path(file).with_name("TK_alice_2026.07.18_10-00-00.mp4"))
        Path(converted).write_bytes(b"converted")
        Path(file).unlink()
        return converted

    monkeypatch.setattr("web.app.VideoManagement.convert_flv_to_mp4", _fake_convert)

    app = create_app(
        supervisor=FakeSupervisor(),
        output_dir=output_dir,
        auth=SessionAuth(PASSWORD),
        status_db=tmp_path / "status.sqlite3",
    )
    client = TestClient(app)
    client.post("/api/login", json={"password": PASSWORD})

    resp = client.post(
        "/api/files/convert",
        params={"name": "alice/TK_alice_2026.07.18_10-00-00_flv.mp4"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "name": "alice/TK_alice_2026.07.18_10-00-00.mp4"}


def test_convert_raw_file_uses_configured_ffmpeg_path(tmp_path, monkeypatch):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    status_db = tmp_path / "status.sqlite3"
    raw = output_dir / "TK_alice_2026.07.17_10-00-00_flv.mp4"
    raw.write_bytes(b"raw")

    called = {}

    def _fake_convert(file, bitrate=None, ffmpeg_path=None, scale=False):
        called["file"] = file
        called["ffmpeg_path"] = ffmpeg_path
        called["scale"] = scale
        converted = str(Path(file).with_name("TK_alice_2026.07.17_10-00-00.mp4"))
        Path(converted).write_bytes(b"converted")
        Path(file).unlink()
        return converted

    monkeypatch.setattr("web.app.VideoManagement.convert_flv_to_mp4", _fake_convert)

    app = create_app(
        supervisor=FakeSupervisor(),
        output_dir=output_dir,
        auth=SessionAuth(PASSWORD),
        status_db=status_db,
        ffmpeg_path="/custom/ffmpeg",
    )
    client = TestClient(app)
    client.post("/api/login", json={"password": PASSWORD})

    resp = client.post("/api/files/convert", params={"name": raw.name})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "name": "TK_alice_2026.07.17_10-00-00.mp4"}
    assert called["file"] == str(raw)
    assert called["ffmpeg_path"] == "/custom/ffmpeg"
    assert called["scale"] is False
    assert not raw.exists()
    assert (output_dir / "TK_alice_2026.07.17_10-00-00.mp4").is_file()


def test_convert_scale_query_forwards_scale(tmp_path, monkeypatch):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    status_db = tmp_path / "status.sqlite3"
    raw = output_dir / "TK_alice_2026.07.17_10-00-00_flv.mp4"
    raw.write_bytes(b"raw")

    called = {}

    def _fake_convert(file, bitrate=None, ffmpeg_path=None, scale=False):
        called["scale"] = scale
        converted = str(Path(file).with_name("TK_alice_2026.07.17_10-00-00.mp4"))
        Path(converted).write_bytes(b"converted")
        Path(file).unlink()
        return converted

    monkeypatch.setattr("web.app.VideoManagement.convert_flv_to_mp4", _fake_convert)

    app = create_app(
        supervisor=FakeSupervisor(),
        output_dir=output_dir,
        auth=SessionAuth(PASSWORD),
        status_db=status_db,
    )
    client = TestClient(app)
    client.post("/api/login", json={"password": PASSWORD})

    resp = client.post("/api/files/convert", params={"name": raw.name, "scale": 1})
    assert resp.status_code == 200
    assert called["scale"] is True


def test_convert_rejects_non_raw_file(env):
    client, _, _, output_dir = env
    file = output_dir / "TK_alice_2026.07.17_10-00-00.mp4"
    file.write_bytes(b"video")

    resp = client.post("/api/files/convert", params={"name": file.name})
    assert resp.status_code == 409
    assert "Only raw" in resp.json()["detail"]


def test_convert_rejects_active_raw_file(env):
    client, _, status_db, output_dir = env
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

    resp = client.post("/api/files/convert", params={"name": raw.name})
    assert resp.status_code == 409
    assert "still being recorded or converted" in resp.json()["detail"]


def test_convert_rejects_active_raw_file_in_a_per_user_folder(env):
    client, _, status_db, output_dir = env
    alice_dir = output_dir / "alice"
    alice_dir.mkdir()
    raw = alice_dir / "TK_alice_2026.07.18_10-00-00_flv.mp4"
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

    listed = client.get("/api/files").json()["files"]
    nested = next(f for f in listed if f["name"].startswith("alice/"))
    assert nested["convertible"] is False

    resp = client.post("/api/files/convert", params={"name": nested["name"]})
    assert resp.status_code == 409
    assert "still being recorded or converted" in resp.json()["detail"]


def test_convert_matches_active_recordings_by_path_not_basename(env):
    client, _, status_db, output_dir = env
    alice_dir = output_dir / "alice"
    alice_dir.mkdir()
    bob_dir = output_dir / "bob"
    bob_dir.mkdir()
    active = alice_dir / "TK_x_2026.07.18_10-00-00_flv.mp4"
    active.write_bytes(b"raw")
    idle = bob_dir / "TK_x_2026.07.18_10-00-00_flv.mp4"
    idle.write_bytes(b"raw")

    store = StatusStore(status_db)
    store.update(
        "alice",
        state="recording",
        pid=os.getpid(),
        output_path=str(active),
        bytes_written=10,
    )
    store.close()

    files = {f["name"]: f for f in client.get("/api/files").json()["files"]}
    assert files["alice/TK_x_2026.07.18_10-00-00_flv.mp4"]["convertible"] is False
    assert files["bob/TK_x_2026.07.18_10-00-00_flv.mp4"]["convertible"] is True


def test_download_blocks_path_traversal(env):
    client, _, _, output_dir = env
    secret = output_dir.parent / "secret.mp4"
    secret.write_bytes(b"nope")

    assert client.get("/files", params={"name": "../secret.mp4"}).status_code == 404
    assert client.get("/files", params={"name": "..%2Fsecret.mp4"}).status_code == 404
    assert (
        client.get("/files", params={"name": "a/../../secret.mp4"}).status_code == 404
    )
    assert client.get("/files", params={"name": "/etc/passwd"}).status_code == 404


def test_convert_blocks_path_traversal(env):
    client, _, _, output_dir = env
    secret = output_dir.parent / "secret_flv.mp4"
    secret.write_bytes(b"nope")

    resp = client.post("/api/files/convert", params={"name": "../secret_flv.mp4"})
    assert resp.status_code == 404
    assert secret.read_bytes() == b"nope"


def test_stop_all_and_resume_all(env):
    client, supervisor, _, _ = env

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
    client, supervisor, _, _ = env

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


def test_status_exposes_scale_flag(env):
    client, supervisor, _, _ = env

    assert client.get("/api/status").json()["scale"] is False

    supervisor.scale = True
    assert client.get("/api/status").json()["scale"] is True


def test_get_settings_reflects_supervisor(env):
    client, supervisor, _, _ = env

    data = client.get("/api/settings").json()
    assert data["scale"] is False
    assert "cookies" in data
    supervisor.scale = True
    assert client.get("/api/settings").json()["scale"] is True


def test_post_settings_toggles_scale(env):
    client, supervisor, _, _ = env

    resp = client.post("/api/settings", json={"scale": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["scale"] is True
    assert ("set_scale", True) in supervisor.calls
    assert supervisor.scale is True

    client.post("/api/settings", json={"scale": False})
    assert ("set_scale", False) in supervisor.calls
    assert supervisor.scale is False


def test_post_settings_saves_cookies(env):
    client, supervisor, status_db, _ = env

    resp = client.post(
        "/api/settings",
        json={
            "sessionid_ss": "abc123",
            "tt-target-idc": "useast8",
            "msToken": "tok",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["cookies_present"] is True
    assert any(c[0] == "update_cookies" for c in supervisor.calls)

    store = StatusStore(status_db)
    try:
        from utils.cookies import setting_key

        assert store.get_setting(setting_key("sessionid_ss")) == "abc123"
    finally:
        store.close()


def test_status_includes_profile_fields(env):
    client, _, status_db, output_dir = env

    store = StatusStore(status_db)
    store.upsert_profile("alice", nickname="Alice A", avatar_url="http://cdn/a.jpg")
    store.close()
    avatar_dir = output_dir / ".tlr-avatar-cache"
    avatar_dir.mkdir()
    (avatar_dir / "alice.jpg").write_bytes(b"jpeg")

    by_user = {e["user"]: e for e in client.get("/api/status").json()["recordings"]}

    assert by_user["alice"]["nickname"] == "Alice A"
    assert by_user["alice"]["avatar"] == "/api/avatar/alice"
    assert by_user["bob"]["nickname"] is None
    assert by_user["bob"]["avatar"] is None


def test_avatar_served_from_cache(env):
    client, _, _, output_dir = env
    avatar_dir = output_dir / ".tlr-avatar-cache"
    avatar_dir.mkdir()
    (avatar_dir / "alice.jpg").write_bytes(b"jpeg-bytes")

    resp = client.get("/api/avatar/alice")
    assert resp.status_code == 200
    assert resp.content == b"jpeg-bytes"
    assert client.get("/api/avatar/nobody").status_code == 404


def test_avatar_blocks_path_traversal(env):
    client, _, _, output_dir = env
    (output_dir / ".tlr-avatar-cache").mkdir()
    secret = output_dir / "secret.jpg"
    secret.write_bytes(b"nope")

    assert client.get("/api/avatar/..%2Fsecret").status_code == 404
    assert client.get("/api/avatar/%2e%2e%2fsecret").status_code == 404
