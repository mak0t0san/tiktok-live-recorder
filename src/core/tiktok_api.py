import html
import json
import re

from http_utils.http_client import HttpClient
from utils.enums import StatusCode, TikTokError
from utils.logger_manager import logger
from utils.custom_exceptions import (
    UserLiveError,
    TikTokRecorderError,
    LiveNotFound,
    TikRecUnavailableError,
)

# Scene selector for /api/user/list/. NOTE: verify against a real account —
# if the endpoint turns out to return followers instead of followed accounts,
# fixing it is a matter of changing this one constant.
USER_LIST_SCENE_FOLLOWING = 21

# Stale-but-accepted seed token used only for the priming request that
# harvests a fresh msToken from TikTok's response cookies.
_SEED_MS_TOKEN = (
    "GphHoLvRR4QxA5AWVwDkrs3AbumoK5H8toE8LVHtj6cce3ToGdXhMfvDWzOXG-0GXUWoaGVHrwG"
    "NA4k_NnjuFFnHgv2S5eMjsvtkAhwMPa13xLmvP7tumx0KreFjPwTNnOj-BvAkPdO5Zrev3hoFBD9lHVo="
)

# Fixed web-fingerprint params shared by every /api/user/list/ request.
_USER_LIST_STATIC_PARAMS = (
    "WebIdLastTime=1747672102&aid=1988&app_language=it-IT&app_name=tiktok_web"
    "&browser_language=it-IT&browser_name=Mozilla&browser_online=true"
    "&browser_platform=Linux%20x86_64"
    "&browser_version=5.0%20%28X11%3B%20Linux%20x86_64%29%20AppleWebKit%2F537.36"
    "%20%28KHTML%2C%20like%20Gecko%29%20Chrome%2F140.0.0.0%20Safari%2F537.36"
    "&channel=tiktok_web&cookie_enabled=true&data_collection_enabled=true"
    "&device_id=7506194516308166166&device_platform=web_pc&focus_state=true"
    "&from_page=user&history_len=3&is_fullscreen=false&is_page_visible=true"
    "&odinId=7246312836442604570&os=linux&priority_region=IT&referer=&region=IT"
    "&screen_height=1080&screen_width=1920&tz_name=Europe%2FRome&user_is_login=true"
    "&verifyFp=verify_mh4yf0uq_rdjp1Xwt_OoTk_4Jrf_AS8H_sp31opbnJFre"
    "&webcast_language=it-IT"
)


def _extract_avatar(user_obj: dict) -> str | None:
    """
    Pull an avatar URL out of a TikTok user object. Depending on the endpoint
    the avatar fields are either plain URL strings or {"urlList": [...]}.
    """
    for key in ("avatarThumb", "avatarMedium", "avatarLarger"):
        value = user_obj.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            url_list = value.get("urlList") or value.get("url_list") or []
            if url_list:
                return url_list[0]
    return None


