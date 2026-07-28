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

```bash
sudo docker run \
  -v ./output:/output \
  michele0303/tiktok-live-recorder:latest \
  -output /output \
  -user <username>
```

</details>

## Command-Line Usage

```bash
uv run python src/main.py [options]
```

### Options

| Flag | Description |
|------|-------------|
| `-user <USERNAME>` | Username(s) to record. Separate multiple with commas. |
| `-url <URL>` | TikTok live URL to record from. |
| `-room_id <ROOM_ID>` | Room ID to record from. |
| `-users-file <PATH>` | Path to a text file listing usernames to monitor in automatic mode, one per line (`#` starts a comment). Re-read periodically, so usernames can be added or removed while running; removing one stops its recording. Cannot be combined with `-user`, `-room_id`, or `-url`. |
| `-mode <MODE>` | Recording mode: `manual`, `automatic`. |
| `-automatic_interval <MIN>` | Polling interval in minutes (automatic mode only). |
| `-output <DIRECTORY>` | Directory where recordings will be saved. |
| `-duration <SECONDS>` | Stop recording after this many seconds. |
| `-proxy <URL>` | HTTP proxy to bypass regional restrictions. |
| `-bitrate <BITRATE>` | Output bitrate for post-processing (e.g. `1M`, `1000k`). |
| `-scale` | Re-encode the recording to a single consistent size (the highest resolution seen anywhere in the recording) so TikTok's mid-stream resolution changes don't make the video shrink and grow on playback. Slower and slightly lossy. |
| `-telegram` | Upload the recording to Telegram when done. Requires `telegram.json`. |
| `-web` | Start the web dashboard instead of the plain CLI (see [Web Dashboard](#web-dashboard)). |
| `-web-host <HOST>` | Interface for the web dashboard. Default: `0.0.0.0` (all interfaces). |
| `-web-port <PORT>` | Port for the web dashboard. Default: `8000`. |
| `-web-password <PASSWORD>` | Password for the web dashboard (or set `TLR_WEB_PASSWORD`). |
| `-no-update-check` | Skip the automatic update check on startup. |

### Recording Modes

- **`manual`** *(default)*: Records immediately if the user is currently live.
- **`automatic`**: Polls at regular intervals and records whenever the user goes live.

## Web Dashboard

A browser UI for managing recordings, built on the automatic users-file mode:

```bash
# one-time: install the web extra
uv sync --extra web

# start the dashboard (creates users.txt if missing)
TLR_WEB_PASSWORD=changeme uv run python src/main.py -web -output ./recordings
```

Then open `http://<your-machine>:8000` from any device on your network and log
in with the password. From the dashboard you can:

- **Add or remove monitored users** — edits the same users file the CLI uses,
  so the two stay interchangeable.
- **Watch recordings in progress** — live state, duration, and file size per
  user, updated every couple of seconds.
- **Stop individual recordings** — a stop finalizes the file (flush + MP4
  conversion) instead of killing the recorder mid-write; **Resume** restarts
  monitoring afterwards. Stops are persistent: a stopped (paused) user stays
  paused across dashboard restarts until you resume them, without losing
  their place in the users file.
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

## Guide

- [How to set cookies in cookies.json](https://github.com/Michele0303/tiktok-live-recorder/blob/main/docs/GUIDE.md#how-to-set-cookies)
- [How to get room_id](https://github.com/Michele0303/tiktok-live-recorder/blob/main/docs/GUIDE.md#how-to-get-room_id)
- [How to enable upload to Telegram](https://github.com/Michele0303/tiktok-live-recorder/blob/main/docs/GUIDE.md#how-to-enable-upload-to-telegram)

## Contributing

Contributions are welcome! Feel free to open an [issue](https://github.com/Michele0303/tiktok-live-recorder/issues) or submit a [pull request](https://github.com/Michele0303/tiktok-live-recorder/pulls).

## Legal ⚖️

This code is in no way affiliated with, authorized, maintained, sponsored or endorsed by TikTok or any of its affiliates or subsidiaries. Use at your own risk.
