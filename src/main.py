import sys
import os
import multiprocessing

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_recordings(args, mode, cookies):
    from core.supervisor import build_config, record_user

    config = build_config(args, mode, cookies, user=None)
    # One-shot -url / -room_id recording (no multi-user supervisor).
    record_user(config)


def main():
    from utils.args_handler import validate_and_parse_args
    from utils.cookies import resolve_cookies
    from utils.logger_manager import logger
    from utils.custom_exceptions import TikTokRecorderError
    from utils.dependencies import check_ffmpeg
    from check_updates import check_updates

    try:
        # validate and parse command line arguments
        args, mode = validate_and_parse_args()

        # check ffmpeg binary (supports custom path via -ffmpeg-path)
        check_ffmpeg(args.ffmpeg_path or "ffmpeg")

        # check for updates
        if args.update_check is True:
            logger.info("Checking for updates...\n")
            if check_updates():
                sys.exit()
        else:
            logger.info("Skipped update check\n")

        # warn (don't block) if another instance is already running: the real
        # duplicate-recording guard is the per-user lock, but running multiple
        # whole instances is a common way to end up with duplicate recordings
        import atexit
        from pathlib import Path
        from utils.recording_lock import FileLock

        instance_lock = FileLock(Path.cwd() / ".tiktok-recorder.lock")
        if not instance_lock.acquire():
            logger.warning(
                "Another TikTok Live Recorder instance appears to be running. "
                "Running multiple instances can produce duplicate recordings."
            )
        else:
            atexit.register(instance_lock.release)

        # run the web dashboard or a one-shot recording
        if getattr(args, "web", False):
            from web.server import run_web

            run_web(args, mode)
        else:
            cookies = resolve_cookies(None)
            run_recordings(args, mode, cookies)

    except TikTokRecorderError as ex:
        logger.error(f"Application Error: {ex}")

    except KeyboardInterrupt:
        logger.info("\n[!] Stopped by user.")

    except Exception as ex:
        logger.critical(f"Generic Error: {ex}", exc_info=True)


if __name__ == "__main__":
    # print the banner
    from utils.utils import banner

    banner()

    # check and install dependencies
    from utils.dependencies import check_and_install_dependencies

    check_and_install_dependencies()

    # required for multiprocessing support in frozen executables (e.g. PyInstaller on Windows)
    multiprocessing.freeze_support()

    # run
    main()
