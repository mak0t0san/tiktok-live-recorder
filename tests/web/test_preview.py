import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from utils.status_store import StatusStore  # noqa: E402
from web.app import create_app  # noqa: E402
from web.auth import SessionAuth  # noqa: E402
from web.preview import PreviewManager  # noqa: E402

PASSWORD = "hunter2"

FAKE_FFMPEG = """#!/usr/bin/env python3
import sys, pathlib
out = pathlib.Path(sys.argv[-1])
out.write_text("#EXTM3U\\n#EXT-X-VERSION:3\\n")
(out.parent / "index0.ts").write_bytes(b"segment-bytes")
sys.stdin.buffer.read()
"""


class FakeSupervisor:
    def snapshot(self):
        return {}


@pytest.fixture
def fake_ffmpeg(tmp_path):
    script = tmp_path / "fake-ffmpeg"
    script.write_text(FAKE_FFMPEG)
    script.chmod(0o755)
    return script


@pytest.fixture
def env(tmp_path, fake_ffmpeg):
    status_db = tmp_path / "status.sqlite3"
    store = StatusStore(status_db)
    store.add_monitored("alice")
    store.close()

    source = tmp_path / "TK_alice_flv.mp4"
    source.write_bytes(b"flv-data" * 100)

    store = StatusStore(status_db)
    store.update(
        "alice",
        state="recording",
        output_path=str(source),
        pid=os.getpid(),
    )
    store.close()

    previews = PreviewManager(ffmpeg_path=str(fake_ffmpeg), idle_timeout=30)
    app = create_app(
        supervisor=FakeSupervisor(),
        output_dir=tmp_path,
        auth=SessionAuth(PASSWORD),
        status_db=status_db,
        previews=previews,
    )
    client = TestClient(app)
    client.post("/api/login", json={"password": PASSWORD})
    yield client, previews, status_db
    previews.shutdown()


def test_playlist_starts_preview_and_serves_m3u8(env):
    client, previews, _ = env

    resp = client.get("/preview/alice/index.m3u8")
    assert resp.status_code == 200
    assert resp.text.startswith("#EXTM3U")
    assert previews.get("alice") is not None


def test_segment_served_after_playlist(env):
    client, _, _ = env
    client.get("/preview/alice/index.m3u8")

    resp = client.get("/preview/alice/index0.ts")
    assert resp.status_code == 200
    assert resp.content == b"segment-bytes"


def test_segment_traversal_blocked(env):
    client, _, _ = env
    client.get("/preview/alice/index.m3u8")

    assert client.get("/preview/alice/..%2Fsecret.ts").status_code == 404
    assert client.get("/preview/alice/index.m3u8.bak").status_code == 404


def test_preview_404_when_not_recording(env):
    client, _, status_db = env

    store = StatusStore(status_db)
    store.update("alice", state="stopped")
    store.close()

    assert client.get("/preview/alice/index.m3u8").status_code == 404


def test_preview_404_for_unknown_user(env):
    client, _, _ = env
    assert client.get("/preview/ghost/index.m3u8").status_code == 404


def test_preview_requires_auth(env):
    client, _, _ = env
    client.cookies.clear()
    assert client.get("/preview/alice/index.m3u8").status_code == 401


def test_idle_preview_is_reaped(tmp_path, fake_ffmpeg):
    source = tmp_path / "src.flv"
    source.write_bytes(b"x" * 100)

    manager = PreviewManager(ffmpeg_path=str(fake_ffmpeg), idle_timeout=0.1)
    try:
        preview = manager.get_or_start("alice", source)
        deadline = time.monotonic() + 10
        while manager.get("alice") is not None and time.monotonic() < deadline:
            time.sleep(0.2)
        assert manager.get("alice") is None
        assert not preview.out_dir.exists()
    finally:
        manager.shutdown()


def test_shutdown_stops_processes(tmp_path, fake_ffmpeg):
    source = tmp_path / "src.flv"
    source.write_bytes(b"x" * 100)

    manager = PreviewManager(ffmpeg_path=str(fake_ffmpeg), idle_timeout=30)
    preview = manager.get_or_start("alice", source)
    manager.shutdown()

    assert not preview.alive()
    assert not preview.out_dir.exists()
