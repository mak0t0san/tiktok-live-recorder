# ---- Stage 1: Builder ----
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better layer caching.
# --extra web pulls in fastapi/uvicorn; without it the -web dashboard cannot start.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra web --no-install-project

# Copy source code. Note this flattens src/ into /app, so main.py is at
# /app/main.py and the unprefixed imports (core.*, utils.*) resolve from
# /app as sys.path[0].
COPY src/ ./

# ---- Stage 2: Runtime ----
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

# ffmpeg does the recording and flv->mp4 conversion; gosu drops privileges to
# PUID:PGID; tini reaps orphaned ffmpeg children and forwards SIGTERM so a
# `docker stop` shuts recordings down cleanly.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    gosu \
    tini \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

# Templates for the runtime credential files. The real ones are excluded from
# the build context by .dockerignore and live on the /config volume; the
# entrypoint seeds these on first run and symlinks them into /app, where
# src/utils/utils.py and src/web/app.py resolve them.
# Values are empty rather than "<paste ... here>" placeholders: cookies are
# fed straight into the HTTP session (HttpClient.__init__), so placeholder text
# would be sent to TikTok as a real cookie value.
RUN mkdir -p /app/defaults && \
    printf '{\n  "sessionid_ss": "",\n  "tt-target-idc": "useast2a",\n  "msToken": ""\n}\n' \
    > /app/defaults/cookies.json && \
    printf '{\n  "api_id": "",\n  "api_hash": "",\n  "chat_id": "me"\n}\n' \
    > /app/defaults/telegram.json

COPY docker/entrypoint.sh docker/healthcheck.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh

# The working directory is the data volume, not the code: users.txt,
# tiktok-recorder.log and .tiktok-recorder.lock are all resolved relative to
# the cwd, so this is what makes them persist.
WORKDIR /data

VOLUME ["/data", "/config"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["/usr/local/bin/healthcheck.sh"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
