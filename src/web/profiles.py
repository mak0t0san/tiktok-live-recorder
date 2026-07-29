"""
Background refresher for cosmetic profile data (display name + avatar).

A daemon thread periodically fills the ``profiles`` table in the status DB
for every monitored user, and mirrors avatar images into a small on-disk
cache so the dashboard never hotlinks TikTok's expiring CDN URLs. Profile
data is best-effort: any failure is logged at debug level and skipped.
"""

import threading
import time
from pathlib import Path

from utils.logger_manager import logger
from utils.status_store import StatusStore

# Hidden avatar-image cache, created alongside the recordings/status DB.
AVATAR_CACHE_DIRNAME = ".tlr-avatar-cache"


class ProfileRefresher:
    def __init__(
        self,
        *,
        status_db,
        api_factory,
        cache_dir,
        profile_ttl=24 * 3600,
        avatar_ttl=7 * 24 * 3600,
        interval=600,
        per_user_delay=2.0,
        batch_limit=10,
    ):
        self.status_db = status_db
        self.api_factory = api_factory
        self.cache_dir = Path(cache_dir)
        self.profile_ttl = profile_ttl
        self.avatar_ttl = avatar_ttl
        self.interval = interval
        self.per_user_delay = per_user_delay
        self.batch_limit = batch_limit
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop.set()

    def avatar_path(self, user: str) -> Path:
        return self.cache_dir / f"{user}.jpg"

    def run_once(self):
        """One refresh pass; extracted from the thread loop for testability."""
        store = StatusStore(self.status_db)
        api = None
        try:
            users = store.list_monitored()
            if not users:
                return
            api = self._refresh_profiles(store, users)
            api = self._refresh_avatars(store, users, api)
        finally:
            if api is not None:
                api.close()
            store.close()

    def _get_api(self, api):
        if api is None and self.api_factory is not None:
            api = self.api_factory()
        return api

    def _refresh_profiles(self, store, users):
        profiles = store.profiles()
        now = time.time()
        stale = [
            user
            for user in users
            if user not in profiles
            or (profiles[user]["updated_at"] or 0) < now - self.profile_ttl
        ]

        api = None
        for user in stale[: self.batch_limit]:
            if self._stop.is_set():
                break
            api = self._get_api(api)
            if api is None:
                break
            details = None
            try:
                details = api.get_user_details(user)
            except Exception:
                logger.debug(f"Profile lookup failed for @{user}", exc_info=True)
            if details:
                store.upsert_profile(
                    user,
                    nickname=details.get("nickname"),
                    avatar_url=details.get("avatar_url"),
                )
            else:
                # Bump updated_at so an unresolvable user isn't retried
                # every cycle.
                store.upsert_profile(user)
            if self._stop.wait(self.per_user_delay):
                break
        return api

    def _refresh_avatars(self, store, users, api):
        profiles = store.profiles()
        now = time.time()
        for user in users:
            if self._stop.is_set():
                break
            profile = profiles.get(user)
            if not profile or not profile.get("avatar_url"):
                continue
            # Users file entries are already validated, but the username
            # becomes a file name — refuse anything path-like outright.
            if "/" in user or "\\" in user or user.startswith("."):
                continue
            path = self.avatar_path(user)
            fetched_at = profile.get("avatar_fetched_at") or 0
            if path.is_file() and fetched_at > now - self.avatar_ttl:
                continue
            api = self._get_api(api)
            if api is None:
                break
            try:
                response = api.http_client.get(profile["avatar_url"])
                if response.status_code == 200 and response.content:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(response.content)
                    store.upsert_profile(user, avatar_fetched_at=time.time())
            except Exception:
                logger.debug(f"Avatar download failed for @{user}", exc_info=True)
        return api

    def _run(self):
        # Small initial delay so startup (supervisor spawn, first status
        # poll) isn't competing with profile fetches.
        if self._stop.wait(5):
            return
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.debug("Profile refresh pass failed", exc_info=True)
            if self._stop.wait(self.interval):
                return
