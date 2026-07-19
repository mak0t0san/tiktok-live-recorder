# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Overview

TikTok Live Recorder — a Python 3.11+ CLI that records TikTok live streams to MP4.
Managed with **uv**. HTTP requests use `curl_cffi` (Chrome impersonation) with a
`requests`-based fallback on Termux. Recording relies on the `ffmpeg` binary being
available on `PATH` (or passed via `-ffmpeg-path`).

## Commands

```bash
# install deps (incl. dev tools)
uv sync --extra dev

# run the CLI
uv run python src/main.py -h
uv run python src/main.py -user <username> -mode automatic

# tests
uv run --extra dev pytest

# lint / format (also enforced by CI and pre-commit)
uv run ruff format .
uv run ruff check .

# pre-commit hooks
uv run pre-commit install

# bump version (keeps pyproject.toml and src/utils/enums.py VERSION in sync)
uv run bump-my-version bump patch   # or: minor / major
```

> **Note:** `CONTRIBUTING.md` documents `uv sync --dev` + `uv run pytest` for tests —
> that fails because `dev` is an optional *extra*, not a dependency group. Use
> `uv run --extra dev pytest` instead (this is also what CI runs).

## Architecture

- `src/main.py` — parses CLI args, checks for the ffmpeg binary, checks for updates,
  then spawns one `multiprocessing.Process` per user (`-user` accepts a comma-separated
  list), or hands off to the web dashboard when `-web` is passed.
- `src/core/supervisor.py` — `Supervisor`: owns the user→process table for
  users-file mode (start/restart with backoff, removal, per-user cooperative stop
  via a `multiprocessing.Event` passed through `RecorderConfig.stop_event`; a
  second per-user event, `RecorderConfig.wake_event`, interrupts the automatic-
  mode recheck sleep for the dashboard's "Check now"). Shared by the CLI loop
  and the web UI. `preseed_stopped()` seeds per-user pauses persisted in the
  status DB before the first `sync_users()`.
- `src/utils/status_store.py` — SQLite (WAL) status DB written by recorder
  processes (`StatusReporter`, no-op `NullStatusReporter` for plain CLI runs) and
  read by the dashboard; lives in the output dir as `.tiktok-recorder-status.sqlite3`.
  Tables: `recordings` (current state per user), `profiles` (nickname/avatar),
  `user_settings` (persistent per-user pause flag), `recording_history` (one row
  per finished session, written by `StatusReporter.record_session` at recording
  end; keyed `(user, started_at)` so the post-conversion re-write only updates
  the output path).
- `src/web/` — FastAPI dashboard (`-web`, needs `uv sync --extra web`):
  `app.py` (routes + session middleware), `auth.py` (shared password →
  signed cookie), `preview.py` (on-demand HLS preview: a follower thread tails
  the growing FLV into `ffmpeg -c copy -f hls`; idle previews reaped after ~30s),
  `server.py` (bootstrap), `static/` (vanilla-JS frontend, vendored hls.js).
- `src/core/tiktok_recorder.py` — `TikTokRecorder`: orchestrates the two recording
  modes (`manual_mode`, `automatic_mode`) and the record loop,
  including CDN-candidate fallback and flv→mp4 conversion on completion.
- `src/core/tiktok_api.py` — `TikTokAPI`: all TikTok HTTP calls — room/user resolution,
  liveness checks, live-URL candidate extraction. Room-ID
  resolution goes through a tikrec signing service first, falling back to a legacy
  eulerstream API if tikrec is unreachable.
- `src/http_utils/http_client.py` — `HttpClient`: builds the `curl_cffi` session
  (falls back to plain `requests.Session` on Termux); handles proxy setup.
- `src/utils/`
  - `args_handler.py` — argparse setup + validation (`validate_and_parse_args`).
  - `recorder_config.py` — `RecorderConfig` dataclass passed between `main` and
    `TikTokRecorder`.
  - `enums.py` — `Mode` (MANUAL/AUTOMATIC), `Error`, `TikTokError`,
    `Info` (holds `VERSION` and the release banner/`NEW_FEATURES`).
  - `video_management.py` — ffmpeg-based flv→mp4 conversion.
  - `dependencies.py` — startup dependency/ffmpeg checks and install prompts.
  - `logger_manager.py` — singleton logger: INFO to stdout, ERROR to stderr, DEBUG+
    to rotating `tiktok-recorder.log`.
- `src/upload/telegram.py` — optional Telethon upload to Saved Messages, enabled via
  `-telegram` and configured in `src/telegram.json`.
- Runtime config lives alongside source: `src/cookies.json`, `src/telegram.json`.

## Conventions

- Package root is `src/` (`tool.setuptools.package-dir = {"" = "src"}`), so internal
  imports are unprefixed: `from core.tiktok_api import TikTokAPI`, `from utils.enums
  import Mode`, etc. Do not add a `src.` prefix. Tests replicate this by inserting
  `src/` onto `sys.path` (see `tests/test_tiktok_api.py`, `tests/cli/test_validate_args.py`).
- Ruff line-length is 88; formatting/linting enforced in CI (`.github/workflows/ruff.yml`)
  and pre-commit.
- Commit messages follow Conventional Commits (`feat`, `fix`, `docs`, `refactor`, `chore`).
- Don't hand-edit the version string — `src/utils/enums.py:VERSION` and
  `pyproject.toml` are kept in sync via `bump-my-version` (see `[tool.bumpversion]`
  in `pyproject.toml`).
- `src/cookies.json` and `src/telegram.json` are runtime credential files (session
  cookies, Telegram API id/hash) — never commit real secrets into them.
