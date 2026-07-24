"""
Process supervision for multi-user recording.

One ``multiprocessing.Process`` records each monitored user. The supervisor
keeps the process table in sync with the users file, restarts dead processes
with exponential backoff, and supports per-user cooperative stop: each child
gets a ``multiprocessing.Event`` that ``TikTokRecorder`` polls, so a stop
finalizes (flush + convert) an in-flight recording instead of killing it
mid-file. Used by the CLI users-file loop and by the web UI.
"""

import multiprocessing
import threading
import time

from utils.enums import TimeOut
from utils.logger_manager import logger
from utils.recorder_config import RecorderConfig
from utils.utils import read_users_file


def record_user(config):
    from core.tiktok_recorder import TikTokRecorder

    try:
        TikTokRecorder(config).run()
    except Exception as e:
        logger.error(f"{e}", exc_info=True)
        if config.status_db and config.user:
            from utils.status_store import StatusReporter

            StatusReporter(config.user, config.status_db).report(
                state="error", error=str(e)
            )


def build_config(
    args,
    mode,
    cookies,
    user=None,
    stop_event=None,
    status_db=None,
    wake_event=None,
    scale=None,
):
    return RecorderConfig(
        url=args.url,
        user=user,
        room_id=args.room_id,
        mode=mode,
        automatic_interval=args.automatic_interval,
        cookies=cookies,
        proxy=args.proxy,
        output=args.output,
        duration=args.duration,
        use_telegram=args.telegram,
        bitrate=args.bitrate,
        scale=args.scale if scale is None else scale,
        ffmpeg_path=args.ffmpeg_path,
        stop_event=stop_event,
        status_db=status_db,
        wake_event=wake_event,
    )


def terminate_all(processes, grace=5):
    """
    Force-stop child recorder processes, escalating SIGTERM -> SIGKILL and
    confirming each is dead, so none survive as orphans (reparented to init/
    launchd and left recording after the parent is gone).

    The graceful wait shares a single ``grace``-second deadline across all
    children (instead of per-process), so cleanup stays bounded no matter how
    many users are being recorded.
    """
    procs = list(processes.values()) if isinstance(processes, dict) else list(processes)

    for p in procs:
        if p.is_alive():
            p.terminate()

    deadline = time.monotonic() + grace
    for p in procs:
        if p.is_alive():
            p.join(timeout=max(0.0, deadline - time.monotonic()))

    for p in procs:
        if p.is_alive():
            p.kill()
    for p in procs:
        if p.is_alive():
            p.join(timeout=5)


def install_shutdown_handlers(processes):
    """
    Ensure children are cleaned up when the parent is asked to exit by a signal
    that would otherwise kill only the parent and orphan the recorders — SIGTERM
    (e.g. ``kill <pid>``) or SIGHUP (terminal closed). SIGINT is intentionally
    left alone so in-terminal Ctrl+C keeps raising KeyboardInterrupt and shuts
    down gracefully. An atexit backstop covers other exit paths.
    """
    import atexit
    import signal

    atexit.register(terminate_all, processes)

    def _handler(signum, frame):
        logger.info(f"Received signal {signum}; stopping recorders...")
        terminate_all(processes)
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # not in the main thread, or unsupported on this platform
            pass


