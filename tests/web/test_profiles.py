import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from utils.status_store import StatusStore  # noqa: E402
from web.profiles import ProfileRefresher  # noqa: E402


class FakeAvatarResponse:
    status_code = 200
    content = b"jpeg-bytes"


class FakeHttpClient:
    def __init__(self):
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return FakeAvatarResponse()


class FakeAPI:
    def __init__(self, details=None, error=None):
        self.details = details or {}
        self.error = error
        self.http_client = FakeHttpClient()
        self.lookups = []
        self.closed = False

    def get_user_details(self, user):
        self.lookups.append(user)
        if self.error is not None:
            raise self.error
        return self.details.get(user)

    def close(self):
        self.closed = True


def _refresher(tmp_path, api, users=("alice",), **kwargs):
    store = StatusStore(tmp_path / "status.sqlite3")
    for user in users:
        store.add_monitored(user)
    store.close()
    return ProfileRefresher(
        status_db=tmp_path / "status.sqlite3",
        api_factory=lambda: api,
        cache_dir=tmp_path / "avatars",
        per_user_delay=0,
        **kwargs,
    )


def _profiles(tmp_path):
    store = StatusStore(tmp_path / "status.sqlite3")
    try:
        return store.profiles()
    finally:
        store.close()


def test_run_once_populates_profiles_and_avatars(tmp_path):
    api = FakeAPI(
        details={
            "alice": {
                "unique_id": "alice",
                "nickname": "Alice A",
                "avatar_url": "http://cdn/alice.jpg",
            }
        }
    )
    refresher = _refresher(tmp_path, api)

    refresher.run_once()

    profile = _profiles(tmp_path)["alice"]
    assert profile["nickname"] == "Alice A"
    assert profile["avatar_fetched_at"] > 0
    assert (tmp_path / "avatars" / "alice.jpg").read_bytes() == b"jpeg-bytes"
    assert api.http_client.urls == ["http://cdn/alice.jpg"]
    assert api.closed


def test_run_once_skips_fresh_profiles(tmp_path):
    api = FakeAPI()
    refresher = _refresher(tmp_path, api)

    store = StatusStore(tmp_path / "status.sqlite3")
    store.upsert_profile(
        "alice",
        nickname="Alice A",
        avatar_url="http://cdn/alice.jpg",
        avatar_fetched_at=time.time(),
    )
    store.close()
    refresher.cache_dir.mkdir()
    refresher.avatar_path("alice").write_bytes(b"cached")

    refresher.run_once()

    assert api.lookups == []
    assert api.http_client.urls == []
    assert refresher.avatar_path("alice").read_bytes() == b"cached"


def test_run_once_survives_lookup_failure(tmp_path):
    api = FakeAPI(error=RuntimeError("boom"))
    refresher = _refresher(tmp_path, api)

    refresher.run_once()

    # the row is still bumped so the user isn't retried every cycle
    assert "alice" in _profiles(tmp_path)
    assert _profiles(tmp_path)["alice"]["nickname"] is None


def test_run_once_handles_unknown_user(tmp_path):
    api = FakeAPI(details={})  # get_user_details returns None
    refresher = _refresher(tmp_path, api)

    refresher.run_once()

    assert api.lookups == ["alice"]
    assert _profiles(tmp_path)["alice"]["nickname"] is None


def test_pathlike_usernames_rejected_on_add(tmp_path):
    store = StatusStore(tmp_path / "status.sqlite3")
    try:
        for bad in ("../evil", "a/b", "a\\b", ".."):
            try:
                store.add_monitored(bad)
            except ValueError:
                continue
            raise AssertionError(f"expected ValueError for {bad!r}")
    finally:
        store.close()
