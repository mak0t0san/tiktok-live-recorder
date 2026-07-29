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
  `app.py` (routes + session middleware; the file APIs speak *output-relative*
  paths — `/api/files` walks the tree with `rglob` and returns a `name` like
  `alice/TK_alice_….mp4` plus a `user` field, and `/files` and
  `/api/files/convert` take that name as a `?name=` **query parameter** rather
  than a path segment so nested names survive proxies that normalise `%2F` in
  paths; `_resolve_in_output_dir()` is the containment guard), `auth.py` (shared password →
  signed cookie), `preview.py` (on-demand HLS preview: a follower thread tails
  the growing FLV into `ffmpeg -c copy -f hls`; idle previews reaped after ~30s),
  `server.py` (bootstrap), `static/` (vanilla-JS frontend, vendored hls.js).
- `src/core/tiktok_recorder.py` — `TikTokRecorder`: orchestrates the two recording
  modes (`manual_mode`, `automatic_mode`) and the record loop,
  including CDN-candidate fallback and flv→mp4 conversion on completion.
  `_build_output_path()` nests every recording under `<output>/<username>/`
  (via `utils.output_paths`), falling back to a flat write with a logged error
  if the folder can't be created — a live stream isn't repeatable, so losing the
  recording is worse than losing the layout.
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
  - `output_paths.py` — `user_dir_name()` (case-folds a username and reduces it
    to a safe single path component, `_unknown` if nothing survives) and
    `user_output_dir()` (that folder, created). The case-folding matches the
    precedent in `recording_lock()`, so `@Alice` and `@alice` share one folder.
  - `video_management.py` — ffmpeg-based flv→mp4 conversion. `_build_output_file()`
    rejoins on the *input's* directory, so converted files follow their
    `_flv.mp4` source into the per-user folder — load-bearing, keep it.
  - `dependencies.py` — startup dependency/ffmpeg checks and install prompts.
  - `logger_manager.py` — singleton logger: INFO to stdout, ERROR to stderr, DEBUG+
    to rotating `tiktok-recorder.log`.
- `src/upload/telegram.py` — optional Telethon upload to Saved Messages, enabled via
  `-telegram` and configured in `src/telegram.json`.
- `src/tools/migrate_to_user_folders.py` — one-off, hand-run script that moves
  pre-nesting flat recordings into their per-user folders (dry run by default,
  `--apply` to move, never overwrites). It lives under `src/` rather than a
  top-level `scripts/` because the Dockerfile's `COPY src/ ./` is the only path
  into the image, and container users are the ones with legacy files to migrate.
  Deliberately not wired into startup: moving a file out from under a live
  ffmpeg would corrupt that recording.
- Runtime config lives alongside source: `src/cookies.json`, `src/telegram.json`.
- `docker/` — container packaging: `entrypoint.sh` (seeds `/config`, drops to
  `PUID:PGID` via `gosu`, maps `TLR_*` env vars to CLI flags, then execs
  `main.py`), `healthcheck.sh` (probes `GET /login`, the only unauthenticated
  route besides `/api/login`), `truenas-compose.yaml` (TrueNAS Custom App).
  Built and published to GHCR by `.github/workflows/docker-publish.yml`, whose
  `smoke` job boots the image and logs in before the push is allowed.

### Container layout

The image flattens `src/` into `/app`, and the process **working directory is
`/data`** (the mounted volume). That is load-bearing: `users.txt`,
`tiktok-recorder.log` and `.tiktok-recorder.lock` are all resolved relative to
the cwd, so the cwd is what makes them persist. `cookies.json` and
`telegram.json` resolve relative to the *source tree* instead, so the entrypoint
symlinks `/app/*.json` to `/config/*.json`.

Consequences for new code:

- Anything written relative to `Path.cwd()` lands on the data volume — fine.
- Anything written relative to `Path(__file__)` lands in the immutable image
  and is lost on upgrade. Put it under the output dir or `/config` instead.
- Real credentials must never enter the build context; `.dockerignore` excludes
  `src/cookies.json` and `src/telegram.json`, and the Dockerfile writes empty
  templates to `/app/defaults/` for the entrypoint to seed from.

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
