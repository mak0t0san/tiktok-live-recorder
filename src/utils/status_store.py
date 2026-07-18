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
        self._conn.commit()

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


class NullStatusReporter:
    """Default reporter for plain CLI runs: does nothing."""

    def report(self, **fields):
        pass
