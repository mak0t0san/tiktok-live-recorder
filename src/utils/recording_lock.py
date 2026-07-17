"""
Cross-process advisory locks backed by an atomic PID file.

Used to guarantee a given user's live stream is recorded by at most one worker
at a time, even across separate program instances (which otherwise each detect
the user as live and start their own duplicate recording). Portable across
macOS / Linux / Termux / Windows — no ``fcntl`` required.
"""

import errno
import os
from pathlib import Path


def pid_is_running(pid: int) -> bool:
    """Return True if a process with ``pid`` currently exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as e:
        # ESRCH: no such process. EPERM: exists but owned by someone else.
        return e.errno == errno.EPERM
    return True


class FileLock:
    """
    A non-blocking advisory lock represented by a PID file created with
    ``O_EXCL``. If the file already exists but the PID inside it belongs to a
    process that is no longer running, the lock is considered stale and stolen.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._acquired = False

    def acquire(self) -> bool:
        """
        Try to acquire the lock without blocking.

        Returns True on success, False if another live process holds it.
        """
        # At most two attempts: the second runs only after we clear a stale file.
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._holder_alive():
                    return False
                # Stale lock from a crashed worker: remove it and retry once.
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            else:
                with os.fdopen(fd, "w") as f:
                    f.write(str(os.getpid()))
                self._acquired = True
                return True
        return False

    def _holder_alive(self) -> bool:
        try:
            pid = int(self.path.read_text().strip() or 0)
        except (OSError, ValueError):
            # Unreadable / malformed lock file: treat as stale.
            return False
        return pid_is_running(pid)

    def release(self) -> None:
        """Release the lock if we own it. Safe to call more than once."""
        if not self._acquired:
            return
        self._acquired = False
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def recording_lock(user: str, output_dir=None) -> FileLock:
    """
    Build a per-user recording lock. The username is case-folded because
    TikTok usernames are case-insensitive, so ``vuilu695`` and ``Vuilu695``
    must share one lock.
    """
    directory = Path(output_dir) if output_dir else Path.cwd()
    return FileLock(directory / f".tiktok-rec.{user.casefold()}.lock")
