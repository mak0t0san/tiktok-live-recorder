"""
FastAPI application for the web dashboard.

The app is a thin HTTP layer: process control goes through the Supervisor,
recording state comes from the SQLite status store, and the list of monitored
users lives in the users file (shared with the CLI).
"""

import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from utils.utils import add_user_to_file, read_users_file, remove_user_from_file
from utils.status_store import StatusStore
from web.auth import SESSION_COOKIE, SESSION_MAX_AGE

STATIC_DIR = Path(__file__).parent / "static"

# Paths reachable without a session (the login flow itself).
PUBLIC_PATHS = {"/login", "/api/login"}


class LoginBody(BaseModel):
    password: str


class UserBody(BaseModel):
    user: str


class StopBody(BaseModel):
    force: bool = False


def create_app(*, supervisor, users_file, output_dir, auth, status_db, previews=None):
    app = FastAPI(title="TikTok Live Recorder", docs_url=None, redoc_url=None)
    users_file = Path(users_file)
    output_dir = Path(output_dir)

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
        finally:
            store.close()

        entries = []
        for user in dict.fromkeys([*users, *procs]):
            proc = procs.get(user, {})
            row = rows.get(user, {})
            entries.append(
                {
                    "user": user,
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
        return {"now": time.time(), "recordings": entries}

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
