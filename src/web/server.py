"""Bootstraps the web UI: supervisor + status DB + uvicorn."""

import os
import secrets
import threading
from pathlib import Path

from utils.logger_manager import logger


def run_web(args, mode, cookies):
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "The web UI needs extra dependencies. Install them with:\n"
            "    uv sync --extra web"
        )

    from core.supervisor import Supervisor, install_shutdown_handlers
    from utils.status_store import status_db_path
    from web.app import create_app
    from web.auth import SessionAuth
    from web.preview import PreviewManager

    password = args.web_password or os.environ.get("TLR_WEB_PASSWORD")
    if not password:
        password = secrets.token_urlsafe(9)
        logger.warning(
            f"No web password configured; using a one-off password for this "
            f"run: {password}\n"
            "Set TLR_WEB_PASSWORD (or pass -web-password) to keep it stable."
        )

    output_dir = Path(args.output) if args.output else Path.cwd()
    status_db = status_db_path(output_dir)

    supervisor = Supervisor(args, mode, cookies, status_db=status_db)
    supervisor.sync_users()
    install_shutdown_handlers(supervisor.processes)

    poller = threading.Thread(target=supervisor.run_forever, daemon=True)
    poller.start()

    previews = PreviewManager(ffmpeg_path=args.ffmpeg_path or "ffmpeg")

    app = create_app(
        supervisor=supervisor,
        users_file=args.users_file,
        output_dir=output_dir,
        auth=SessionAuth(password),
        status_db=status_db,
        previews=previews,
    )

    logger.info(f"Web UI listening on http://{args.web_host}:{args.web_port}")
    try:
        uvicorn.run(app, host=args.web_host, port=args.web_port, log_level="warning")
    finally:
        previews.shutdown()
        supervisor.shutdown()
