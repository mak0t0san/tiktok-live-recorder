#!/bin/bash
#
# Docker HEALTHCHECK: the dashboard answers GET /login without a session
# (it is one of the two entries in PUBLIC_PATHS in src/web/app.py), so it
# works as a liveness probe with no authentication and no extra endpoint.
#
# Uses the image's own Python rather than curl, which is not installed.
#
set -euo pipefail

exec /app/.venv/bin/python - "${TLR_WEB_PORT:-8000}" <<'PY'
import sys
import urllib.request

url = f"http://127.0.0.1:{sys.argv[1]}/login"
try:
    with urllib.request.urlopen(url, timeout=5) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception as exc:
    print(f"healthcheck failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
