import requests

from utils.enums import StatusCode
from utils.logger_manager import logger
from utils.utils import is_termux

DEFAULT_TIMEOUT = 30


class TimeoutSession(requests.Session):
    """requests.Session with a default timeout on every request."""

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        return super().request(*args, **kwargs)


class HttpClient:
    def __init__(self, proxy=None, cookies=None):
        self.req = None
        self.req_stream = requests

        self.proxy = proxy
        self.cookies = cookies
        self.headers = {
            "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Accept-Language": "en-US",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.127 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,application/json,text/plain,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Priority": "u=0, i",
            "Referer": "https://www.tiktok.com/",
            "Origin": "https://www.tiktok.com",
        }

        self.configure_session()

    def configure_session(self) -> None:
        self.req_stream = TimeoutSession()

        if is_termux():
            self.req = self.req_stream
        else:
            from curl_cffi import Session, CurlSslVersion, CurlOpt

            self.req = Session(
                impersonate="chrome136",
                http_version="v1",
                timeout=DEFAULT_TIMEOUT,
                curl_options={CurlOpt.SSLVERSION: CurlSslVersion.TLSv1_2},
            )

        self.req.headers.update(self.headers)
        self.req_stream.headers.update(self.headers)

        if self.cookies is not None:
            self.req.cookies.update(self.cookies)
            self.req_stream.cookies.update(self.cookies)

        self.check_proxy()

    def check_proxy(self) -> None:
        if self.proxy is None:
            return

        logger.info(f"Testing {self.proxy}...")
        proxies = {"http": self.proxy, "https": self.proxy}

        try:
            response = requests.get(
                "https://ifconfig.me/ip", proxies=proxies, timeout=10
            )
            if response.status_code == StatusCode.OK:
                logger.info("Proxy set up successfully")
            else:
                logger.warning(
                    f"Proxy check returned HTTP {response.status_code}. "
                    "Using the proxy anyway."
                )
        except requests.RequestException as e:
            logger.warning(f"Proxy check failed ({e}). Using the proxy anyway.")

        self.req.proxies.update(proxies)
        self.req_stream.proxies.update(proxies)

    def close(self) -> None:
        self.req_stream.close()
        if self.req is not self.req_stream and self.req is not None:
            self.req.close()
