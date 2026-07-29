"""
FastAPI application for the web dashboard.

The app is a thin HTTP layer: process control goes through the Supervisor,
recording state comes from the SQLite status store, and the list of monitored
users lives in the users file (shared with the CLI).
"""

import json
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from utils.utils import add_user_to_file, read_users_file, remove_user_from_file
from utils.status_store import StatusStore
from utils.video_management import VideoManagement
from web.auth import SESSION_COOKIE, SESSION_MAX_AGE
from web.profiles import AVATAR_CACHE_DIRNAME

STATIC_DIR = Path(__file__).parent / "static"

# Paths reachable without a session (the login flow itself).
PUBLIC_PATHS = {"/login", "/api/login"}


class LoginBody(BaseModel):
    password: str


class UserBody(BaseModel):
    user: str


class StopBody(BaseModel):
    force: bool = False


class SettingsBody(BaseModel):
    scale: bool


def create_app(
    *,
    supervisor,
    users_file,
    output_dir,
    auth,
    status_db,
    previews=None,
    ffmpeg_path="ffmpeg",
):
    app = FastAPI(title="TikTok Live Recorder", docs_url=None, redoc_url=None)
    users_file = Path(users_file)
    output_dir = Path(output_dir)
    avatar_dir = output_dir / AVATAR_CACHE_DIRNAME

    @app.middleware("http")
    async def require_session(request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or auth.verify_token(
            request.cookies.get(SESSION_COOKIE)
        ):
            return await call_next(request)
        if path.startswith("/api/") or path.startswith("/preview/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse("/login")

    # -- auth ---------------------------------------------------------------

    @app.get("/login")
    def login_page():
        return FileResponse(STATIC_DIR / "login.html")

    @app.post("/api/login")
    def login(body: LoginBody):
        if not auth.check_password(body.password):
            return JSONResponse({"detail": "Wrong password"}, status_code=401)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            SESSION_COOKIE,
            auth.issue_token(),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/api/logout")
    def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

    # -- dashboard ----------------------------------------------------------

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    def status():
        users = read_users_file(users_file)
        procs = supervisor.snapshot()

        store = StatusStore(status_db)
        try:
            rows = {row["user"]: row for row in store.all()}
            profile_rows = store.profiles()
            paused_set = store.paused_users()
            history = store.latest_history()
        finally:
            store.close()

        entries = []
        for user in dict.fromkeys([*users, *procs]):
            proc = procs.get(user, {})
            row = rows.get(user, {})
            profile = profile_rows.get(user, {})
            last = history.get(user, {})
            entries.append(
                {
                    "user": user,
                    "nickname": profile.get("nickname"),
                    "avatar": (
                        f"/api/avatar/{user}"
                        if (avatar_dir / f"{user}.jpg").is_file()
                        else None
                    ),
                    "monitored": user in users,
                    "alive": proc.get("alive", False),
                    "stopped": proc.get("stopped", False),
                    "paused": user in paused_set or proc.get("stopped", False),
                    "last_recorded_at": last.get("ended_at"),
                    "last_duration": last.get("duration"),
                    "pid": proc.get("pid"),
                    "state": row.get("state", "starting" if proc else "unknown"),
                    "room_id": row.get("room_id"),
                    "output_path": row.get("output_path"),
                    "bytes_written": row.get("bytes_written", 0),
                    "started_at": row.get("started_at"),
                    "updated_at": row.get("updated_at"),
                    "error": row.get("error"),
                    "previewable": bool(
                        row.get("state") == "recording"
                        and row.get("output_path")
                        and Path(row["output_path"]).exists()
                    ),
                }
            )
        return {
            "now": time.time(),
            "paused": bool(getattr(supervisor, "paused", False)),
            "scale": bool(getattr(supervisor, "scale", False)),
            "recordings": entries,
        }

    @app.get("/api/avatar/{user}")
    def avatar(user: str):
        # forbid path traversal: the name must resolve inside the cache dir
        target = (avatar_dir / f"{user}.jpg").resolve()
        if target.parent != avatar_dir.resolve() or not target.is_file():
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(
            target,
            media_type="image/jpeg",
            headers={"Cache-Control": "max-age=3600"},
        )

    # -- users file ---------------------------------------------------------

    @app.get("/api/users")
    def list_users():
        return {"users": read_users_file(users_file)}

    @app.post("/api/users")
    def add_user(body: UserBody):
        try:
            added = add_user_to_file(users_file, body.user)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        if not added:
            return JSONResponse({"detail": "User already listed"}, status_code=409)
        supervisor.sync_users()
        return {"ok": True}

    @app.delete("/api/users/{user}")
    def delete_user(user: str):
        removed = remove_user_from_file(users_file, user)
        supervisor.remove_user(user, reason="removed via web UI")
        store = StatusStore(status_db)
        try:
            store.remove(user)
        finally:
            store.close()
        if not removed:
            return JSONResponse({"detail": "User not listed"}, status_code=404)
        return {"ok": True}

    # -- recording control --------------------------------------------------

    def _persist_paused(users, paused):
        store = StatusStore(status_db)
        try:
            for user in users:
                store.set_paused(user, paused)
        finally:
            store.close()

    @app.post("/api/recordings/stop-all")
    def stop_all_recordings(body: StopBody | None = None):
        stopped = supervisor.stop_all(force=bool(body and body.force))
        _persist_paused(stopped, True)
        return {"ok": True, "stopped": stopped}

    @app.post("/api/recordings/resume-all")
    def resume_all_recordings():
        resumed = supervisor.resume_all()
        _persist_paused(resumed, False)
        return {"ok": True, "resumed": resumed}

    @app.post("/api/monitoring/pause")
    def pause_monitoring():
        supervisor.pause()
        return {"ok": True, "paused": True}

    @app.post("/api/monitoring/resume")
    def resume_monitoring():
        supervisor.unpause()
        return {"ok": True, "paused": False}

    # -- settings -----------------------------------------------------------

    @app.get("/api/settings")
    def get_settings():
        return {"scale": bool(getattr(supervisor, "scale", False))}

    @app.post("/api/settings")
    def update_settings(body: SettingsBody):
        supervisor.set_scale(body.scale)
        return {"ok": True, "scale": body.scale}

    @app.post("/api/recordings/{user}/stop")
    def stop_recording(user: str, body: StopBody | None = None):
        # Keyed on the users file (not on a live process) so a user paused at
        # startup — who has no process — can still be managed.
        if user not in read_users_file(users_file):
            return JSONResponse({"detail": "User is not monitored"}, 404)
        if not supervisor.stop_user(user, force=bool(body and body.force)):
            # no live process: still exclude the user from future restarts
            supervisor.preseed_stopped([user])
        _persist_paused([user], True)
        return {"ok": True}

    @app.post("/api/recordings/{user}/resume")
    def resume_recording(user: str):
        if user not in read_users_file(users_file):
            return JSONResponse({"detail": "User is not monitored"}, 404)
        _persist_paused([user], False)
        supervisor.resume_user(user)
        return {"ok": True}

    @app.post("/api/recordings/{user}/check-now")
    def check_now(user: str):
        if supervisor.check_now(user):
            return {"ok": True}
        return JSONResponse(
            {"detail": "User is paused or not actively monitored"}, status_code=409
        )

    # -- completed recordings -----------------------------------------------

    def _active_raw_outputs() -> set[Path]:
        store = StatusStore(status_db)
        try:
            rows = store.all()
        finally:
            store.close()

        active = set()
        for row in rows:
            output_path = row.get("output_path")
            if not output_path:
                continue
            path = Path(output_path)
            if not path.name.endswith("_flv.mp4"):
                continue
            if row.get("state") in {"stopped", "error", "stale"}:
                continue
            # compared as whole paths, not basenames: two creators can produce
            # identically-named files in their own folders
            active.add(_resolve_quietly(path))
        return active

    def _resolve_quietly(path: Path) -> Path:
        try:
            return path.resolve()
        except OSError:
            return path

    def _relative_name(path: Path, root: Path) -> str:
        """Output-relative POSIX path, the identifier the API speaks in."""
        return path.relative_to(root).as_posix()

    def _resolve_in_output_dir(name: str) -> Path | None:
        """
        Map an API file name onto a real path inside the output directory.

        Returns ``None`` when the name escapes the output tree (``../``, an
        absolute path) or is not an ``.mp4``, so callers can 404 uniformly.
        """
        root = output_dir.resolve()
        target = _resolve_quietly(output_dir / name)
        if target == root or not target.is_relative_to(root):
            return None
        if target.suffix != ".mp4":
            return None
        return target

    @app.get("/api/files")
    def list_files():
        active_raw_outputs = _active_raw_outputs()
        root = output_dir.resolve()
        files = []
        # rglob, not glob: recordings live in per-user folders, and files made
        # before that change still sit at the output root
        for path in root.rglob("*.mp4"):
            if not path.is_file():
                continue
            stat = path.stat()
            raw = path.name.endswith("_flv.mp4")
            parent = path.parent
            files.append(
                {
                    "name": _relative_name(path, root),
                    "user": None if parent == root else parent.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    # still-raw FLV data (recording in progress or conversion
                    # failed); converted files drop the _flv suffix
                    "raw": raw,
                    "convertible": raw and path not in active_raw_outputs,
                }
            )
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return {"files": files}

    @app.post("/api/files/convert")
    def convert_file(name: str, scale: bool = False):
        target = _resolve_in_output_dir(name)
        if target is None or not target.is_file():
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if not target.name.endswith("_flv.mp4"):
            return JSONResponse(
                {"detail": "Only raw _flv.mp4 files can be converted"},
                status_code=409,
            )

        if target in _active_raw_outputs():
            return JSONResponse(
                {"detail": "File is still being recorded or converted"},
                status_code=409,
            )

        converted = VideoManagement.convert_flv_to_mp4(
            str(target), ffmpeg_path=ffmpeg_path, scale=scale
        )
        if not converted:
            return JSONResponse({"detail": "Conversion failed"}, status_code=500)
        return {
            "ok": True,
            "name": _relative_name(
                _resolve_quietly(Path(converted)), output_dir.resolve()
            ),
        }

    @app.get("/files")
    def download_file(name: str):
        # the name is a query parameter, not a path segment, so a nested
        # "alice/TK_....mp4" survives proxies that normalise %2F in paths
        target = _resolve_in_output_dir(name)
        if target is None or not target.is_file():
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(target, filename=target.name)

    # -- live preview (wired in web/preview.py) -----------------------------

    if previews is not None:
        previews.register(app, status_db=status_db)

    # -- diagnostics --------------------------------------------------------

    def _probe_health(base_url: str, timeout: int = 5) -> tuple[bool, str]:
        """
        Try to reach a service's /health endpoint.

        Returns (reachable: bool, detail: str).
        """
        probe = base_url.rstrip("/") + "/health"
        try:
            try:
                from curl_cffi import requests as cffi_requests

                r = cffi_requests.get(probe, timeout=timeout)
                if r.status_code < 600:
                    return True, f"HTTP {r.status_code}"
            except ImportError:
                pass
            try:
                import requests as req_lib

                r = req_lib.get(probe, timeout=timeout)
                if r.status_code < 600:
                    return True, f"HTTP {r.status_code}"
            except ImportError:
                pass
            import urllib.request

            with urllib.request.urlopen(probe, timeout=timeout) as resp:
                return True, f"HTTP {resp.status}"
        except Exception as exc:
            return False, str(exc)

    @app.get("/api/diagnostics")
    def diagnostics():
        """
        Return a snapshot of key service health indicators that the dashboard
        can display as a diagnostics panel.

        Fields
        ------
        cookies_file      : str          — path to cookies.json
        cookies_present   : bool         — file holds a non-empty sessionid_ss
        cookies_hint      : str          — actionable advice

        tikrec_url        : str          — tikrec signing service base URL
        tikrec_reachable  : bool | null  — probe result
        tikrec_detail     : str | null   — human-readable probe outcome
        """
        result: dict = {}

        # --- cookies ---
        cookies_path = Path(__file__).parent.parent / "cookies.json"
        if cookies_path.is_symlink():
            # In the container this points at the /config volume, which is the
            # path the user can actually edit.
            cookies_path = cookies_path.resolve()
        # Check for an actual session value rather than file size: a seeded
        # template with empty values is well over any size threshold but is
        # not usable, which would report a false "cookies found".
        try:
            cookies_present = bool(
                json.loads(cookies_path.read_text(encoding="utf-8")).get("sessionid_ss")
            )
        except (OSError, ValueError):
            cookies_present = False
        result["cookies_file"] = str(cookies_path)
        result["cookies_present"] = cookies_present
        result["cookies_hint"] = (
            "cookies.json found — if TikTok calls fail, try refreshing the "
            "session cookies."
            if cookies_present
            else (
                f"No TikTok session cookie configured. Add a valid "
                f"'sessionid_ss' to {cookies_path} to enable authenticated "
                f"requests."
            )
        )

        # --- tikrec ---
        tikrec_url = "https://tikrec.com"
        result["tikrec_url"] = tikrec_url
        tikrec_reachable, tikrec_detail = _probe_health(tikrec_url, timeout=6)
        result["tikrec_reachable"] = tikrec_reachable
        result["tikrec_detail"] = tikrec_detail

        return result

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
