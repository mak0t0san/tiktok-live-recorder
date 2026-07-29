import argparse
import os
import re

from utils.custom_exceptions import ArgsParseError
from utils.enums import Mode, Regex


def parse_args():
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(
        description="TikTok Live Recorder - A tool for recording live TikTok sessions.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "-url",
        dest="url",
        help="Record a live session from the TikTok URL.",
        action="store",
    )

    parser.add_argument(
        "-room_id",
        dest="room_id",
        help="Record a live session from the TikTok room ID.",
        action="store",
    )

    parser.add_argument(
        "-mode",
        dest="mode",
        help=(
            "Recording mode: (manual, automatic) [Default: manual]\n"
            "[manual] => Manual live recording.\n"
            "[automatic] => Automatic live recording when the user is live.\n"
            "Ignored when -web is set (dashboard always uses automatic)."
        ),
        default="manual",
        action="store",
    )

    parser.add_argument(
        "-automatic_interval",
        dest="automatic_interval",
        help="Sets the interval in minutes to check if the user is live in automatic mode. [Default: 5]",
        type=int,
        default=5,
        action="store",
    )

    parser.add_argument(
        "-proxy",
        dest="proxy",
        help=(
            "Use HTTP proxy to bypass login restrictions in some countries.\n"
            "Example: -proxy http://127.0.0.1:8080"
        ),
        action="store",
    )

    parser.add_argument(
        "-output",
        dest="output",
        help=("Specify the output directory where recordings will be saved.\n"),
        action="store",
    )

    parser.add_argument(
        "-duration",
        dest="duration",
        help="Specify the duration in seconds to record the live session [Default: None].",
        type=int,
        default=None,
        action="store",
    )

    parser.add_argument(
        "-telegram",
        dest="telegram",
        action="store_true",
        help="Activate the option to upload the video to Telegram at the end "
        "of the recording.\nRequires configuring the telegram.json file",
    )

    parser.add_argument(
        "-bitrate",
        dest="bitrate",
        help="Specify the bitrate for the output file (e.g. 1000k, 1M). Default: None (keep original)",
        action="store",
    )

    parser.add_argument(
        "-scale",
        dest="scale",
        action="store_true",
        help=(
            "Re-encode the recording onto a single consistent size (the "
            "highest resolution seen anywhere in the recording) so TikTok's "
            "mid-stream resolution changes no longer make the video shrink and "
            "grow on playback.\n"
            "Slower and slightly lossy since it re-encodes instead of copying."
        ),
    )

    parser.add_argument(
        "-ffmpeg-path",
        dest="ffmpeg_path",
        help="Specify a custom path to the ffmpeg binary. [Default: 'ffmpeg']",
        default=None,
        action="store",
    )

    parser.add_argument(
        "-web",
        dest="web",
        action="store_true",
        help=(
            "Start the web dashboard (primary mode).\n"
            "Manage monitored users, TikTok session cookies, and recordings "
            "from the UI. Cookies can also be supplied via TLR_SESSIONID_SS / "
            "TLR_TT_TARGET_IDC / TLR_MSTOKEN.\n"
            "Requires the 'web' extra: uv sync --extra web"
        ),
    )

    parser.add_argument(
        "-web-host",
        dest="web_host",
        default="0.0.0.0",
        help="Interface for the web dashboard. [Default: 0.0.0.0 (all interfaces)]",
        action="store",
    )

    parser.add_argument(
        "-web-port",
        dest="web_port",
        type=int,
        default=8000,
        help="Port for the web dashboard. [Default: 8000]",
        action="store",
    )

    parser.add_argument(
        "-web-password",
        dest="web_password",
        default=None,
        help=(
            "Password for the web dashboard (or set TLR_WEB_PASSWORD).\n"
            "A one-off password is generated and logged when unset."
        ),
        action="store",
    )

    parser.add_argument(
        "-no-update-check",
        dest="update_check",
        action="store_false",
        help=(
            "Disable the check for updates before running the program. "
            "By default, update checking is enabled."
        ),
    )

    args = parser.parse_args()

    return args


def validate_and_parse_args():
    args = parse_args()

    if args.web:
        if args.room_id or args.url:
            raise ArgsParseError(
                "-web cannot be combined with -room_id or -url; "
                "manage users from the web UI instead."
            )
        if args.mode not in ("manual", "automatic"):
            raise ArgsParseError("-web only supports automatic mode.")
        args.mode = "automatic"
    else:
        if not args.room_id and not args.url:
            raise ArgsParseError(
                "Missing target. Use -web to run the dashboard, or provide "
                "-url or -room_id for a one-shot recording."
            )

    if not args.mode:
        raise ArgsParseError(
            "Missing mode value. Please specify the mode (manual or automatic)."
        )
    if args.mode not in ["manual", "automatic"]:
        raise ArgsParseError(
            "Incorrect mode value. Choose between 'manual' or 'automatic'."
        )

    if args.url and not re.match(str(Regex.IS_TIKTOK_LIVE), args.url):
        raise ArgsParseError(
            "The provided URL does not appear to be a valid TikTok live URL."
        )

    if args.room_id and args.url:
        raise ArgsParseError("Please provide only one among room ID or URL.")

    if args.automatic_interval < 1:
        raise ArgsParseError(
            "Incorrect automatic_interval value. Must be one minute or more."
        )

    if args.duration is not None and args.duration <= 0:
        raise ArgsParseError("Incorrect duration value. Must be a positive number.")

    if args.output:
        try:
            os.makedirs(args.output, exist_ok=True)
        except OSError as e:
            raise ArgsParseError(f"Cannot create output directory: {e}")

    if args.mode == "manual":
        mode = Mode.MANUAL
    elif args.mode == "automatic":
        mode = Mode.AUTOMATIC

    return args, mode
