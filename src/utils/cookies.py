"""
TikTok session cookie resolution.

Priority (first non-empty wins per key):
1. Environment variables (TLR_SESSIONID_SS, TLR_TT_TARGET_IDC, TLR_MSTOKEN)
2. Values persisted in the status DB ``app_settings``
3. Legacy ``cookies.json`` next to the source tree (one-time migration source)

Env-sourced keys are treated as locked so the Settings UI does not overwrite
TrueNAS / Compose secrets.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

COOKIE_KEYS = ("sessionid_ss", "tt-target-idc", "msToken")

ENV_MAP = {
    "sessionid_ss": "TLR_SESSIONID_SS",
    "tt-target-idc": "TLR_TT_TARGET_IDC",
    "msToken": "TLR_MSTOKEN",
}

SETTING_PREFIX = "cookie."


def setting_key(name: str) -> str:
    return f"{SETTING_PREFIX}{name}"


def cookies_json_path() -> Path:
    return Path(__file__).resolve().parent.parent / "cookies.json"


def read_legacy_cookies_file(path: Path | None = None) -> dict:
    """Load cookies.json if present; empty dict on any failure."""
    target = path or cookies_json_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(data[k]) for k in COOKIE_KEYS if data.get(k)}


def env_cookie_sources() -> dict[str, bool]:
    """Map cookie key -> True when a non-empty env var is set for it."""
    return {key: bool(os.environ.get(env, "").strip()) for key, env in ENV_MAP.items()}


def cookies_from_env() -> dict:
    out = {}
    for key, env in ENV_MAP.items():
        value = os.environ.get(env, "").strip()
        if value:
            out[key] = value
    return out


def cookies_from_store(store) -> dict:
    out = {}
    for key in COOKIE_KEYS:
        value = store.get_setting(setting_key(key))
        if value:
            out[key] = value
    return out


def save_cookies_to_store(store, cookies: dict, *, only_keys=None) -> None:
    """
    Persist cookie values into app_settings.

    Empty / missing values are skipped (leave unchanged). Pass ``only_keys``
    to restrict which keys are written (e.g. skip env-locked keys).
    """
    keys = only_keys if only_keys is not None else COOKIE_KEYS
    for key in keys:
        if key not in COOKIE_KEYS:
            continue
        value = cookies.get(key)
        if value is None:
            continue
        value = str(value).strip()
        if not value:
            continue
        store.set_setting(setting_key(key), value)


def migrate_legacy_cookies_file(store, path: Path | None = None) -> bool:
    """
    If the DB has no session cookie yet, copy non-empty values from
    cookies.json. Returns True when anything was written.
    """
    if store.get_setting(setting_key("sessionid_ss")):
        return False
    legacy = read_legacy_cookies_file(path)
    if not legacy:
        return False
    # Do not clobber keys already set via env by writing them into the DB
    # as a stale fallback — only fill keys the env did not provide.
    env_locked = env_cookie_sources()
    to_save = {k: v for k, v in legacy.items() if not env_locked.get(k)}
    if not to_save:
        return False
    save_cookies_to_store(store, to_save)
    return True


def resolve_cookies(store=None) -> dict:
    """
    Build the cookie dict used by TikTokAPI / HttpClient.

    Always returns all three keys (possibly empty strings) so callers can
    treat the shape as stable.
    """
    merged = {key: "" for key in COOKIE_KEYS}
    if store is not None:
        merged.update(cookies_from_store(store))
    else:
        # No DB yet (one-shot CLI): still honour a legacy file as fallback.
        merged.update(read_legacy_cookies_file())
    merged.update(cookies_from_env())
    return merged


def cookie_status(store=None) -> dict:
    """
    Dashboard-facing summary: presence, source per key, and a hint string.
    Never includes raw secret values.
    """
    env = cookies_from_env()
    stored = cookies_from_store(store) if store is not None else {}
    legacy = read_legacy_cookies_file()
    resolved = resolve_cookies(store)
    sources = {}
    for key in COOKIE_KEYS:
        if key in env:
            sources[key] = "env"
        elif key in stored:
            sources[key] = "settings"
        elif key in legacy and store is None:
            sources[key] = "file"
        elif resolved.get(key):
            sources[key] = "settings"
        else:
            sources[key] = "missing"
    present = bool(resolved.get("sessionid_ss"))
    if present:
        hint = (
            "TikTok session configured"
            + (
                " (from environment)"
                if sources["sessionid_ss"] == "env"
                else " (from Settings)"
            )
            + " — if TikTok calls fail, refresh the session cookies."
        )
    else:
        hint = (
            "No TikTok session cookie configured. Set TLR_SESSIONID_SS "
            "(and optionally TLR_TT_TARGET_IDC / TLR_MSTOKEN), or paste "
            "them in Settings."
        )
    return {
        "cookies_present": present,
        "cookies_sources": sources,
        "cookies_hint": hint,
        "cookies_env_locked": env_cookie_sources(),
    }
