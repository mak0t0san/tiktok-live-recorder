import sys
import os
import multiprocessing

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def record_user(config):
    from core.tiktok_recorder import TikTokRecorder
    from utils.logger_manager import logger

    try:
        TikTokRecorder(config).run()
    except Exception as e:
        logger.error(f"{e}", exc_info=True)


def _build_config(args, mode, cookies, user=None):
    from utils.recorder_config import RecorderConfig

    return RecorderConfig(
        url=args.url,
        user=user,
        room_id=args.room_id,
        mode=mode,
        automatic_interval=args.automatic_interval,
        cookies=cookies,
        proxy=args.proxy,
        output=args.output,
        duration=args.duration,
        use_telegram=args.telegram,
        bitrate=args.bitrate,
        ffmpeg_path=args.ffmpeg_path,
    )


def run_recordings_from_file(args, mode, cookies):
    import time
    from utils.utils import read_users_file
    from utils.enums import TimeOut
    from utils.logger_manager import logger

    processes = {}  # user -> Process
    restart_state = {}  # user -> {"count", "next_allowed", "started"}

    restart_base = TimeOut.USERS_FILE_POLL  # seconds
    restart_cap = 600  # max backoff between restarts of a failing user
    stable_after = 300  # reset the backoff once a process survives this long

    def start_user(user):
        config = _build_config(args, mode, cookies, user=user)
        p = multiprocessing.Process(target=record_user, args=(config,))
        p.start()
        processes[user] = p
        state = restart_state.setdefault(
            user, {"count": 0, "next_allowed": 0.0, "started": 0.0}
        )
        state["started"] = time.time()

    def sync_users():
        try:
            users = read_users_file(args.users_file)
        except OSError as e:
            logger.error(f"Failed to read users file: {e}")
            return

        now = time.time()

        # stop monitoring users removed from the file
        for user in set(processes) - set(users):
            p = processes.pop(user)
            restart_state.pop(user, None)
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
            logger.info(f"Stopped monitoring @{user} (removed from users file)")

        for user in users:
            proc = processes.get(user)
            if proc is None:
                start_user(user)
                logger.info(f"Started monitoring @{user}")
                continue

            if proc.is_alive():
                continue

            # dead process: restart with exponential backoff so a failing
            # user doesn't respawn endlessly every poll
            state = restart_state.setdefault(
                user, {"count": 0, "next_allowed": 0.0, "started": 0.0}
            )
            if state["started"] and now - state["started"] >= stable_after:
                state["count"] = 0

            if now < state["next_allowed"]:
                continue

            state["count"] += 1
            state["next_allowed"] = now + min(
                restart_base * 2 ** state["count"], restart_cap
            )
            start_user(user)
            logger.info(f"Restarted monitoring @{user}")

    sync_users()
    if not processes:
        logger.error("No users found in the users file to monitor.")
        return

    try:
        while True:
            time.sleep(TimeOut.USERS_FILE_POLL)
            sync_users()
    except KeyboardInterrupt:
        print("\n[!] Ctrl-C detected.")
        try:
            for p in processes.values():
                p.join()
        except KeyboardInterrupt:
            print("\n[!] Forcefully terminating all processes.")
            for p in processes.values():
                if p.is_alive():
                    p.terminate()


def run_recordings(args, mode, cookies):
    if args.users_file:
        run_recordings_from_file(args, mode, cookies)
    elif isinstance(args.user, list):
        processes = []
        for user in args.user:
            config = _build_config(args, mode, cookies, user=user)
            p = multiprocessing.Process(target=record_user, args=(config,))
            p.start()
            processes.append(p)
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
                for p in processes:
                    if p.is_alive():
                        p.terminate()
    else:
        config = _build_config(args, mode, cookies, user=args.user)
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

        # run the recordings based on the parsed arguments
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
