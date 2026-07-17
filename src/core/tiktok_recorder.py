import time
from http.client import HTTPException
from pathlib import Path
from threading import Event, Thread

from requests import HTTPError, RequestException

from core.tiktok_api import TikTokAPI
from utils.logger_manager import logger
from utils.recorder_config import RecorderConfig
from utils.video_management import VideoManagement
from utils.custom_exceptions import LiveNotFound, UserLiveError, TikTokRecorderError
from utils.enums import Mode, Error, TimeOut, TikTokError


class TikTokRecorder:
    def __init__(self, config: RecorderConfig):
        self.tiktok = TikTokAPI(proxy=config.proxy, cookies=config.cookies)

        self.url = config.url
        self.user = config.user
        self.room_id = config.room_id
        self.mode = config.mode
        self.automatic_interval = config.automatic_interval
        self.duration = config.duration
        self.output = config.output
        self.bitrate = config.bitrate
        self.ffmpeg_path = config.ffmpeg_path
        self.use_telegram = config.use_telegram
        self._proxy = config.proxy
        self._cookies = config.cookies

        # Cooperative shutdown signal for recordings running in worker
        # threads (followers mode): KeyboardInterrupt only reaches the
        # main thread, so workers poll this event instead.
        self._stop_event = Event()

    def _setup(self):
        """Resolve user/room data and validate prerequisites via network calls."""
        if self.mode == Mode.FOLLOWERS:
            self.check_country_blacklisted()

            self.sec_uid = self.tiktok.get_sec_uid()
            if self.sec_uid is None:
                raise TikTokRecorderError("Failed to retrieve sec_uid.")

            logger.info("Followers mode activated\n")
        else:
            if self.url:
                self.user, self.room_id = self.tiktok.get_room_and_user_from_url(
                    self.url
                )

            if not self.user:
                self.user = self.tiktok.get_user_from_room_id(self.room_id)

            if not self.room_id:
                self.room_id = self.tiktok.get_room_id_from_user(self.user)

            self.check_country_blacklisted()

            logger.info(f"USERNAME: {self.user}" + ("\n" if not self.room_id else ""))
            if self.room_id:
                logger.info(
                    f"ROOM_ID:  {self.room_id}"
                    + ("\n" if not self.tiktok.is_room_alive(self.room_id) else "")
                )

        # If proxy was used for the initial checks, switch to a direct connection
        # for the actual stream download to avoid proxy bottlenecks
        if self._proxy:
            self.tiktok = TikTokAPI(proxy=None, cookies=self._cookies)

    def run(self):
        """
        Resolves prerequisites and runs the recorder in the selected mode.

        If the mode is MANUAL, it checks if the user is currently live and
        if so, starts recording.

        If the mode is AUTOMATIC, it continuously checks if the user is live
        and if not, waits for the specified timeout before rechecking.
        If the user is live, it starts recording.

        if the mode is FOLLOWERS, it continuously checks the followers of
        the authenticated user. If any follower is live, it starts recording
        their live stream in a separate process.
        """
        self._setup()

        if self.mode == Mode.MANUAL:
            self.manual_mode()

        elif self.mode == Mode.AUTOMATIC:
            self.automatic_mode()

        elif self.mode == Mode.FOLLOWERS:
            self.followers_mode()

    def manual_mode(self):
        if not self.tiktok.is_room_alive(self.room_id):
            raise UserLiveError(f"@{self.user}: {TikTokError.USER_NOT_CURRENTLY_LIVE}")

        self.start_recording(self.user, self.room_id)

    def automatic_mode(self):
        while True:
            try:
                self.room_id = self.tiktok.get_room_id_from_user(self.user)
                self.manual_mode()

            except (UserLiveError, LiveNotFound) as ex:
                logger.info(ex)
                logger.info(
                    f"Waiting {self.automatic_interval} minutes before recheck\n"
                )
                time.sleep(self.automatic_interval * TimeOut.ONE_MINUTE)

            except (ConnectionError, RequestException, HTTPException):
                logger.error(Error.CONNECTION_CLOSED_AUTOMATIC)
                time.sleep(TimeOut.CONNECTION_CLOSED * TimeOut.ONE_MINUTE)

    def followers_mode(self):
        active_recordings = {}  # follower -> Thread

        while True:
            try:
                followers = self.tiktok.get_followers_list(self.sec_uid)

                for follower in followers:
                    if follower in active_recordings:
                        if not active_recordings[follower].is_alive():
                            logger.info(f"Recording of @{follower} finished.")
                            del active_recordings[follower]
                        else:
                            continue

                    try:
                        room_id = self.tiktok.get_room_id_from_user(follower)

                        if not room_id or not self.tiktok.is_room_alive(room_id):
                            continue

                        logger.info(f"@{follower} is live. Starting recording...")

                        thread = Thread(
                            target=self.start_recording,
                            args=(follower, room_id),
                        )
                        thread.start()
                        active_recordings[follower] = thread

                        time.sleep(2.5)

                    except TikTokRecorderError as e:
                        logger.error(f"Error while processing @{follower}: {e}")
                        continue

                    except Exception as e:
                        logger.error(
                            f"Unexpected error processing @{follower}: {e}",
                            exc_info=True,
                        )
                        continue

                print()
                logger.info(
                    f"Waiting {self.automatic_interval} minutes for the next check..."
                )
                time.sleep(self.automatic_interval * TimeOut.ONE_MINUTE)

            except (UserLiveError, LiveNotFound) as ex:
                logger.info(ex)
                logger.info(
                    f"Waiting {self.automatic_interval} minutes before recheck\n"
                )
                time.sleep(self.automatic_interval * TimeOut.ONE_MINUTE)

            except (ConnectionError, RequestException, HTTPException):
                logger.error(Error.CONNECTION_CLOSED_AUTOMATIC)
                time.sleep(TimeOut.CONNECTION_CLOSED * TimeOut.ONE_MINUTE)

            except KeyboardInterrupt:
                self._shutdown_recordings(active_recordings)
                raise

    def _shutdown_recordings(self, active_recordings: dict) -> None:
        """
        Signal worker threads to stop and wait for them to finalize
        (flush + convert) their recordings.
        """
        self._stop_event.set()

        alive = {u: t for u, t in active_recordings.items() if t.is_alive()}
        if not alive:
            return

        logger.info(f"Stopping {len(alive)} active recording(s), please wait...")
        for follower, thread in alive.items():
            thread.join(timeout=30)
            if thread.is_alive():
                logger.warning(
                    f"Recording of @{follower} did not stop in time; "
                    "its file may be left unconverted."
                )

    def _build_output_path(self, user: str) -> str:
        filename = (
            f"TK_{user}_{time.strftime('%Y.%m.%d_%H-%M-%S', time.localtime())}_flv.mp4"
        )
        if self.output:
            return str(Path(self.output) / filename)
        return filename

    def start_recording(self, user, room_id):
        """
        Start recording live
        """
        live_urls = self.tiktok.get_live_url_candidates(room_id, user=user)
        if not live_urls:
            raise LiveNotFound(TikTokError.RETRIEVE_LIVE_URL)

        output = self._build_output_path(user)

        min_stream_bytes = 4096
        interrupted = False
        for index, live_url in enumerate(live_urls, start=1):
            if self.duration:
                logger.info(
                    f"Started recording for {self.duration} seconds "
                    f"(stream {index}/{len(live_urls)})"
                )
            else:
                logger.info(f"Started recording (stream {index}/{len(live_urls)})...")

            buffer_size = 512 * 1024  # 512 KB buffer
            buffer = bytearray()
            bytes_written = 0

            logger.info("[PRESS CTRL + C ONCE TO STOP]")
            with open(output, "wb") as out_file:
                stop_recording = False
                stream_ended = False
                while not stop_recording:
                    try:
                        if self._stop_event.is_set():
                            stop_recording = True
                            break

                        if not self.tiktok.is_room_alive(room_id):
                            logger.info("User is no longer live. Stopping recording.")
                            break

                        start_time = time.time()
                        for chunk in self.tiktok.download_live_stream(live_url):
                            buffer.extend(chunk)
                            bytes_written += len(chunk)
                            if len(buffer) >= buffer_size:
                                out_file.write(buffer)
                                buffer.clear()

                            elapsed_time = time.time() - start_time
                            if self.duration and elapsed_time >= self.duration:
                                stop_recording = True
                                break

                            if self._stop_event.is_set():
                                stop_recording = True
                                break
                        else:
                            stream_ended = True

                        if stream_ended and bytes_written < min_stream_bytes:
                            break

                    except ConnectionError:
                        if self.mode == Mode.AUTOMATIC:
                            logger.error(Error.CONNECTION_CLOSED_AUTOMATIC)
                            time.sleep(TimeOut.CONNECTION_CLOSED * TimeOut.ONE_MINUTE)
                        else:
                            logger.warning("Connection lost, retrying...")
                            time.sleep(2)

                    except HTTPError as ex:
                        # A 4xx means this CDN URL is stale/dead (e.g. a
                        # page-scraped fallback URL that has expired). Stop
                        # retrying it and move on to the next candidate; if
                        # none work we raise LiveNotFound and recheck later.
                        status = getattr(
                            getattr(ex, "response", None), "status_code", None
                        )
                        if status is not None and 400 <= status < 500:
                            logger.warning(
                                f"Stream URL is no longer valid (HTTP {status}); "
                                "trying another CDN/quality..."
                            )
                            break
                        logger.warning(f"Network hiccup, retrying: {ex}")
                        time.sleep(2)

                    except (RequestException, HTTPException) as ex:
                        logger.warning(f"Network hiccup, retrying: {ex}")
                        time.sleep(2)

                    except KeyboardInterrupt:
                        logger.info("Recording stopped by user.")
                        stop_recording = True
                        interrupted = True

                    except Exception as ex:
                        logger.error(
                            f"Unexpected error during recording: {ex}",
                            exc_info=True,
                        )
                        stop_recording = True

                    finally:
                        if buffer:
                            out_file.write(buffer)
                            buffer.clear()
                        out_file.flush()

            if bytes_written >= min_stream_bytes:
                break

            if interrupted:
                Path(output).unlink(missing_ok=True)
                raise KeyboardInterrupt()

            if self._stop_event.is_set():
                # Cooperative stop in a worker thread with too little data
                # to keep: discard and exit without trying other CDNs.
                Path(output).unlink(missing_ok=True)
                return

            logger.warning(
                f"Stream {index}/{len(live_urls)} returned only {bytes_written} bytes. "
                "Trying another CDN/quality..."
            )
        else:
            Path(output).unlink(missing_ok=True)
            raise LiveNotFound(TikTokError.RETRIEVE_LIVE_URL)

        logger.info(f"Recording finished: {Path(output).resolve()}\n")
        converted = VideoManagement.convert_flv_to_mp4(
            output, self.bitrate, self.ffmpeg_path
        )

        # skip the upload on Ctrl+C so the program exits promptly
        if self.use_telegram and converted and not interrupted:
            from upload.telegram import Telegram

            Telegram().upload(converted)

        if interrupted:
            raise KeyboardInterrupt()

    def check_country_blacklisted(self):
        is_blacklisted = self.tiktok.is_country_blacklisted()
        if not is_blacklisted:
            return False

        if self.room_id is None:
            raise TikTokRecorderError(TikTokError.COUNTRY_BLACKLISTED)

        if self.mode == Mode.AUTOMATIC:
            raise TikTokRecorderError(TikTokError.COUNTRY_BLACKLISTED_AUTO_MODE)

        elif self.mode == Mode.FOLLOWERS:
            raise TikTokRecorderError(TikTokError.COUNTRY_BLACKLISTED_FOLLOWERS_MODE)

        return is_blacklisted
