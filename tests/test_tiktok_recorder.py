import pytest

from core.tiktok_recorder import TikTokRecorder
from utils.custom_exceptions import LiveNotFound, TikTokRecorderError
from utils.enums import Mode
from utils.recorder_config import RecorderConfig
from utils.video_management import VideoManagement


class FakeTikTokAPI:
    def __init__(self, blacklisted=True):
        self.blacklisted = blacklisted
        self.calls = []

    def is_country_blacklisted(self):
        self.calls.append("is_country_blacklisted")
        return self.blacklisted

    def get_room_id_from_user(self, user):
        self.calls.append(f"get_room_id_from_user:{user}")
        return "1234567890"

    def get_user_from_room_id(self, room_id):
        self.calls.append(f"get_user_from_room_id:{room_id}")
        return "creator"

    def get_sec_uid(self):
        self.calls.append("get_sec_uid")
        return "sec_uid"

    def is_room_alive(self, room_id):
        self.calls.append(f"is_room_alive:{room_id}")
        return True


def test_setup_resolves_room_id_before_country_check_for_manual_user():
    recorder = TikTokRecorder(
        RecorderConfig(mode=Mode.MANUAL, user="creator", cookies={})
    )
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api

    recorder._setup()

    assert recorder.room_id == "1234567890"
    assert fake_api.calls == [
        "get_room_id_from_user:creator",
        "is_country_blacklisted",
        "is_room_alive:1234567890",
    ]


def test_setup_keeps_followers_country_check_before_sec_uid():
    recorder = TikTokRecorder(RecorderConfig(mode=Mode.FOLLOWERS, cookies={}))
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api

    with pytest.raises(TikTokRecorderError, match="Captcha required"):
        recorder._setup()

    assert fake_api.calls == ["is_country_blacklisted"]


def test_setup_keeps_automatic_mode_blocked_after_room_resolution():
    recorder = TikTokRecorder(
        RecorderConfig(mode=Mode.AUTOMATIC, user="creator", cookies={})
    )
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api

    with pytest.raises(TikTokRecorderError, match="Automatic mode is available"):
        recorder._setup()

    assert recorder.room_id == "1234567890"
    assert fake_api.calls == [
        "get_room_id_from_user:creator",
        "is_country_blacklisted",
    ]


def test_setup_keeps_manual_room_id_allowed_when_country_check_is_blocked():
    recorder = TikTokRecorder(
        RecorderConfig(mode=Mode.MANUAL, room_id="1234567890", cookies={})
    )
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api

    recorder._setup()

    assert recorder.room_id == "1234567890"
    assert fake_api.calls == [
        "get_user_from_room_id:1234567890",
        "is_country_blacklisted",
        "is_room_alive:1234567890",
    ]


def test_automatic_mode_propagates_keyboard_interrupt_without_looping():
    recorder = TikTokRecorder(
        RecorderConfig(mode=Mode.AUTOMATIC, user="creator", cookies={})
    )
    fake_api = FakeTikTokAPI(blacklisted=False)
    recorder.tiktok = fake_api

    def fake_start_recording(user, room_id):
        raise KeyboardInterrupt()

    recorder.start_recording = fake_start_recording

    with pytest.raises(KeyboardInterrupt):
        recorder.automatic_mode()

    # start_recording was only ever reached once - the loop did not restart
    # the recording after the interrupt.
    assert fake_api.calls == [
        "get_room_id_from_user:creator",
        "is_room_alive:1234567890",
    ]


class FakeStreamAPI:
    def __init__(self):
        self.calls = []

    def is_room_alive(self, room_id):
        self.calls.append(f"is_room_alive:{room_id}")
        return True

    def get_live_url_candidates(self, room_id, user=None):
        return ["https://example.com/stream.flv"]

    def download_live_stream(self, live_url):
        yield b"x" * 8192
        raise KeyboardInterrupt()


