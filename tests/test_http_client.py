import requests

from http_utils.http_client import DEFAULT_TIMEOUT, HttpClient, TimeoutSession


def _termux_client(monkeypatch, proxy=None):
    monkeypatch.setattr("http_utils.http_client.is_termux", lambda: True)
    return HttpClient(proxy=proxy)


def test_unreachable_proxy_does_not_crash_and_applies_to_both_sessions(monkeypatch):
    def failing_get(*args, **kwargs):
        raise requests.ConnectionError("unreachable")

    monkeypatch.setattr("http_utils.http_client.requests.get", failing_get)

    client = _termux_client(monkeypatch, proxy="http://127.0.0.1:1")

    proxies = {"http": "http://127.0.0.1:1", "https": "http://127.0.0.1:1"}
    assert client.req.proxies == proxies
    assert client.req_stream.proxies == proxies


def test_no_proxy_skips_check(monkeypatch):
    def failing_get(*args, **kwargs):
        raise AssertionError("should not be called")

    monkeypatch.setattr("http_utils.http_client.requests.get", failing_get)

    client = _termux_client(monkeypatch)
    assert client.req.proxies == {}


def test_timeout_session_injects_default_timeout(monkeypatch):
    seen = {}

    def fake_request(self, method, url, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(requests.Session, "request", fake_request)

    TimeoutSession().get("https://example.com")
    assert seen["timeout"] == DEFAULT_TIMEOUT


def test_timeout_session_respects_explicit_timeout(monkeypatch):
    seen = {}

    def fake_request(self, method, url, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(requests.Session, "request", fake_request)

    TimeoutSession().get("https://example.com", timeout=5)
    assert seen["timeout"] == 5


def test_close_is_idempotent(monkeypatch):
    client = _termux_client(monkeypatch)
    client.close()
    client.close()
