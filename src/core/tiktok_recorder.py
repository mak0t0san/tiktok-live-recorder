import time
from http.client import HTTPException
from pathlib import Path
from threading import Event

from requests import HTTPError, RequestException

from core.tiktok_api import TikTokAPI
from utils.logger_manager import logger
from utils.recorder_config import RecorderConfig
from utils.video_management import VideoManagement
from utils.custom_exceptions import (
    LiveNotFound,
    UserLiveError,
    TikTokRecorderError,
    AlreadyRecording,
)
from utils.enums import Mode, Error, TimeOut, TikTokError
from utils.recording_lock import recording_lock
from utils.status_store import NullStatusReporter, StatusReporter


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
        # main thread, so workers poll this event instead. The parent
        # process (supervisor/web UI) may inject a multiprocessing.Event
        # via the config to request the same cooperative stop from outside.
        self._stop_event = (
            config.stop_event if config.stop_event is not None else Event()
        )

        # Optional "check now" signal from the parent: interrupts the
        # automatic-mode recheck sleep without stopping the recorder.
        self._wake_event = config.wake_event

        # Best-effort status reporting for the web dashboard; a no-op unless
        # a status database path was provided (real reporter is attached in
        # run() once the username is resolved).
        self._status_db = config.status_db
        self._status = NullStatusReporter()

    def _setup(self):
        """Resolve user/room data and validate prerequisites via network calls."""
        if self.url:
            self.user, self.room_id = self.tiktok.get_room_and_user_from_url(self.url)

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
        """
        self._setup()

        if self._status_db and self.user:
            self._status = StatusReporter(self.user, self._status_db)
            self._status.report(state="waiting", room_id=self.room_id)

        if self.mode == Mode.MANUAL:
            self.manual_mode()

        elif self.mode == Mode.AUTOMATIC:
            self.automatic_mode()

    def manual_mode(self):
        if not self.tiktok.is_room_alive(self.room_id):
            raise UserLiveError(f"@{self.user}: {TikTokError.USER_NOT_CURRENTLY_LIVE}")

        self.start_recording(self.user, self.room_id)

    def _wait_or_stop(self, seconds) -> bool:
        """
        Idle for up to ``seconds``, waking early if a cooperative stop is
        requested (returns True) or a manual "check now" is requested via the
        wake event (returns False, so the caller re-checks liveness).
        """
        if self._wake_event is None:
            return self._stop_event.wait(seconds)

        # Drop wakes that queued up while we were busy (e.g. recording), so
        # a stale request doesn't skip the very next recheck sleep.
        self._wake_event.clear()
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            # 1s slices keep stop latency unchanged while letting the wake
            # event interrupt the sleep.
            if self._stop_event.wait(min(1.0, remaining)):
                return True
            if self._wake_event.is_set():
                self._wake_event.clear()
                return False

    def automatic_mode(self):
        while not self._stop_event.is_set():
            try:
                self.room_id = self.tiktok.get_room_id_from_user(self.user)
                self.manual_mode()

            except AlreadyRecording as ex:
                logger.info(ex)
                logger.info(
                    f"Waiting {self.automatic_interval} minutes before recheck\n"
                )
                self._status.report(state="waiting")
                if self._wait_or_stop(self.automatic_interval * TimeOut.ONE_MINUTE):
                    break

            except (UserLiveError, LiveNotFound) as ex:
                logger.info(ex)
                logger.info(
                    f"Waiting {self.automatic_interval} minutes before recheck\n"
                )
                self._status.report(state="waiting")
                if self._wait_or_stop(self.automatic_interval * TimeOut.ONE_MINUTE):
                    break

            except (ConnectionError, RequestException, HTTPException):
                logger.error(Error.CONNECTION_CLOSED_AUTOMATIC)
                self._status.report(state="waiting")
                if self._wait_or_stop(TimeOut.CONNECTION_CLOSED * TimeOut.ONE_MINUTE):
                    break

        if self._stop_event.is_set():
            logger.info(f"Stop requested; @{self.user} monitoring ended.")
            self._status.report(state="stopped")

    def _build_output_path(self, user: str) -> str:
        filename = (
            f"TK_{user}_{time.strftime('%Y.%m.%d_%H-%M-%S', time.localtime())}_flv.mp4"
        )
        if self.output:
            return str(Path(self.output) / filename)
        return filename

    def start_recording(self, user, room_id):
        """
        Acquire a per-user lock, then record. The lock prevents a second worker
        or program instance from recording the same user into a duplicate file.
        """
        lock = recording_lock(user, self.output)
        if not lock.acquire():
            raise AlreadyRecording(
                f"@{user} is already being recorded by another "
                "worker/instance; skipping."
            )
        try:
            self._do_recording(user, room_id)
        finally:
            lock.release()

    def _do_recording(self, user, room_id):
        """
        Start recording live
        """
        live_urls = self.tiktok.get_live_url_candidates(room_id, user=user)
        if not live_urls:
            raise LiveNotFound(TikTokError.RETRIEVE_LIVE_URL)

        output = self._build_output_path(user)
        started_at = time.time()
        self._status.report(
            state="recording",
            room_id=room_id,
            output_path=output,
            started_at=started_at,
            bytes_written=0,
        )

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
                                self._status.report(
                                    state="recording", bytes_written=bytes_written
                                )

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
        # Written before conversion so a crashed/hung ffmpeg still leaves a
        # history row; the same (user, started_at) row is updated with the
        # converted path below.
        ended_at = time.time()
        self._status.record_session(
            started_at=started_at,
            ended_at=ended_at,
            bytes_written=bytes_written,
            output_path=output,
        )
        self._status.report(state="converting", bytes_written=bytes_written)
        converted = VideoManagement.convert_flv_to_mp4(
            output, self.bitrate, self.ffmpeg_path
        )
        if converted:
            self._status.report(state="converting", output_path=str(converted))
            self._status.record_session(
                started_at=started_at,
                ended_at=ended_at,
                bytes_written=bytes_written,
                output_path=str(converted),
            )

        # skip the upload on Ctrl+C so the program exits promptly
        if self.use_telegram and converted and not interrupted:
            from upload.telegram import Telegram

            self._status.report(state="uploading")
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

        return is_blacklisted
