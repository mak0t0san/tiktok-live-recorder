"""
FastAPI application for the web dashboard.

The app is a thin HTTP layer: process control goes through the Supervisor,
recording state comes from the SQLite status store, and the list of monitored
users lives in the users file (shared with the CLI).
"""

import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from utils.custom_exceptions import TikTokRecorderError
from utils.utils import add_user_to_file, read_users_file, remove_user_from_file
from utils.status_store import StatusStore
from web.auth import SESSION_COOKIE, SESSION_MAX_AGE
from web.profiles import AVATAR_CACHE_DIRNAME

# How long a fetched following list stays served from cache.
FOLLOWING_CACHE_TTL = 300

STATIC_DIR = Path(__file__).parent / "static"

# Paths reachable without a session (the login flow itself).
PUBLIC_PATHS = {"/login", "/api/login"}


class LoginBody(BaseModel):
    password: str


class UserBody(BaseModel):
    user: str


class StopBody(BaseModel):
    force: bool = False


def create_app(
    *,
    supervisor,
    users_file,
    output_dir,
    auth,
    status_db,
    previews=None,
    api_factory=None,
    profiles=None,
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
        finally:
            store.close()

        entries = []
        for user in dict.fromkeys([*users, *procs]):
            proc = procs.get(user, {})
            row = rows.get(user, {})
            profile = profile_rows.get(user, {})
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

    # -- following picker -----------------------------------------------------

    following_cache = {"entries": None, "fetched_at": 0.0, "sec_uid": None}
    following_lock = threading.Lock()

    @app.get("/api/following")
    def following(refresh: bool = False):
        if api_factory is None:
            return JSONResponse(
                {"detail": "No TikTok session configured"}, status_code=503
            )

        # The lock also serializes concurrent fetches so at most one slow
        # TikTok pagination runs at a time.
        with following_lock:
            expired = time.time() - following_cache["fetched_at"] > FOLLOWING_CACHE_TTL
            if refresh or following_cache["entries"] is None or expired:
                api = api_factory()
                try:
                    sec_uid = following_cache["sec_uid"] or api.get_sec_uid()
                    if not sec_uid:
                        return JSONResponse(
                            {
                                "detail": "Could not resolve your TikTok "
                                "account — check src/cookies.json"
                            },
                            status_code=502,
                        )
                    entries = api.get_following(sec_uid)
                except TikTokRecorderError as e:
                    return JSONResponse({"detail": str(e)}, status_code=502)
                finally:
                    api.close()
                following_cache.update(
                    entries=entries, fetched_at=time.time(), sec_uid=sec_uid
                )
                if profiles is not None:
                    profiles.seed(entries)

            # Filter against the users file at request time so a just-added
            # user disappears from the picker immediately.
            listed = {u.lower() for u in read_users_file(users_file)}
            visible = [
                e
                for e in following_cache["entries"]
                if e["unique_id"].lower() not in listed
            ]
            return {
                "following": visible,
                "fetched_at": following_cache["fetched_at"],
            }

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
        if not removed:
            return JSONResponse({"detail": "User not listed"}, status_code=404)
        return {"ok": True}

    # -- recording control --------------------------------------------------

    @app.post("/api/recordings/stop-all")
    def stop_all_recordings(body: StopBody | None = None):
        stopped = supervisor.stop_all(force=bool(body and body.force))
        return {"ok": True, "stopped": stopped}

    @app.post("/api/recordings/resume-all")
    def resume_all_recordings():
        return {"ok": True, "resumed": supervisor.resume_all()}

    @app.post("/api/monitoring/pause")
    def pause_monitoring():
        supervisor.pause()
        return {"ok": True, "paused": True}

    @app.post("/api/monitoring/resume")
    def resume_monitoring():
        supervisor.unpause()
        return {"ok": True, "paused": False}

    @app.post("/api/recordings/{user}/stop")
    def stop_recording(user: str, body: StopBody | None = None):
        force = bool(body and body.force)
        if not supervisor.stop_user(user, force=force):
            return JSONResponse({"detail": "No recorder for that user"}, 404)
        return {"ok": True}

    @app.post("/api/recordings/{user}/resume")
    def resume_recording(user: str):
        if user not in read_users_file(users_file):
            return JSONResponse({"detail": "User is not monitored"}, 404)
        supervisor.resume_user(user)
        return {"ok": True}

    # -- completed recordings -----------------------------------------------

    @app.get("/api/files")
    def list_files():
        files = []
        for path in output_dir.glob("*.mp4"):
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    # still-raw FLV data (recording in progress or conversion
                    # failed); converted files drop the _flv suffix
                    "raw": path.name.endswith("_flv.mp4"),
                }
            )
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return {"files": files}

    @app.get("/files/{name}")
    def download_file(name: str):
        # forbid path traversal: the name must resolve inside output_dir
        target = (output_dir / name).resolve()
        if target.parent != output_dir.resolve() or target.suffix != ".mp4":
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if not target.is_file():
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(target, filename=name)

    # -- live preview (wired in web/preview.py) -----------------------------

    if previews is not None:
        previews.register(app, status_db=status_db)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
