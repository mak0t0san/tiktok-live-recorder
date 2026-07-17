import pytest

from core.tiktok_api import TikTokAPI
from utils.custom_exceptions import (
    LiveNotFound,
    TikTokRecorderError,
    UserLiveError,
)


class FakeResponse:
    def __init__(self, data, text="", status_code=200):
        self._data = data
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._data


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        response = self.responses.pop(0)
        if not isinstance(response, FakeResponse):
            response = FakeResponse(response)
        return response


def build_api(*responses):
    api = TikTokAPI.__new__(TikTokAPI)
    api.WEBCAST_URL = "https://webcast.tiktok.com"
    api.http_client = FakeHttpClient(list(responses))
    return api


def test_is_room_alive_rejects_fake_check_alive_positive():
    api = build_api(
        {"data": [{"alive": True, "room_id": 123}], "status_code": 0},
        {"data": {"message": "Request params error"}, "status_code": 10011},
    )

    assert api.is_room_alive("123") is False


def test_is_room_alive_accepts_confirmed_stream_room():
    api = build_api(
        {"data": [{"alive": True, "room_id": 123}], "status_code": 0},
        {
            "data": {
                "status": 2,
                "stream_url": {
                    "live_core_sdk_data": {"pull_data": {"stream_data": '{"data": {}}'}}
                },
            },
            "status_code": 0,
        },
    )

    assert api.is_room_alive("123") is True


def test_is_room_alive_keeps_restricted_live_as_alive():
    api = build_api(
        {"data": [{"alive": True, "room_id": 123}], "status_code": 0},
        {"data": {}, "status_code": 4003110},
    )

    assert api.is_room_alive("123") is True


def test_is_room_alive_skips_room_info_when_check_alive_is_false():
    api = build_api({"data": [{"alive": False, "room_id": 123}], "status_code": 0})

    assert api.is_room_alive("123") is False
    assert len(api.http_client.urls) == 1


def test_is_room_alive_rejects_null_check_alive_data():
    api = build_api({"data": None, "status_code": 0})

    assert api.is_room_alive("123") is False
    assert len(api.http_client.urls) == 1


def test_is_room_alive_rejects_null_room_info_data():
    api = build_api(
        {"data": [{"alive": True, "room_id": 123}], "status_code": 0},
        {"data": None, "status_code": 0},
    )

    assert api.is_room_alive("123") is False


def test_is_room_alive_rejects_ended_room_with_stale_stream_urls():
    api = build_api(
        {"data": [{"alive": True, "room_id": 123}], "status_code": 0},
        {
            "data": {
                "status": 4,
                "finish_time": 1784118433,
                "stream_url": {
                    "live_core_sdk_data": {
                        "pull_data": {"stream_data": '{"data": {}}'}
                    },
                    "flv_pull_url": {"HD1": "https://example.com/stale.flv"},
                },
            },
            "status_code": 0,
        },
    )

    assert api.is_room_alive("123") is False


def test_get_live_url_rejects_ended_room_with_stale_stream_urls():
    api = build_api(
        {
            "data": {
                "status": 4,
                "finish_time": 1784118433,
                "stream_url": {
                    "live_core_sdk_data": {
                        "pull_data": {"stream_data": '{"data": {}}'}
                    },
                    "flv_pull_url": {"HD1": "https://example.com/stale.flv"},
                },
            },
            "status_code": 0,
        },
    )

    with pytest.raises(UserLiveError, match="not hosting a live stream"):
        api.get_live_url("123", user="creator")


def test_get_live_url_candidates_returns_ordered_unique_streams():
    api = build_api(
        {
            "data": {
                "status": 2,
                "stream_url": {
                    "live_core_sdk_data": {
                        "pull_data": {
                            "stream_data": (
                                '{"data": {'
                                '"hd": {"main": {"flv": "https://cdn/hd.flv"}},'
                                '"ld": {"main": {"flv": "https://cdn/ld.flv"}},'
                                '"ao": {"main": {"flv": "https://cdn/audio.flv"}}'
                                "}}"
                            ),
                            "options": {
                                "qualities": [
                                    {"sdk_key": "hd", "level": 3},
                                    {"sdk_key": "ld", "level": 1},
                                ]
                            },
                        }
                    },
                    "flv_pull_url": {
                        "HD1": "https://cdn/hd.flv",
                        "SD1": "https://cdn/sd.flv",
                    },
                },
            },
            "status_code": 0,
        },
    )

    assert api.get_live_url_candidates("123", user="creator") == [
        "https://cdn/hd.flv",
        "https://cdn/ld.flv",
        "https://cdn/audio.flv",
        "https://cdn/sd.flv",
    ]


def test_get_user_from_room_id_detects_private_account():
    api = build_api({"data": {"message": "This account is private"}})

    with pytest.raises(UserLiveError, match="Account is private"):
        api.get_user_from_room_id("123")


def test_get_live_url_candidates_detects_private_account():
    api = build_api({"data": {"message": "This account is private"}})

    with pytest.raises(UserLiveError, match="Account is private"):
        api.get_live_url_candidates("123", user="creator")


def test_get_room_and_user_from_url_rejects_unparsable_url():
    api = build_api(FakeResponse({}, text="<html></html>", status_code=200))

    with pytest.raises(LiveNotFound, match="not a valid TikTok live"):
        api.get_room_and_user_from_url("https://example.com/not-tiktok")


class FakeJsonErrorResponse(FakeResponse):
    def __init__(self):
        super().__init__(
            None, text="<html>Please wait while we verify your browser</html>"
        )

    def json(self):
        raise ValueError("Expecting value")


def test_html_response_raises_recorder_error_with_snippet():
    api = build_api(FakeJsonErrorResponse())

    with pytest.raises(TikTokRecorderError, match="non-JSON response.*Please wait"):
        api.is_room_alive("123")


class FakeStreamResponse:
    def __init__(self, chunks, status_error=None):
        self.chunks = chunks
        self.status_error = status_error
        self.closed = False

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def iter_content(self, chunk_size):
        yield from self.chunks

    def close(self):
        self.closed = True


class FakeStreamClient:
    def __init__(self, response):
        self.response = response

    def get(self, url, stream=False):
        return self.response


def _build_stream_api(response):
    api = TikTokAPI.__new__(TikTokAPI)
    api._http_client_stream = FakeStreamClient(response)
    return api


def test_download_live_stream_raises_on_http_error_and_closes():
    response = FakeStreamResponse([b"<html>"], status_error=RuntimeError("404"))
    api = _build_stream_api(response)

    with pytest.raises(RuntimeError):
        list(api.download_live_stream("https://cdn/live.flv"))

    assert response.closed


def test_download_live_stream_closes_when_consumer_abandons_generator():
    response = FakeStreamResponse([b"a", b"b", b"c"])
    api = _build_stream_api(response)

    gen = api.download_live_stream("https://cdn/live.flv")
    assert next(gen) == b"a"
    gen.close()

    assert response.closed