class TikTokAPI:
    def __init__(self, proxy, cookies):
        self.BASE_URL = "https://www.tiktok.com"
        self.WEBCAST_URL = "https://webcast.tiktok.com"
        self.API_URL = "https://www.tiktok.com/api-live/user/room/"
        self.EULER_API = "https://tiktok.eulerstream.com"
        self.TIKREC_API = "https://tikrec.com"

        self._http = HttpClient(proxy, cookies)
        self.http_client = self._http.req
        self._http_client_stream = self._http.req_stream

    def close(self):
        self._http.close()

    @staticmethod
    def _get_json(response) -> dict:
        """
        Parse a JSON response, raising a TikTokRecorderError with a snippet
        of the body when TikTok returns HTML (WAF page, error page) instead.
        """
        try:
            return response.json()
        except ValueError as e:
            snippet = (response.text or "")[:150]
            raise TikTokRecorderError(
                f"TikTok returned a non-JSON response: {snippet!r}"
            ) from e

    def _is_authenticated(self) -> bool:
        response = self.http_client.get(f"{self.BASE_URL}/foryou")
        response.raise_for_status()

        content = response.text
        return "login-title" not in content

    def is_country_blacklisted(self) -> bool:
        """
        Checks if the user is in a blacklisted country that requires login
        """
        response = self.http_client.get(f"{self.BASE_URL}/live", allow_redirects=False)

        return response.status_code == StatusCode.REDIRECT

    def is_room_alive(self, room_id: str) -> bool:
        """
        Checking whether the user is live.
        """
        if not room_id:
            raise UserLiveError(TikTokError.USER_NOT_CURRENTLY_LIVE)

        alive_data = self._get_json(
            self.http_client.get(
                f"{self.WEBCAST_URL}/webcast/room/check_alive/"
                f"?aid=1988&region=CH&room_ids={room_id}&user_is_login=true"
            )
        )

        data_list = alive_data.get("data")
        if (
            not isinstance(data_list, list)
            or not data_list
            or not isinstance(data_list[0], dict)
            or not data_list[0].get("alive", False)
        ):
            return False

        room_info = self._get_json(
            self.http_client.get(
                f"{self.WEBCAST_URL}/webcast/room/info/?aid=1988&room_id={room_id}"
            )
        )

        status_code = room_info.get("status_code", 0)
        if status_code == 4003110:
            return True

        if status_code != 0:
            return False

        room_data = room_info.get("data") or {}
        room_status = room_data.get("status")
        if room_status is not None and str(room_status) != "2":
            return False

        stream_url = room_data.get("stream_url") or {}
        sdk_stream_data = (
            (stream_url.get("live_core_sdk_data") or {})
            .get("pull_data", {})
            .get("stream_data")
        )

        return bool(
            sdk_stream_data
            or stream_url.get("flv_pull_url")
            or stream_url.get("hls_pull_url")
            or stream_url.get("hls_pull_url_map")
            or stream_url.get("rtmp_pull_url")
        )

    def get_sec_uid(self):
        """
        Returns the sec_uid of the authenticated user.
        """
        response = self.http_client.get(f"{self.BASE_URL}/foryou")

        sec_uid = re.search('"secUid":"(.*?)",', response.text)
        if sec_uid:
            sec_uid = sec_uid.group(1)

        return sec_uid

    def get_user_from_room_id(self, room_id) -> str:
        """
        Given a room_id, I get the username
        """
        data = self._get_json(
            self.http_client.get(
                f"{self.WEBCAST_URL}/webcast/room/info/?aid=1988&room_id={room_id}"
            )
        )

        if "Follow the creator to watch their LIVE" in json.dumps(data):
            raise UserLiveError(TikTokError.ACCOUNT_PRIVATE_FOLLOW)

        if "This account is private" in json.dumps(data):
            raise UserLiveError(TikTokError.ACCOUNT_PRIVATE)

        display_id = data.get("data", {}).get("owner", {}).get("display_id")
        if display_id is None:
            raise TikTokRecorderError(TikTokError.USERNAME_ERROR)

        return display_id

    def get_room_and_user_from_url(self, live_url: str):
        """
        Given a url, get user and room_id.
        """
        response = self.http_client.get(live_url, allow_redirects=False)
        content = response.text

        if response.status_code == StatusCode.REDIRECT:
            raise UserLiveError(TikTokError.COUNTRY_BLACKLISTED)

        user = None
        if response.status_code == StatusCode.MOVED:  # MOBILE URL
            matches = re.findall("com/@(.*?)/live", content)
            if len(matches) < 1:
                raise LiveNotFound(TikTokError.INVALID_TIKTOK_LIVE_URL)

            user = matches[0]

        # https://www.tiktok.com/@<username>/live
        match = re.match(r"https?://(?:www\.)?tiktok\.com/@([^/]+)/live", live_url)
        if match:
            user = match.group(1)

        if user is None:
            raise LiveNotFound(TikTokError.INVALID_TIKTOK_LIVE_URL)

        room_id = self.get_room_id_from_user(user)

        return user, room_id

    def _old_get_room_id_from_user(self, user: str) -> str:
        params = {"uniqueId": user, "giftInfo": "false"}

        response = self.http_client.get(
            f"{self.EULER_API}/webcast/room_info",
            params=params,
            headers={"x-api-key": ""},
        )

        if response.status_code != 200:
            raise UserLiveError(TikTokError.ROOM_ID_ERROR)

        data = response.json()

        room_id = data.get("data", {}).get("room_info", {}).get("id")
        if not room_id:
            raise UserLiveError(TikTokError.ROOM_ID_ERROR)

        return room_id

    def _tikrec_get_room_id_signed_url(self, user: str) -> str:
        try:
            response = self.http_client.get(
                f"{self.TIKREC_API}/tiktok/room/api/sign",
                params={"unique_id": user},
            )
            response.raise_for_status()
        except Exception as e:
            raise TikRecUnavailableError(
                f"tikrec signing service is unreachable: {e}"
            ) from e

        try:
            data = response.json()
        except ValueError as e:
            raise TikRecUnavailableError(
                "tikrec signing service returned an invalid response "
                "(expected JSON, got something else — the service may be down)."
            ) from e

        signed_path = data.get("signed_path")
        if not signed_path:
            raise TikRecUnavailableError(
                "tikrec signing service did not return a signed_path "
                "(the service may be down or overloaded)."
            )

        return f"{self.BASE_URL}{signed_path}"

    def get_room_id_from_user(self, user: str) -> str | None:
        """Given a username, get the room_id."""
        try:
            signed_url = self._tikrec_get_room_id_signed_url(user)
        except TikRecUnavailableError as e:
            logger.warning(
                f"[!] tikrec is unavailable ({e}). "
                "Falling back to unsigned API — recording continues but may be less reliable."
            )
            return self._old_get_room_id_from_user(user)

        response = self.http_client.get(signed_url)
        content = response.text

        if not content or "Please wait" in content:
            raise UserLiveError(TikTokError.WAF_BLOCKED)

        data = self._get_json(response)
        return (data.get("data") or {}).get("user", {}).get("roomId")

    def _user_list_url(self, *, sec_uid, cursor, ms_token, scene, count) -> str:
        sec = f"&secUid={sec_uid}" if sec_uid else ""
        return (
            f"{self.BASE_URL}/api/user/list/?{_USER_LIST_STATIC_PARAMS}"
            f"&count={count}&maxCursor={cursor}&minCursor={cursor}"
            f"&scene={scene}{sec}&msToken={ms_token}&X-Bogus=&X-Gnarly="
        )

    def get_following(
        self, sec_uid, scene=USER_LIST_SCENE_FOLLOWING, count=30
    ) -> list[dict]:
        """
        Paginate /api/user/list/ for the authenticated user and return rich
        entries: {"unique_id", "nickname", "avatar_url"}. Returns [] when the
        list is empty.
        """
        # Priming request: harvest a fresh msToken from the response cookies.
        priming = self.http_client.get(
            self._user_list_url(
                sec_uid="",
                cursor=0,
                ms_token=_SEED_MS_TOKEN,
                scene=scene,
                count=count,
            )
        )
        ms_token = priming.cookies.get("msToken")
        if not ms_token:
            raise TikTokRecorderError(
                "TikTok session cookie missing or expired — update src/cookies.json"
            )

        entries = []
        seen = set()
        cursor = 0
        has_more = True

        while has_more:
            response = self.http_client.get(
                self._user_list_url(
                    sec_uid=sec_uid,
                    cursor=cursor,
                    ms_token=ms_token,
                    scene=scene,
                    count=count,
                )
            )

            if response.status_code != StatusCode.OK:
                raise TikTokRecorderError("Failed to retrieve user list.")

            if not response.content:
                # A 200 with an empty body is how TikTok rejects an unsigned
                # /api/user/list/ request. Without a valid X-Bogus signature
                # (which this client does not generate) the endpoint returns
                # nothing — the same limitation that affects followers mode.
                raise TikTokRecorderError(
                    "TikTok returned no data for the following list. The "
                    "account list endpoint now requires request signing that "
                    "isn't available, or the session in src/cookies.json has "
                    "expired."
                )

            data = self._get_json(response)

            for entry in data.get("userList", []):
                user = entry.get("user", {})
                unique_id = user.get("uniqueId")
                if not unique_id or unique_id in seen:
                    continue
                seen.add(unique_id)
                entries.append(
                    {
                        "unique_id": unique_id,
                        "nickname": user.get("nickname"),
                        "avatar_url": _extract_avatar(user),
                    }
                )

            has_more = data.get("hasMore", False)
            new_cursor = data.get("minCursor", 0)

            if new_cursor == cursor:
                break

            cursor = new_cursor

        return entries

    def get_followers_list(self, sec_uid) -> list:
        """
        Returns all followers for the authenticated user by paginating
        """
        followers = [e["unique_id"] for e in self.get_following(sec_uid, count=5)]

        if not followers:
            raise TikTokRecorderError("Followers list is empty.")

        return followers

    def get_user_details(self, user: str) -> dict | None:
        """
        Best-effort profile lookup (nickname, avatar) via the tikrec-signed
        room API. Returns None when the data can't be fetched — profile info
        is cosmetic and must never break callers.
        """
        try:
            signed_url = self._tikrec_get_room_id_signed_url(user)
        except TikRecUnavailableError:
            return None

        response = self.http_client.get(signed_url)
        content = response.text
        if not content or "Please wait" in content:
            return None

        try:
            data = self._get_json(response)
        except TikTokRecorderError:
            return None

        user_data = (data.get("data") or {}).get("user") or {}
        if not user_data:
            return None

        return {
            "unique_id": user_data.get("uniqueId") or user,
            "nickname": user_data.get("nickname"),
            "avatar_url": _extract_avatar(user_data),
        }

    def _get_stream_url_from_page(self, user: str) -> str | None:
        """
        Fallback: fetch the live page HTML and extract the stream URL directly.
        Used when the webcast API returns status code 4003110 (WAF/access restriction).
        """
        try:
            live_page_url = f"{self.BASE_URL}/@{user}/live"
            response = self.http_client.get(live_page_url)
            content = response.text

            flv_matches = re.findall(r'https?://[^\s"\'<>]+\.flv[^\s"\'<>]*', content)
            if flv_matches:
                # Prefer original (_or4) or SD quality
                for url in flv_matches:
                    url = html.unescape(url.rstrip("\\"))
                    if "_or4" in url or "_sd" in url:
                        logger.info(f"Found stream URL from page: {url[:80]}...")
                        return url
                return html.unescape(flv_matches[0].rstrip("\\"))

            hls_matches = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', content)
            if hls_matches:
                return html.unescape(hls_matches[0].rstrip("\\"))

            return None
        except Exception as e:
            logger.warning(f"Failed to extract stream URL from page: {e}")
            return None

    def _add_live_url_candidate(self, candidates: list[str], url: str | None) -> None:
        if url and url not in candidates:
            candidates.append(url)

    def get_live_urls(self, room_id: str, user: str = None) -> list[str]:
        """
        Return candidate CDN URLs (flv or m3u8) for the streaming.
        If the API returns status code 4003110 and a username is provided,
        falls back to scraping the live page directly.
        """
        data = self._get_json(
            self.http_client.get(
                f"{self.WEBCAST_URL}/webcast/room/info/?aid=1988&room_id={room_id}"
            )
        )

        if "This account is private" in json.dumps(data):
            raise UserLiveError(TikTokError.ACCOUNT_PRIVATE)

        status_code = data.get("status_code", 0)

        if status_code == 4003110:
            if user:
                logger.info(
                    "API blocked by WAF (4003110). Trying fallback: extract stream URL from live page..."
                )
                fallback_url = self._get_stream_url_from_page(user)
                if fallback_url:
                    return [fallback_url]

            raise UserLiveError(TikTokError.LIVE_RESTRICTION)

        room_data = data.get("data") or {}
        room_status = room_data.get("status")
        if room_status is not None and str(room_status) != "2":
            raise UserLiveError(TikTokError.USER_NOT_CURRENTLY_LIVE)

        stream_url = room_data.get("stream_url", {})

        sdk_data_str = (
            stream_url.get("live_core_sdk_data", {})
            .get("pull_data", {})
            .get("stream_data")
        )
        candidates = []
        if not sdk_data_str:
            logger.warning(
                "No SDK stream data found. Falling back to legacy URLs. Consider contacting the developer to update the code."
            )
            flv_pull_url = stream_url.get("flv_pull_url", {})
            for key in ("FULL_HD1", "HD1", "SD2", "SD1"):
                self._add_live_url_candidate(candidates, flv_pull_url.get(key))
            self._add_live_url_candidate(candidates, stream_url.get("hls_pull_url"))
            self._add_live_url_candidate(candidates, stream_url.get("rtmp_pull_url"))
            return candidates

        # Extract stream options
        sdk_data = json.loads(sdk_data_str).get("data", {})
        qualities = (
            stream_url.get("live_core_sdk_data", {})
            .get("pull_data", {})
            .get("options", {})
            .get("qualities", [])
        )
        if not qualities:
            logger.warning("No qualities found in the stream data. Returning None.")
            return candidates
        level_map = {q["sdk_key"]: q["level"] for q in qualities}

        ordered_sdk_keys = sorted(
            sdk_data.keys(), key=lambda key: level_map.get(key, -1), reverse=True
        )
        for sdk_key in ordered_sdk_keys:
            entry = sdk_data[sdk_key]
            stream_main = entry.get("main", {})
            self._add_live_url_candidate(candidates, stream_main.get("flv"))
            self._add_live_url_candidate(
                candidates, stream_main.get("hls") or stream_main.get("m3u8")
            )

        flv_pull_url = stream_url.get("flv_pull_url", {})
        for key in ("FULL_HD1", "HD1", "SD2", "SD1"):
            self._add_live_url_candidate(candidates, flv_pull_url.get(key))
        self._add_live_url_candidate(candidates, stream_url.get("hls_pull_url"))
        self._add_live_url_candidate(candidates, stream_url.get("rtmp_pull_url"))

        return candidates

    def get_live_url(self, room_id: str, user: str = None) -> str | None:
        """Return the first candidate CDN URL for the streaming."""
        live_urls = self.get_live_urls(room_id, user=user)
        if live_urls:
            return live_urls[0]
        return None

    def get_live_url_candidates(self, room_id: str, user: str = None) -> list[str]:
        """Return candidate CDN URLs for the streaming."""
        return self.get_live_urls(room_id, user=user)

    def download_live_stream(self, live_url: str):
        """Generator that returns the live stream for a given room_id."""
        stream = self._http_client_stream.get(live_url, stream=True)
        try:
            stream.raise_for_status()
            for chunk in stream.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk
        finally:
            # also runs on GeneratorExit when the consumer abandons the
            # generator, so the connection is always released
            stream.close()
