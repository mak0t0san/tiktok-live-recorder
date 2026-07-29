<div align="center">

# TikTok Live Recorder 🎥

_TikTok Live Recorder is a tool for recording live streaming TikTok._

[![Telegram](https://img.shields.io/badge/Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://telegram.me/tiktokliverecorder)
![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)
[![Licence](https://img.shields.io/github/license/Ileriayo/markdown-badges?style=for-the-badge)](./LICENSE)
[![Stars](https://img.shields.io/github/stars/Michele0303/tiktok-live-recorder?style=for-the-badge)](https://github.com/Michele0303/tiktok-live-recorder/stargazers)
[![Release](https://img.shields.io/github/v/release/Michele0303/tiktok-live-recorder?style=for-the-badge)](https://github.com/Michele0303/tiktok-live-recorder/releases/latest)
[![Docker Pulls](https://img.shields.io/docker/pulls/michele0303/tiktok-live-recorder?style=for-the-badge&logo=docker&logoColor=white)](https://hub.docker.com/r/michele0303/tiktok-live-recorder)

The TikTok Live Recorder is a tool designed to easily capture and save live streaming sessions from TikTok. It records both audio and video, allowing users to revisit and preserve engaging live content for later enjoyment and analysis. It's a valuable resource for creators, researchers, and anyone who wants to capture memorable moments from TikTok live streams.

![preview](https://i.ibb.co/YTHp5DT/image.png)

</div>

## Table of Contents

- [Installation](#installation)
- [Usage](#command-line-usage)
- [Web Dashboard](#web-dashboard)
- [Running on TrueNAS](#running-on-truenas)
- [Guide](#guide)

## Installation

**Prerequisites:** [Git](https://git-scm.com), [Python 3.11+](https://www.python.org/downloads/), [FFmpeg](https://ffmpeg.org/download.html)

<details>
<summary>Windows 💻</summary>

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/Michele0303/tiktok-live-recorder
cd tiktok-live-recorder
uv venv
uv sync
uv run python src/main.py -h
```

</details>

<details>
<summary>Linux 🐧</summary>

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/Michele0303/tiktok-live-recorder
cd tiktok-live-recorder
uv venv
uv sync
uv run python src/main.py -h
```

</details>

<details>
<summary>macOS 🍎</summary>

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
brew install ffmpeg
git clone https://github.com/Michele0303/tiktok-live-recorder
cd tiktok-live-recorder
uv venv
uv sync
uv run python src/main.py -h
```

</details>

<details>
<summary>Android — Termux 📱</summary>

Install Termux from [F-Droid](https://f-droid.org/packages/com.termux/) (avoid the Play Store version).

```bash
pkg update && pkg upgrade
pkg install git ffmpeg uv tur-repo
pkg uninstall python
pkg install python3.11
git clone https://github.com/Michele0303/tiktok-live-recorder
cd tiktok-live-recorder
uv venv
uv sync
uv run python src/main.py -h
```

</details>

<details>
<summary>Docker 🐳</summary>

The image runs the web dashboard and is configured with environment variables.
Two volumes: `/config` for optional credentials (`telegram.json`, legacy
`cookies.json`), `/data` for recordings, the status database, and the log.

```bash
docker run -d --name tiktok-live-recorder \
  -p 8000:8000 \
  -e TLR_WEB_PASSWORD=changeme \
  -e TLR_SESSIONID_SS='your-sessionid_ss' \
  -e TLR_TT_TARGET_IDC=useast8 \
  -e TLR_MSTOKEN='your-msToken' \
  -e PUID=$(id -u) -e PGID=$(id -g) \
  -v ./config:/config \
  -v ./data:/data \
  ghcr.io/mak0t0san/tiktok-live-recorder:main
```

Branch builds are tagged with the branch name; `latest` is published only on a
tagged release.

To build it yourself: `docker build -t tiktok-live-recorder .`

See [Running on TrueNAS](#running-on-truenas) for the NAS setup, and
[Container environment variables](#container-environment-variables) for the
full list of settings.

</details>

## Command-Line Usage

```bash
uv run python src/main.py [options]
```

### Options

| Flag | Description |
|------|-------------|
| `-url <URL>` | TikTok live URL for a one-shot recording (without `-web`). |
| `-room_id <ROOM_ID>` | Room ID for a one-shot recording (without `-web`). |
| `-mode <MODE>` | Recording mode: `manual`, `automatic`. Ignored with `-web` (always automatic). |
| `-automatic_interval <MIN>` | Polling interval in minutes (automatic mode only). |
| `-output <DIRECTORY>` | Directory where recordings will be saved, one subfolder per user (see [Where recordings are saved](#where-recordings-are-saved)). |
| `-duration <SECONDS>` | Stop recording after this many seconds. |
| `-proxy <URL>` | HTTP proxy to bypass regional restrictions. |
| `-bitrate <BITRATE>` | Output bitrate for post-processing (e.g. `1M`, `1000k`). |
| `-scale` | Re-encode the recording to a single consistent size (the highest resolution seen anywhere in the recording) so TikTok's mid-stream resolution changes don't make the video shrink and grow on playback. Slower and slightly lossy. |
| `-telegram` | Upload the recording to Telegram when done. Requires `telegram.json`. |
| `-web` | Start the web dashboard (primary mode; see [Web Dashboard](#web-dashboard)). |
| `-web-host <HOST>` | Interface for the web dashboard. Default: `0.0.0.0` (all interfaces). |
| `-web-port <PORT>` | Port for the web dashboard. Default: `8000`. |
| `-web-password <PASSWORD>` | Password for the web dashboard (or set `TLR_WEB_PASSWORD`). |
| `-no-update-check` | Skip the automatic update check on startup. |

### Recording Modes

- **`manual`** *(default)*: Records immediately if the user is currently live.
- **`automatic`**: Polls at regular intervals and records whenever the user goes live.

### Where recordings are saved

Each user gets their own folder inside the output directory:

```
recordings/
├── alice/
│   └── TK_alice_2026.07.28_21-15-04.mp4
└── some_creator_99/
    └── TK_some_creator_99_2026.07.28_20-02-11.mp4
```

The folder name is the username lowercased and reduced to safe characters, so
`@Alice` and `@alice` share one folder.

If you recorded with an older version, those files are still sitting flat in the
output directory. A one-off script moves them into place — run it while nothing
is recording, since moving a file out from under a live recording would corrupt
it:

```bash
# show what would move (default)
uv run python src/tools/migrate_to_user_folders.py ./recordings

# actually move them
uv run python src/tools/migrate_to_user_folders.py ./recordings --apply
```

In Docker, run it against the data volume:

```bash
docker exec -it tiktok-live-recorder \
  python /app/tools/migrate_to_user_folders.py /data --apply
```

Nothing is ever overwritten: if a file already exists at the destination, the
script reports it as a conflict and leaves both copies alone.

## Web Dashboard

A browser UI for managing monitored users, TikTok session cookies, and
recordings. This is the primary way to run the app (especially in Docker /
TrueNAS):

```bash
# one-time: install the web extra
uv sync --extra web

# start the dashboard
TLR_WEB_PASSWORD=changeme uv run python src/main.py -web -output ./recordings
```

Then open `http://<your-machine>:8000` from any device on your network and log
in with the password. From the dashboard you can:

- **Add or remove monitored users** — stored in the status database (not a
  text file). Use **Export** / **Import** to move a username list between
  installs (plain `users.txt` format: one name per line).
- **Configure TikTok cookies** — paste `sessionid_ss`, `tt-target-idc`, and
  `msToken` in Settings, or set `TLR_SESSIONID_SS` / `TLR_TT_TARGET_IDC` /
  `TLR_MSTOKEN` (env wins and those fields become read-only in the UI).
- **Watch recordings in progress** — live state, duration, and file size per
  user, updated every couple of seconds.
- **Stop individual recordings** — a stop finalizes the file (flush + MP4
  conversion) instead of killing the recorder mid-write; **Resume** restarts
  monitoring afterwards. Stops are persistent across dashboard restarts.
- **Check now** — trigger an immediate liveness check for a waiting user
  instead of waiting out the recheck interval (default 5 minutes).
- **Sort the user grid** — by recording status, username, or display name;
  the choice is remembered per browser.
- **See recording history at a glance** — each card shows when the user was
  last recorded and how long that recording ran.
- **Preview live streams** — an on-demand HLS player fed from the recording
  already being written to disk (no extra TikTok connection, no re-encoding).
  Previews shut down automatically ~30 s after the last viewer leaves.
- **Browse and download completed recordings.**

Security notes: the dashboard binds to all interfaces by default so you can
reach it from other devices — anyone on the network who has the password has
full control, and traffic is plain HTTP. Keep it on a trusted LAN or a VPN
such as Tailscale; use `-web-host 127.0.0.1` for local-only access. If no
password is configured, a one-off password is generated and printed at startup.

## Running on TrueNAS

TrueNAS SCALE 24.10 and later run apps on Docker, so the dashboard installs as
a Custom App from a Compose file. A ready-to-edit one lives at
[`docker/truenas-compose.yaml`](docker/truenas-compose.yaml).

**0. Make the image pullable.** Packages published to GHCR are private by
default. Either make the package public (GitHub → Packages → the package →
Package settings → Change visibility), or the pull will fail with
`unauthorized`. Note that branch builds are tagged with the branch name;
`latest` appears only once a release is published.

**1. Create a dataset for the recordings.** Put it wherever you keep your own
data — **not** under `<pool>/ix-apps`, which TrueNAS manages itself. `568` is
TrueNAS SCALE's built-in `apps` user, which is what the container drops to:

```bash
zfs create <pool>/<parent>/tiktok-recordings
chown 568:568 /mnt/<pool>/<parent>/tiktok-recordings
```

This becomes `/data`: recordings, the status database and the log.
It grows with every recording, so keep it somewhere you can share over SMB and
snapshot on its own schedule. It must be a local ZFS path — the status database
runs SQLite in WAL mode and will corrupt on an NFS- or SMB-backed mount.

`/config` is optional for TikTok cookies (prefer env vars or the Settings UI)
and still holds `telegram.json` if you use Telegram uploads. How you provide
it depends on which install route you take, below.

**2a. Install via YAML** *(one paste, recommended)*. **Apps → Discover**, ⋮ menu,
**Install via YAML**, and paste [`docker/truenas-compose.yaml`](docker/truenas-compose.yaml)
with `<owner>`, `<pool>` and `<parent>` filled in and `TLR_WEB_PASSWORD`
changed. TrueNAS 25.10 and later require the top-level `services:` key, which
the file already has. This route needs `/config` as a second dataset — create
it the same way as above.

**2b. Or install through the Custom App form.** Set Repository to
`ghcr.io/<owner>/tiktok-live-recorder`, Tag to the branch name, Pull Policy to
*always*, and leave Entrypoint and Command empty. Add `TLR_WEB_PASSWORD`,
`PUID=568` and `PGID=568` as environment variables, forward port 8000, and
under Storage add two mounts:

| Mount path | Type | Notes |
|------------|------|-------|
| `/config` | **ixVolume** named `config` | TrueNAS creates and owns it under `<pool>/ix-apps/app_mounts/<appname>/`. Used for `telegram.json` (and optional legacy `cookies.json`). |
| `/data` | **Host Path** | The dataset from step 1. |

The container must start as **root** so it can seed `/config` and fix
ownership; it drops to `PUID:PGID` itself before recording anything. If the
Security Context section exposes a run-as user, set it to `0`.

**3. Add your cookies.** TikTok calls work better authenticated. Prefer one of:

1. Set `TLR_SESSIONID_SS` (and optionally `TLR_TT_TARGET_IDC` / `TLR_MSTOKEN`)
   as environment variables in the TrueNAS app form / Compose file, or
2. Log into the dashboard and paste them under **Settings**.

Env vars win over Settings (those fields become read-only when locked by the
environment). A legacy `cookies.json` on `/config` is migrated into Settings
once on first start if nothing else is configured. The diagnostics panel
reports whether a session cookie is present and where it came from.

Then open `http://<truenas-ip>:8000` and log in. Add users in the UI (or
**Import** a plain-text list). Upgrades are a pull-and-recreate: nothing
mutable lives in the image, so your user list, recordings, history and saved
cookies all survive on `/data`. If you previously ran the container as root,
`chown -R 568:568` the datasets once — the entrypoint only fixes ownership of
the top-level directories, since recursing through a recordings dataset on
every restart would be far too slow.

### Container environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TLR_WEB_PASSWORD` | *(unset)* | Dashboard password. A random one is logged at startup if unset. |
| `TLR_SESSIONID_SS` | *(unset)* | TikTok `sessionid_ss` cookie (preferred over Settings / cookies.json). |
| `TLR_TT_TARGET_IDC` | *(unset)* | TikTok `tt-target-idc` cookie (e.g. `useast8`). |
| `TLR_MSTOKEN` | *(unset)* | TikTok `msToken` cookie. |
| `PUID` / `PGID` | `568` | User the recorder runs as; owns everything it writes. `568` is the TrueNAS `apps` user. |
| `TZ` | `UTC` | Timezone for log and filename timestamps. |
| `TLR_WEB_PORT` | `8000` | Port inside the container. |
| `TLR_WEB_HOST` | `0.0.0.0` | Bind address. Leave as-is; a container must not bind to loopback. |
| `TLR_OUTPUT` | `/data/recordings` | Where recordings, the status database and the avatar cache go. |
| `TLR_SCALE` | `true` | Re-encode to one consistent resolution (the `-scale` flag). Set `false` to stream-copy. |
| `TLR_INTERVAL` | `5` | Minutes between liveness checks. |
| `TLR_PROXY` | *(unset)* | HTTP proxy for TikTok requests. |
| `TLR_TELEGRAM` | `false` | Upload finished recordings to Telegram. Needs `telegram.json` on `/config`. |
| `TLR_UPDATE_CHECK` | `false` | Leave off. Self-update rewrites the source tree, which an immutable image should not do — rebuild instead. |

Any flag without an environment variable can still be passed through the
Compose `command:` list; the entrypoint appends it to the generated arguments.

## Guide

- [How to set cookies](https://github.com/Michele0303/tiktok-live-recorder/blob/main/docs/GUIDE.md#how-to-set-cookies) (browser values for env vars / Settings)
- [How to get room_id](https://github.com/Michele0303/tiktok-live-recorder/blob/main/docs/GUIDE.md#how-to-get-room_id)
- [How to enable upload to Telegram](https://github.com/Michele0303/tiktok-live-recorder/blob/main/docs/GUIDE.md#how-to-enable-upload-to-telegram)

## Contributing

Contributions are welcome! Feel free to open an [issue](https://github.com/Michele0303/tiktok-live-recorder/issues) or submit a [pull request](https://github.com/Michele0303/tiktok-live-recorder/pulls).

## Legal ⚖️

This code is in no way affiliated with, authorized, maintained, sponsored or endorsed by TikTok or any of its affiliates or subsidiaries. Use at your own risk.