def test_start_recording_finalizes_then_propagates_keyboard_interrupt(
    tmp_path, monkeypatch
):
    recorder = TikTokRecorder(
        RecorderConfig(
            mode=Mode.MANUAL, user="creator", cookies={}, output=str(tmp_path)
        )
    )
    recorder.tiktok = FakeStreamAPI()

    converted = []
    monkeypatch.setattr(
        VideoManagement,
        "convert_flv_to_mp4",
        lambda *args, **kwargs: converted.append(args),
    )

    with pytest.raises(KeyboardInterrupt):
        recorder.start_recording("creator", "1234567890")

    assert converted, "recording should be finalized/converted before re-raising"


def _build_recorder(tmp_path, mode=Mode.FOLLOWERS):
    return TikTokRecorder(
        RecorderConfig(mode=mode, user="creator", cookies={}, output=str(tmp_path))
    )


class CooperativeStopAPI:
    """Yields one large chunk, then sets the recorder's stop event."""

    def __init__(self):
        self.recorder = None

    def is_room_alive(self, room_id):
        return True

    def get_live_url_candidates(self, room_id, user=None):
        return ["https://example.com/stream.flv"]

    def download_live_stream(self, live_url):
        yield b"x" * 8192
        self.recorder._stop_event.set()
        yield b"y" * 100


def test_stop_event_finalizes_recording_without_raising(tmp_path, monkeypatch):
    recorder = _build_recorder(tmp_path)
    api = CooperativeStopAPI()
    api.recorder = recorder
    recorder.tiktok = api

    converted = []
    monkeypatch.setattr(
        VideoManagement,
        "convert_flv_to_mp4",
        lambda *args, **kwargs: converted.append(args),
    )

    recorder.start_recording("creator", "1234567890")

    assert converted, "cooperative stop should still convert the recording"


def test_stop_event_with_tiny_stream_deletes_output(tmp_path, monkeypatch):
    recorder = _build_recorder(tmp_path)
    recorder.tiktok = FakeStreamAPI()
    recorder._stop_event.set()

    converted = []
    monkeypatch.setattr(
        VideoManagement,
        "convert_flv_to_mp4",
        lambda *args, **kwargs: converted.append(args),
    )

    recorder.start_recording("creator", "1234567890")

    assert not converted
    assert list(tmp_path.iterdir()) == []


class FlakyConnectionAPI:
    def __init__(self, recorder):
        self.recorder = recorder
        self.download_calls = 0

    def is_room_alive(self, room_id):
        return True

    def get_live_url_candidates(self, room_id, user=None):
        return ["https://example.com/stream.flv"]

    def download_live_stream(self, live_url):
        self.download_calls += 1
        self.recorder._stop_event.set()
        raise ConnectionError("dropped")
        yield  # pragma: no cover

    def close(self):
        pass


def test_connection_error_sleeps_outside_automatic_mode(tmp_path, monkeypatch):
    recorder = _build_recorder(tmp_path, mode=Mode.MANUAL)
    recorder.tiktok = FlakyConnectionAPI(recorder)

    sleeps = []
    monkeypatch.setattr("core.tiktok_recorder.time.sleep", lambda s: sleeps.append(s))

    recorder.start_recording("creator", "1234567890")

    assert sleeps, "manual mode must back off after a ConnectionError"


class DeadUrlAPI:
    """Every candidate CDN URL returns HTTP 404 (stale/expired)."""

    def __init__(self):
        self.download_calls = 0

    def is_room_alive(self, room_id):
        return True

    def get_live_url_candidates(self, room_id, user=None):
        return ["https://cdn/dead1.flv", "https://cdn/dead2.flv"]

    def download_live_stream(self, live_url):
        from requests import HTTPError

        self.download_calls += 1

        class _Resp:
            status_code = 404

        raise HTTPError("404 Client Error", response=_Resp())
        yield  # pragma: no cover


