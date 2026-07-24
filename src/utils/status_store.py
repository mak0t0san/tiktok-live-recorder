"""
Cross-process recording status, backed by SQLite (WAL mode).

Recorder processes write their state here at cheap points (state transitions
and buffer flushes); the web dashboard reads it. One row per user. Plain CLI
runs use the no-op ``NullStatusReporter`` and never touch the database.
"""

import sqlite3
import time
from pathlib import Path

from utils.recording_lock import pid_is_running

DB_FILENAME = ".tiktok-recorder-status.sqlite3"

# States a recorder moves through; "stale" is synthesized at read time when
# the reporting process is gone but never wrote a terminal state.
STATES = ("waiting", "recording", "converting", "uploading", "stopped", "error")
TERMINAL_STATES = ("stopped", "error")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    user TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    pid INTEGER,
    room_id TEXT,
    output_path TEXT,
    bytes_written INTEGER NOT NULL DEFAULT 0,
    started_at REAL,
    updated_at REAL NOT NULL,
    error TEXT
)
"""

# Cosmetic profile data (display name, avatar) shown by the dashboard;
# refreshed in the background by web.profiles.ProfileRefresher.
_PROFILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    user TEXT PRIMARY KEY,
    nickname TEXT,
    avatar_url TEXT,
    avatar_fetched_at REAL,
    updated_at REAL NOT NULL
)
"""

# Per-user knobs that must survive restarts (currently just the pause flag).
_USER_SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_settings (
    user TEXT PRIMARY KEY,
    paused INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
)
"""

# Global (not per-user) dashboard settings, as a simple key/value bucket so
# the dashboard can persist toggles that must survive a restart.
_APP_SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL NOT NULL
)
"""

# Whether NEW recordings are re-encoded onto a single consistent resolution
# (the -scale feature), toggled globally from the dashboard.
SCALE_SETTING = "normalize_size"

# One row per finished recording session; keyed by (user, started_at) so the
# post-conversion re-write of the same session just updates the path.
_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS recording_history (
    user TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL NOT NULL,
    duration REAL NOT NULL,
    bytes_written INTEGER NOT NULL DEFAULT 0,
    output_path TEXT,
    PRIMARY KEY (user, started_at)
)
"""

_HISTORY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_history_user_ended
ON recording_history(user, ended_at)
"""


def status_db_path(output_dir=None) -> Path:
    directory = Path(output_dir) if output_dir else Path.cwd()
    return directory / DB_FILENAME


