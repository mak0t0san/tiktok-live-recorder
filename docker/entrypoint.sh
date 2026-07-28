#!/bin/bash
#
# Container entrypoint for the TikTok Live Recorder web dashboard.
#
# Runs as root to seed /config and fix ownership, then drops to PUID:PGID.
# The process working directory is /data, which is what makes users.txt,
# tiktok-recorder.log and .tiktok-recorder.lock land on the mounted volume —
# the app resolves all three relative to the cwd.
#
set -euo pipefail

PUID="${PUID:-568}"
PGID="${PGID:-568}"

# Overridable so the script can be exercised outside a container; inside the
# image these are always the mounted volumes.
DATA_DIR="${TLR_DATA_DIR:-/data}"
CONFIG_DIR="${TLR_CONFIG_DIR:-/config}"
DEFAULTS_DIR="${TLR_DEFAULTS_DIR:-/app/defaults}"
APP_DIR="${TLR_APP_DIR:-/app}"

TLR_OUTPUT="${TLR_OUTPUT:-$DATA_DIR/recordings}"
TLR_USERS_FILE="${TLR_USERS_FILE:-$DATA_DIR/users.txt}"
TLR_WEB_PORT="${TLR_WEB_PORT:-8000}"
TLR_WEB_HOST="${TLR_WEB_HOST:-0.0.0.0}"

# Accept the usual spellings for on/off env vars. Lowercased via tr rather
# than ${x,,} so the script stays runnable under bash 3.2 for local testing.
is_true() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1 | true | yes | on) return 0 ;;
    *) return 1 ;;
  esac
}

# --- config files ---------------------------------------------------------
#
# cookies.json and telegram.json are resolved relative to the source tree
# (src/utils/utils.py, src/web/app.py), which is baked into the image. Seed
# them into the /config volume and symlink them back so edits persist.
mkdir -p "$CONFIG_DIR"
for name in cookies.json telegram.json; do
  if [[ ! -e "$CONFIG_DIR/$name" ]]; then
    cp "$DEFAULTS_DIR/$name" "$CONFIG_DIR/$name"
    echo "[+] seeded $CONFIG_DIR/$name from template"
  fi
  ln -sfn "$CONFIG_DIR/$name" "$APP_DIR/$name"
done

# --- data dir -------------------------------------------------------------

mkdir -p "$DATA_DIR" "$TLR_OUTPUT"
touch "$TLR_USERS_FILE"

# Ownership is fixed non-recursively on purpose: a recursive chown over a
# recordings dataset would take minutes-to-hours on every restart. Pre-existing
# files from an earlier run under a different UID need a one-time manual
# `chown -R` (see the TrueNAS section of the README).
chown "$PUID:$PGID" "$DATA_DIR" "$CONFIG_DIR" "$TLR_OUTPUT"
chown "$PUID:$PGID" "$CONFIG_DIR"/*.json 2>/dev/null || true
for f in "$TLR_USERS_FILE" "$DATA_DIR"/tiktok-recorder.log* \
  "$DATA_DIR"/.tiktok-recorder.lock; do
  if [[ -e "$f" ]]; then
    chown "$PUID:$PGID" "$f"
  fi
done

# --- build the command line ----------------------------------------------
#
# TLR_WEB_PASSWORD is deliberately absent here: src/web/server.py reads it
# straight from the environment, so it never appears in the process list.
args=(
  -web
  -web-host "$TLR_WEB_HOST"
  -web-port "$TLR_WEB_PORT"
  -output "$TLR_OUTPUT"
  -users-file "$TLR_USERS_FILE"
)

if is_true "${TLR_SCALE:-true}"; then
  args+=(-scale)
fi
if is_true "${TLR_TELEGRAM:-false}"; then
  args+=(-telegram)
fi

# Self-update rewrites the source tree in place, which is wrong for an
# immutable image — rebuild and repull instead.
if ! is_true "${TLR_UPDATE_CHECK:-false}"; then
  args+=(-no-update-check)
fi

if [[ -n "${TLR_PROXY:-}" ]]; then
  args+=(-proxy "$TLR_PROXY")
fi
if [[ -n "${TLR_INTERVAL:-}" ]]; then
  args+=(-automatic_interval "$TLR_INTERVAL")
fi

if [[ -z "${TLR_WEB_PASSWORD:-}" ]]; then
  echo "[!] TLR_WEB_PASSWORD is not set; a random password will be printed below."
fi

cd "$DATA_DIR"

# Trailing "$@" passes through any extra flags set in the Compose command:.
exec gosu "$PUID:$PGID" "$APP_DIR/.venv/bin/python" "$APP_DIR/main.py" \
  "${args[@]}" "$@"
