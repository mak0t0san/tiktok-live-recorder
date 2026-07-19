import sys
import os
import multiprocessing

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_recordings_from_file(args, mode, cookies):
    from core.supervisor import Supervisor, install_shutdown_handlers, terminate_all
    from utils.logger_manager import logger
    from utils.status_store import StatusStore, status_db_path

    supervisor = Supervisor(args, mode, cookies)

    # Honor pauses persisted by the web dashboard, but never create the status
    # DB from a plain CLI run.
    db = status_db_path(args.output)
    if db.exists():
        store = StatusStore(db)
        try:
            supervisor.preseed_stopped(store.paused_users())
        finally:
            store.close()

    supervisor.sync_users()
    if not supervisor.processes and not supervisor.stopped_users:
        logger.error("No users found in the users file to monitor.")
        return

    install_shutdown_handlers(supervisor.processes)

    try:
        supervisor.run_forever()
    except KeyboardInterrupt:
        print("\n[!] Ctrl-C detected.")
        try:
            for p in supervisor.processes.values():
                p.join()
        except KeyboardInterrupt:
            print("\n[!] Forcefully terminating all processes.")
            terminate_all(supervisor.processes)


def run_recordings(args, mode, cookies):
    from core.supervisor import (
        build_config,
        install_shutdown_handlers,
        record_user,
        terminate_all,
    )

    if args.users_file:
        run_recordings_from_file(args, mode, cookies)
    elif isinstance(args.user, list):
        processes = []
        for user in args.user:
            config = build_config(args, mode, cookies, user=user)
            p = multiprocessing.Process(target=record_user, args=(config,))
            p.start()
            processes.append(p)
        install_shutdown_handlers(processes)
        try:
            for p in processes:
                p.join()
        except KeyboardInterrupt:
            print("\n[!] Ctrl-C detected.")
            try:
                for p in processes:
                    p.join()
            except KeyboardInterrupt:
                print("\n[!] Forcefully terminating all processes.")
                terminate_all(processes)
    else:
        config = build_config(args, mode, cookies, user=args.user)
        record_user(config)


def main():
    from utils.args_handler import validate_and_parse_args
    from utils.utils import read_cookies
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

        # read cookies from the config file
        cookies = read_cookies()

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

        # run the web dashboard or the recordings based on the parsed arguments
        if getattr(args, "web", False):
            from web.server import run_web

            run_web(args, mode, cookies)
        else:
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