class Supervisor:
    """
    Owns the user -> recorder-process table.

    Thread-safe: the users-file poll loop and web request handlers may call
    into the same instance concurrently.
    """

    RESTART_BASE = TimeOut.USERS_FILE_POLL  # seconds
    RESTART_CAP = 600  # max backoff between restarts of a failing user
    STABLE_AFTER = 300  # reset the backoff once a process survives this long

    def __init__(self, args, mode, cookies, status_db=None):
        self.args = args
        self.mode = mode
        self.cookies = cookies
        self.status_db = str(status_db) if status_db else None
        self.processes = {}  # user -> Process
        self.stop_events = {}  # user -> multiprocessing.Event
        self.wake_events = {}  # user -> multiprocessing.Event ("check now")
        self.stopped_users = set()  # stopped via stop_user(); excluded from restart
        self.paused = False  # global pause: no starts/restarts while set
        # Whether NEW recordings re-encode to one consistent resolution. Seeded
        # from the CLI -scale default, then overridden by any dashboard toggle
        # persisted in the status DB so the choice survives a restart.
        self.scale = bool(getattr(args, "scale", False))
        if self.status_db:
            try:
                from utils.status_store import StatusStore

                store = StatusStore(self.status_db)
                try:
                    self.scale = store.scale_enabled(default=self.scale)
                finally:
                    store.close()
            except Exception:
                logger.debug("Could not load scale setting", exc_info=True)
        self._restart_state = {}  # user -> {"count", "next_allowed", "started"}
        self._lock = threading.RLock()

    def start_user(self, user):
        with self._lock:
            stop_event = multiprocessing.Event()
            wake_event = multiprocessing.Event()
            config = build_config(
                self.args,
                self.mode,
                self.cookies,
                user=user,
                stop_event=stop_event,
                status_db=self.status_db,
                wake_event=wake_event,
                scale=self.scale,
            )
            p = multiprocessing.Process(target=record_user, args=(config,))
            p.start()
            self.processes[user] = p
            self.stop_events[user] = stop_event
            self.wake_events[user] = wake_event
            self.stopped_users.discard(user)
            state = self._restart_state.setdefault(
                user, {"count": 0, "next_allowed": 0.0, "started": 0.0}
            )
            state["started"] = time.time()

    def preseed_stopped(self, users):
        """
        Mark users as stopped before the first sync_users, so pauses persisted
        in the status DB survive a restart without ever spawning a process.
        """
        with self._lock:
            self.stopped_users |= set(users)

    def stop_user(self, user, force=False):
        """
        Request a cooperative stop of ``user``'s recorder: the in-flight
        recording is finalized and the process exits. The user stays excluded
        from restarts until ``resume_user`` (or removal + re-add). With
        ``force``, escalate to terminate/kill immediately.

        Returns False if the user is not currently managed.
        """
        with self._lock:
            p = self.processes.get(user)
            if p is None:
                return False
            self.stopped_users.add(user)
            event = self.stop_events.get(user)
            if event is not None:
                event.set()
            if force and p.is_alive():
                terminate_all([p])
            logger.info(f"Stop requested for @{user}" + (" (forced)" if force else ""))
            return True

    def resume_user(self, user):
        """Undo a stop_user: restart monitoring if the process is gone."""
        with self._lock:
            self.stopped_users.discard(user)
            if self.paused:
                # Only clear the stopped flag; the process starts on unpause.
                logger.info(f"@{user} will resume once monitoring is unpaused")
                return
            p = self.processes.get(user)
            if p is None or not p.is_alive():
                self.start_user(user)
                logger.info(f"Resumed monitoring @{user}")

    def stop_all(self, force=False):
        """
        Cooperatively stop every managed user's recorder. Each user is
        marked stopped (as with stop_user) and stays individually resumable.
        Returns the users that were stopped.
        """
        with self._lock:
            users = list(self.processes)
        return [user for user in users if self.stop_user(user, force=force)]

    def resume_all(self):
        """Undo stop_user/stop_all for every stopped user in the users file."""
        try:
            users = read_users_file(self.args.users_file)
        except OSError as e:
            logger.error(f"Failed to read users file: {e}")
            return []
        resumed = []
        with self._lock:
            for user in users:
                if user in self.stopped_users:
                    self.resume_user(user)
                    resumed.append(user)
        return resumed

    def pause(self):
        """
        Globally pause monitoring: cooperatively stop in-flight recordings
        and prevent any starts/restarts until unpause(). Unlike stop_user,
        users are NOT marked stopped, so unpausing restores the exact
        per-user state from before the pause.
        """
        with self._lock:
            if self.paused:
                return
            self.paused = True
            for user, event in self.stop_events.items():
                if user in self.stopped_users:
                    continue
                p = self.processes.get(user)
                if p is not None and p.is_alive():
                    event.set()
        logger.info("Monitoring paused: in-flight recordings are finalizing")

    def unpause(self):
        """Resume monitoring after pause(): restart non-stopped users."""
        with self._lock:
            if not self.paused:
                return
            self.paused = False
            for user, p in self.processes.items():
                if user in self.stopped_users:
                    continue
                # Exits caused by the pause must not count as failures.
                if not p.is_alive():
                    self._restart_state.pop(user, None)
        logger.info("Monitoring resumed")
        self.sync_users()

    def set_scale(self, enabled):
        """
        Set whether NEW recordings are re-encoded onto a single consistent
        resolution (removing TikTok's mid-stream size jumps on playback).

        Applies only to recordings started after this call; in-flight
        recordings keep the setting they were spawned with. Persisted to the
        status DB so the choice survives a restart.
        """
        enabled = bool(enabled)
        with self._lock:
            self.scale = enabled
        if self.status_db:
            try:
                from utils.status_store import StatusStore

                store = StatusStore(self.status_db)
                try:
                    store.set_scale(enabled)
                finally:
                    store.close()
            except Exception:
                logger.debug("Could not persist scale setting", exc_info=True)
        logger.info(
            "Size normalization for new recordings "
            + ("enabled" if enabled else "disabled")
        )

    def remove_user(self, user, reason="removed from users file"):
        """Stop monitoring ``user`` entirely and forget its state."""
        with self._lock:
            p = self.processes.pop(user, None)
            self.stop_events.pop(user, None)
            self.wake_events.pop(user, None)
            self._restart_state.pop(user, None)
            self.stopped_users.discard(user)
        if p is None:
            return
        if p.is_alive():
            p.terminate()
            p.join(timeout=5)
            # escalate to SIGKILL if it ignored SIGTERM, so it can't linger
            # as an orphan still holding the user's recording lock
            if p.is_alive():
                p.kill()
                p.join(timeout=5)
        logger.info(f"Stopped monitoring @{user} ({reason})")

    def sync_users(self):
        """Reconcile the process table with the users file."""
        try:
            users = read_users_file(self.args.users_file)
        except OSError as e:
            logger.error(f"Failed to read users file: {e}")
            return

        now = time.time()

        with self._lock:
            removed = set(self.processes) - set(users)
        for user in removed:
            self.remove_user(user)

        with self._lock:
            if self.paused:
                # Removals above are still honored; nothing starts or
                # restarts while monitoring is paused.
                return
            for user in users:
                proc = self.processes.get(user)
                if proc is None:
                    if user in self.stopped_users:
                        continue
                    self.start_user(user)
                    logger.info(f"Started monitoring @{user}")
                    continue

                if proc.is_alive():
                    continue

                if user in self.stopped_users:
                    continue

                # dead process: restart with exponential backoff so a failing
                # user doesn't respawn endlessly every poll
                state = self._restart_state.setdefault(
                    user, {"count": 0, "next_allowed": 0.0, "started": 0.0}
                )
                if state["started"] and now - state["started"] >= self.STABLE_AFTER:
                    state["count"] = 0

                if now < state["next_allowed"]:
                    continue

                state["count"] += 1
                state["next_allowed"] = now + min(
                    self.RESTART_BASE * 2 ** state["count"], self.RESTART_CAP
                )
                self.start_user(user)
                logger.info(f"Restarted monitoring @{user}")

    def check_now(self, user):
        """
        Ask ``user``'s recorder to re-check liveness immediately instead of
        waiting out the automatic-mode interval. Returns False when the user
        is paused, stopped, or has no live process.
        """
        with self._lock:
            if self.paused or user in self.stopped_users:
                return False
            p = self.processes.get(user)
            event = self.wake_events.get(user)
            if p is None or not p.is_alive() or event is None:
                return False
            event.set()
            return True

    def snapshot(self):
        """Per-user process view for the web dashboard."""
        with self._lock:
            return {
                user: {
                    "pid": p.pid,
                    "alive": p.is_alive(),
                    "stopped": user in self.stopped_users,
                }
                for user, p in self.processes.items()
            }

    def run_forever(self, poll_interval=TimeOut.USERS_FILE_POLL):
        while True:
            time.sleep(poll_interval)
            self.sync_users()

    def shutdown(self):
        terminate_all(self.processes)
