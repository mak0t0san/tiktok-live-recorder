"""Bootstraps the web UI: supervisor + status DB + uvicorn."""

import os
import secrets
import threading
from pathlib import Path

from utils.logger_manager import logger


def run_web(args, mode):
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "The web UI needs extra dependencies. Install them with:\n"
            "    uv sync --extra web"
        )

    from core.supervisor import Supervisor, install_shutdown_handlers
    from core.tiktok_api import TikTokAPI
    from utils.cookies import migrate_legacy_cookies_file, resolve_cookies
    from utils.status_store import StatusStore, status_db_path
    from web.app import create_app
    from web.auth import SessionAuth
    from web.preview import PreviewManager
    from web.profiles import AVATAR_CACHE_DIRNAME, ProfileRefresher

    password = args.web_password or os.environ.get("TLR_WEB_PASSWORD")
    if not password:
        password = secrets.token_urlsafe(9)
        logger.warning(
            f"No web password configured; using a one-off password for this "
            f"run: {password}\n"
            "Set TLR_WEB_PASSWORD (or pass -web-password) to keep it stable."
        )

    output_dir = Path(args.output) if args.output else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)
    status_db = status_db_path(output_dir)

    store = StatusStore(status_db)
    try:
        # One-time migrations from the legacy file-based config.
        legacy_users = Path(
            os.environ.get("TLR_USERS_FILE")
            or getattr(args, "users_file", None)
            or Path.cwd() / "users.txt"
        )
        imported = store.migrate_users_file_if_needed(legacy_users)
        if imported:
            logger.info(
                f"Migrated {len(imported)} user(s) from {legacy_users} into "
                "the status database."
            )
        if migrate_legacy_cookies_file(store):
            logger.info(
                "Migrated TikTok session cookies from cookies.json into Settings."
            )
        cookies = resolve_cookies(store)
        paused = store.paused_users()
    finally:
        store.close()

    supervisor = Supervisor(args, mode, cookies, status_db=status_db)
    supervisor.preseed_stopped(paused)
    supervisor.sync_users()
    install_shutdown_handlers(supervisor.processes)

    poller = threading.Thread(target=supervisor.run_forever, daemon=True)
    poller.start()

    previews = PreviewManager(ffmpeg_path=args.ffmpeg_path or "ffmpeg")

    def api_factory():
        return TikTokAPI(proxy=args.proxy, cookies=supervisor.cookies)

    profiles = ProfileRefresher(
        status_db=status_db,
        api_factory=api_factory,
        cache_dir=output_dir / AVATAR_CACHE_DIRNAME,
    )
    profiles.start()

    app = create_app(
        supervisor=supervisor,
        output_dir=output_dir,
        auth=SessionAuth(password),
        status_db=status_db,
        previews=previews,
        ffmpeg_path=args.ffmpeg_path or "ffmpeg",
    )

    logger.info(f"Web UI listening on http://{args.web_host}:{args.web_port}")
    try:
        uvicorn.run(app, host=args.web_host, port=args.web_port, log_level="warning")
    finally:
        profiles.shutdown()
        previews.shutdown()
        supervisor.shutdown()