class StatusStore:
    """Owns the SQLite connection; safe for concurrent writers via WAL."""

    def __init__(self, path):
        self.path = Path(path)
        self._conn = sqlite3.connect(self.path, timeout=5)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(_SCHEMA)
        self._conn.execute(_PROFILES_SCHEMA)
        self._conn.execute(_USER_SETTINGS_SCHEMA)
        self._conn.execute(_APP_SETTINGS_SCHEMA)
        self._conn.execute(_HISTORY_SCHEMA)
        self._conn.execute(_HISTORY_INDEX)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def update(self, user, **fields):
        """
        Upsert ``user``'s row. Unknown users are created; ``updated_at`` is
        always refreshed so it doubles as a heartbeat.
        """
        allowed = {
            "state",
            "pid",
            "room_id",
            "output_path",
            "bytes_written",
            "started_at",
            "error",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown status fields: {sorted(unknown)}")

        fields["updated_at"] = time.time()

        # "waiting" is only a default for brand-new rows; a partial update
        # (e.g. just bytes_written) must not clobber the current state.
        insert_fields = dict(fields)
        insert_fields.setdefault("state", "waiting")

        columns = ["user", *insert_fields]
        placeholders = ", ".join("?" * len(columns))
        updates = ", ".join(f"{c}=excluded.{c}" for c in fields)
        self._conn.execute(
            f"INSERT INTO recordings ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(user) DO UPDATE SET {updates}",
            [user, *insert_fields.values()],
        )
        self._conn.commit()

    def get(self, user):
        rows = self._rows("WHERE user = ?", [user])
        return rows[0] if rows else None

    def all(self):
        """
        All rows, with a synthesized ``stale`` state for entries whose
        reporting process died without writing a terminal state.
        """
        rows = self._rows()
        for row in rows:
            if row["state"] not in TERMINAL_STATES and not pid_is_running(
                row["pid"] or 0
            ):
                row["state"] = "stale"
        return rows

    def remove(self, user):
        self._conn.execute("DELETE FROM recordings WHERE user = ?", [user])
        self._conn.execute("DELETE FROM user_settings WHERE user = ?", [user])
        self._conn.execute("DELETE FROM recording_history WHERE user = ?", [user])
        self._conn.commit()

    def set_paused(self, user, paused):
        """Persist whether ``user``'s monitoring is paused."""
        self._conn.execute(
            "INSERT INTO user_settings (user, paused, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user) DO UPDATE SET "
            "paused=excluded.paused, updated_at=excluded.updated_at",
            [user, 1 if paused else 0, time.time()],
        )
        self._conn.commit()

    def paused_users(self) -> set:
        cursor = self._conn.execute("SELECT user FROM user_settings WHERE paused = 1")
        return {row[0] for row in cursor.fetchall()}

    def set_setting(self, key, value):
        """Persist a global dashboard setting (value stored as text)."""
        self._conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            [key, None if value is None else str(value), time.time()],
        )
        self._conn.commit()

    def get_setting(self, key, default=None):
        cursor = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", [key]
        )
        row = cursor.fetchone()
        return row[0] if row is not None else default

    def set_scale(self, enabled):
        """Persist whether new recordings are re-encoded to one resolution."""
        self.set_setting(SCALE_SETTING, "1" if enabled else "0")

    def scale_enabled(self, default=False) -> bool:
        value = self.get_setting(SCALE_SETTING)
        if value is None:
            return default
        return value == "1"

    def add_history(
        self, user, *, started_at, ended_at, bytes_written=0, output_path=None
    ):
        """
        Record a finished recording session. Idempotent per (user, started_at):
        the recorder re-writes the same session after flv->mp4 conversion to
        update the output path.
        """
        self._conn.execute(
            "INSERT INTO recording_history "
            "(user, started_at, ended_at, duration, bytes_written, output_path) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user, started_at) DO UPDATE SET "
            "ended_at=excluded.ended_at, duration=excluded.duration, "
            "bytes_written=excluded.bytes_written, "
            "output_path=excluded.output_path",
            [
                user,
                started_at,
                ended_at,
                max(0.0, ended_at - started_at),
                bytes_written,
                str(output_path) if output_path is not None else None,
            ],
        )
        self._conn.commit()

    def latest_history(self) -> dict:
        """Most recent finished session per user, keyed by user."""
        cursor = self._conn.execute(
            "SELECT h.user, h.started_at, h.ended_at, h.duration, "
            "h.bytes_written, h.output_path "
            "FROM recording_history h "
            "JOIN (SELECT user, MAX(ended_at) AS m FROM recording_history "
            "GROUP BY user) x ON h.user = x.user AND h.ended_at = x.m"
        )
        names = [d[0] for d in cursor.description]
        return {row[0]: dict(zip(names, row)) for row in cursor.fetchall()}

    def upsert_profile(
        self, user, *, nickname=None, avatar_url=None, avatar_fetched_at=None
    ):
        """
        Upsert ``user``'s profile row. None values never clobber existing
        data; ``updated_at`` is always refreshed so it doubles as a
        freshness marker for the background refresher.
        """
        self._conn.execute(
            "INSERT INTO profiles "
            "(user, nickname, avatar_url, avatar_fetched_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user) DO UPDATE SET "
            "nickname=COALESCE(excluded.nickname, nickname), "
            "avatar_url=COALESCE(excluded.avatar_url, avatar_url), "
            "avatar_fetched_at="
            "COALESCE(excluded.avatar_fetched_at, avatar_fetched_at), "
            "updated_at=excluded.updated_at",
            [user, nickname, avatar_url, avatar_fetched_at, time.time()],
        )
        self._conn.commit()

    def profiles(self) -> dict:
        """All profile rows, keyed by user."""
        cursor = self._conn.execute(
            "SELECT user, nickname, avatar_url, avatar_fetched_at, updated_at "
            "FROM profiles"
        )
        names = [d[0] for d in cursor.description]
        return {row[0]: dict(zip(names, row)) for row in cursor.fetchall()}

    def _rows(self, where="", params=()):
        cursor = self._conn.execute(
            f"SELECT user, state, pid, room_id, output_path, bytes_written, "
            f"started_at, updated_at, error FROM recordings {where}",
            params,
        )
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


class StatusReporter:
    """
    Write-side facade used inside a recorder process. Opens the connection
    lazily (after fork/spawn) and never lets a status write break a recording.
    """

    def __init__(self, user, db_path):
        self.user = user
        self.db_path = db_path
        self._store = None

    def _get_store(self):
        if self._store is None:
            self._store = StatusStore(self.db_path)
        return self._store

    def report(self, **fields):
        import os

        try:
            self._get_store().update(self.user, pid=os.getpid(), **fields)
        except Exception:
            # Status is best-effort; the recording must never fail because
            # the dashboard database is locked or unwritable.
            from utils.logger_manager import logger

            logger.debug("Status write failed", exc_info=True)

    def record_session(
        self, *, started_at, ended_at=None, bytes_written=0, output_path=None
    ):
        try:
            self._get_store().add_history(
                self.user,
                started_at=started_at,
                ended_at=ended_at if ended_at is not None else time.time(),
                bytes_written=bytes_written,
                output_path=output_path,
            )
        except Exception:
            from utils.logger_manager import logger

            logger.debug("History write failed", exc_info=True)


class NullStatusReporter:
    """Default reporter for plain CLI runs: does nothing."""

    def report(self, **fields):
        pass

    def record_session(self, **fields):
        pass
