"""Unit tests for TikTok cookie resolution."""

import json

from utils.cookies import (
    cookie_status,
    migrate_legacy_cookies_file,
    resolve_cookies,
    save_cookies_to_store,
    setting_key,
)
from utils.status_store import StatusStore


def test_resolve_prefers_env_over_store(tmp_path, monkeypatch):
    store = StatusStore(tmp_path / "status.sqlite3")
    save_cookies_to_store(
        store,
        {
            "sessionid_ss": "from-db",
            "tt-target-idc": "useast5",
            "msToken": "db-token",
        },
    )
    monkeypatch.setenv("TLR_SESSIONID_SS", "from-env")
    monkeypatch.delenv("TLR_TT_TARGET_IDC", raising=False)
    monkeypatch.delenv("TLR_MSTOKEN", raising=False)

    cookies = resolve_cookies(store)
    assert cookies["sessionid_ss"] == "from-env"
    assert cookies["tt-target-idc"] == "useast5"
    assert cookies["msToken"] == "db-token"
    store.close()


def test_migrate_legacy_cookies_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TLR_SESSIONID_SS", raising=False)
    monkeypatch.delenv("TLR_TT_TARGET_IDC", raising=False)
    monkeypatch.delenv("TLR_MSTOKEN", raising=False)

    legacy = tmp_path / "cookies.json"
    legacy.write_text(
        json.dumps(
            {
                "sessionid_ss": "legacy-sid",
                "tt-target-idc": "useast8",
                "msToken": "legacy-ms",
            }
        ),
        encoding="utf-8",
    )
    store = StatusStore(tmp_path / "status.sqlite3")
    assert migrate_legacy_cookies_file(store, legacy) is True
    assert store.get_setting(setting_key("sessionid_ss")) == "legacy-sid"
    # second call is a no-op
    assert migrate_legacy_cookies_file(store, legacy) is False
    store.close()


def test_cookie_status_reports_env_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("TLR_SESSIONID_SS", "env-sid")
    monkeypatch.delenv("TLR_TT_TARGET_IDC", raising=False)
    monkeypatch.delenv("TLR_MSTOKEN", raising=False)
    store = StatusStore(tmp_path / "status.sqlite3")
    status = cookie_status(store)
    assert status["cookies_present"] is True
    assert status["cookies_sources"]["sessionid_ss"] == "env"
    assert status["cookies_env_locked"]["sessionid_ss"] is True
    store.close()


def test_monitored_users_crud_and_migrate(tmp_path):
    store = StatusStore(tmp_path / "status.sqlite3")
    assert store.add_monitored("@Alice") is True
    assert store.add_monitored("alice") is False
    assert store.list_monitored() == ["Alice"]

    legacy = tmp_path / "users.txt"
    legacy.write_text("bob\ncarol\n", encoding="utf-8")
    # non-empty monitored set skips migration
    assert store.migrate_users_file_if_needed(legacy) == []

    store.replace_monitored([])
    imported = store.migrate_users_file_if_needed(legacy)
    assert imported == ["bob", "carol"]
    store.close()
