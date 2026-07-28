"""
On-demand HLS preview of in-progress recordings.

The recorder already writes the live FLV stream to disk, so a preview never
touches TikTok: a follower thread tails the growing file into an ffmpeg
process that stream-copies (no re-encode, negligible CPU) into a small
sliding-window HLS playlist served to the browser. Previews start on the
first playlist request and are reaped ~30s after the last one, so idle
dashboards cost nothing.
"""

import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from utils.logger_manager import logger

FOLLOW_CHUNK = 256 * 1024
FOLLOW_IDLE_SLEEP = 0.5
PLAYLIST_WAIT = 8  # seconds to wait for ffmpeg to emit the first playlist


class Preview:
    """One running preview: follower thread + ffmpeg process + HLS out dir."""

    def __init__(self, user, source, ffmpeg_path):
        self.user = user
        self.source = Path(source)
        self.out_dir = Path(tempfile.mkdtemp(prefix=f"tlr-preview-{user}-"))
        self.playlist = self.out_dir / "index.m3u8"
        self.last_access = time.monotonic()
        self._stop = threading.Event()

        self._proc = subprocess.Popen(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "flv",
                "-i",
                "pipe:0",
                "-c",
                "copy",
                "-f",
                "hls",
                "-hls_time",
                "2",
                "-hls_list_size",
                "6",
                # Keep ~10 already-rotated segments on disk beyond the 6 in the
                # playlist. While ffmpeg churns through the on-disk backlog at
                # disk speed it produces (and would otherwise immediately delete)
                # segments far faster than the browser can fetch them; the extra
                # retained segments keep the ones a served playlist references
                # from being unlinked before the browser requests them.
                "-hls_delete_threshold",
                "10",
                "-hls_flags",
                "delete_segments+independent_segments",
                str(self.playlist),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self._follower = threading.Thread(target=self._follow, daemon=True)
        self._follower.start()

    def _drain_stderr(self):
        """Forward ffmpeg's stderr to the log so copy/codec failures are visible."""
        try:
            for raw in iter(self._proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    logger.warning(f"[preview @{self.user}] ffmpeg: {line}")
        except (OSError, ValueError):
            pass

    def _follow(self):
        """
        Pipe the growing FLV file into ffmpeg, waiting at EOF for more data.
        Reads from the start of the file: ffmpeg churns through the backlog
        at disk speed and the sliding playlist converges on the live edge.
        """
        try:
            with open(self.source, "rb") as src:
                while not self._stop.is_set() and self._proc.poll() is None:
                    chunk = src.read(FOLLOW_CHUNK)
                    if not chunk:
                        time.sleep(FOLLOW_IDLE_SLEEP)
                        continue
                    self._proc.stdin.write(chunk)
                    self._proc.stdin.flush()
        except (OSError, ValueError, BrokenPipeError):
            pass  # ffmpeg exited or the source vanished; the reaper cleans up
        finally:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            # If ffmpeg died on its own (not our stop request), it produced no
            # usable playlist — surface the exit code so the resulting 404 is
            # explained in the log rather than being a silent dead end.
            code = self._proc.poll()
            if code not in (None, 0) and not self._stop.is_set():
                logger.warning(f"[preview @{self.user}] ffmpeg exited with code {code}")

    def touch(self):
        self.last_access = time.monotonic()

    def alive(self):
        return self._proc.poll() is None

    def stop(self):
        self._stop.set()
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._follower.join(timeout=5)
        self._stderr_thread.join(timeout=5)
        shutil.rmtree(self.out_dir, ignore_errors=True)


class PreviewManager:
    """Starts previews on demand and reaps idle or dead ones."""

    def __init__(self, ffmpeg_path="ffmpeg", idle_timeout=30):
        self.ffmpeg_path = ffmpeg_path
        self.idle_timeout = idle_timeout
        self._previews = {}  # user -> Preview
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()

    def get_or_start(self, user, source):
        with self._lock:
            preview = self._previews.get(user)
            if (
                preview is not None
                and preview.alive()
                and preview.source == Path(source)
            ):
                preview.touch()
                return preview
            if preview is not None:
                preview.stop()
            preview = Preview(user, source, self.ffmpeg_path)
            self._previews[user] = preview
            logger.info(f"Preview started for @{user}")
            return preview

    def get(self, user):
        with self._lock:
            return self._previews.get(user)

    def _reap_loop(self):
        while not self._closed.wait(5):
            now = time.monotonic()
            with self._lock:
                expired = [
                    (user, p)
                    for user, p in self._previews.items()
                    if not p.alive() or now - p.last_access > self.idle_timeout
                ]
                for user, _ in expired:
                    del self._previews[user]
            for user, preview in expired:
                preview.stop()
                logger.info(f"Preview stopped for @{user}")

    def shutdown(self):
        self._closed.set()
        with self._lock:
            previews = list(self._previews.values())
            self._previews.clear()
        for preview in previews:
            preview.stop()

    # -- HTTP routes ---------------------------------------------------------

    def register(self, app, *, status_db):
        from fastapi.responses import FileResponse, JSONResponse

        from utils.status_store import StatusStore

        def _not_found():
            return JSONResponse({"detail": "No active recording"}, status_code=404)

        @app.get("/preview/{user}/index.m3u8")
        def playlist(user: str):
            store = StatusStore(status_db)
            try:
                row = store.get(user)
            finally:
                store.close()

            if (
                not row
                or row["state"] != "recording"
                or not row["output_path"]
                or not Path(row["output_path"]).exists()
            ):
                return _not_found()

            preview = self.get_or_start(user, row["output_path"])

            deadline = time.monotonic() + PLAYLIST_WAIT
            while not preview.playlist.exists() and time.monotonic() < deadline:
                if not preview.alive():
                    return _not_found()
                time.sleep(0.2)
            if not preview.playlist.exists():
                return _not_found()

            return FileResponse(
                preview.playlist,
                media_type="application/vnd.apple.mpegurl",
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/preview/{user}/{segment}")
        def segment(user: str, segment: str):
            preview = self.get(user)
            if preview is None:
                return _not_found()
            preview.touch()

            target = (preview.out_dir / segment).resolve()
            if target.parent != preview.out_dir.resolve() or target.suffix != ".ts":
                return _not_found()
            if not target.is_file():
                return _not_found()
            return FileResponse(
                target,
                media_type="video/mp2t",
                headers={"Cache-Control": "no-store"},
            )
