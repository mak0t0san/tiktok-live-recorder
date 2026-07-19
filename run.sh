#!/bin/bash
#
# Launch the TikTok Live Recorder web dashboard. Just run ./run.sh
#
set -euo pipefail

# Run from the repo root regardless of where this is invoked from.
cd "$(dirname "$0")"

# Dashboard password. Override by exporting TLR_WEB_PASSWORD before running,
# e.g.  TLR_WEB_PASSWORD='my-secret' ./run.sh
: "${TLR_WEB_PASSWORD:=changeme}"
export TLR_WEB_PASSWORD

# Make sure the web extra is installed (a no-op once done).
uv sync --extra web

mkdir -p ./recordings

exec uv run --extra web \
  python src/main.py -web -output ./recordings -no-update-check "$@"