def test_stale_404_url_does_not_retry_forever(tmp_path, monkeypatch):
    recorder = _build_recorder(tmp_path, mode=Mode.MANUAL)
    api = DeadUrlAPI()
    recorder.tiktok = api

    monkeypatch.setattr("core.tiktok_recorder.time.sleep", lambda s: None)

    with pytest.raises(LiveNotFound):
        recorder.start_recording("creator", "1234567890")

    # each dead candidate is tried exactly once, then we give up — no
    # infinite retry loop on the same stale URL
    assert api.download_calls == 2


def test_followers_mode_sets_stop_event_and_joins_on_interrupt(tmp_path):
    recorder = _build_recorder(tmp_path)
    recorder.sec_uid = "sec_uid"

    class InterruptingAPI:
        def get_followers_list(self, sec_uid):
            raise KeyboardInterrupt()

    recorder.tiktok = InterruptingAPI()

    with pytest.raises(KeyboardInterrupt):
        recorder.followers_mode()

    assert recorder._stop_event.is_set()


def test_shutdown_recordings_joins_alive_threads(tmp_path):
    recorder = _build_recorder(tmp_path)

    class FakeThread:
        def __init__(self):
            self.joined = []
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.joined.append(timeout)
            self.alive = False

    thread = FakeThread()
    recorder._shutdown_recordings({"creator": thread})

    assert recorder._stop_event.is_set()
    assert thread.joined


class FiniteStreamAPI:
    """Yields one large chunk, then the stream ends normally."""

    def __init__(self):
        self.alive_checks = 0

    def is_room_alive(self, room_id):
        self.alive_checks += 1
        return self.alive_checks == 1

    def get_live_url_candidates(self, room_id, user=None):
        return ["https://example.com/stream.flv"]

    def download_live_stream(self, live_url):
        yield b"x" * 8192


def _record_with_telegram(tmp_path, monkeypatch, use_telegram, converted_path):
    recorder = TikTokRecorder(
        RecorderConfig(
            mode=Mode.MANUAL,
            user="creator",
            cookies={},
            output=str(tmp_path),
            use_telegram=use_telegram,
        )
    )
    recorder.tiktok = FiniteStreamAPI()

    monkeypatch.setattr(
        VideoManagement,
        "convert_flv_to_mp4",
        lambda *args, **kwargs: converted_path,
    )

    uploads = []

    class FakeTelegram:
        def upload(self, file_path):
            uploads.append(file_path)

    monkeypatch.setattr("upload.telegram.Telegram", FakeTelegram)

    recorder.start_recording("creator", "1234567890")
    return uploads


def test_telegram_upload_called_with_converted_path(tmp_path, monkeypatch):
    uploads = _record_with_telegram(
        tmp_path, monkeypatch, use_telegram=True, converted_path="/videos/out.mp4"
    )
    assert uploads == ["/videos/out.mp4"]


def test_telegram_upload_skipped_when_disabled(tmp_path, monkeypatch):
    uploads = _record_with_telegram(
        tmp_path, monkeypatch, use_telegram=False, converted_path="/videos/out.mp4"
    )
    assert uploads == []


def test_telegram_upload_skipped_when_conversion_failed(tmp_path, monkeypatch):
    uploads = _record_with_telegram(
        tmp_path, monkeypatch, use_telegram=True, converted_path=None
    )
    assert uploads == []


def test_telegram_upload_skipped_when_interrupted(tmp_path, monkeypatch):
    recorder = TikTokRecorder(
        RecorderConfig(
            mode=Mode.MANUAL,
            user="creator",
            cookies={},
            output=str(tmp_path),
            use_telegram=True,
        )
    )
    recorder.tiktok = FakeStreamAPI()

    monkeypatch.setattr(
        VideoManagement,
        "convert_flv_to_mp4",
        lambda *args, **kwargs: "/videos/out.mp4",
    )

    uploads = []

    class FakeTelegram:
        def upload(self, file_path):
            uploads.append(file_path)

    monkeypatch.setattr("upload.telegram.Telegram", FakeTelegram)

    with pytest.raises(KeyboardInterrupt):
        recorder.start_recording("creator", "1234567890")

    assert uploads == []
