"""
Client for the idol message API.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

APP_CONFIGS = {
    "sakurazaka": {
        "base_url": "https://api.s46.glastonr.net",
        "x_talk_app_id": "jp.co.sonymusic.communication.sakurazaka 2.4",
        "name": "櫻坂46",
    },
    "hinatazaka": {
        "base_url": "https://api.kh.glastonr.net",
        "x_talk_app_id": "jp.co.sonymusic.communication.keyakizaka 2.4",
        "name": "日向坂46",
    },
    "nogizaka": {
        "base_url": "https://api.n46.glastonr.net",
        "x_talk_app_id": "jp.co.sonymusic.communication.nogizaka 2.4",
        "name": "乃木坂46",
    },
    "asukasaito": {
        "base_url": "https://api.asukasaito.glastonr.net",
        "x_talk_app_id": "jp.co.sonymusic.communication.asukasaito 2.4",
        "name": "齋藤飛鳥",
    },
    "maishiraishi": {
        "base_url": "https://api.maishiraishi.glastonr.net",
        "x_talk_app_id": "jp.co.sonymusicsolutions.maishiraishi 2.4",
        "name": "白石麻衣",
    },
    "yodel": {
        "base_url": "https://api.ydl.glastonr.net",
        "x_talk_app_id": "jp.co.sonymusic.communication.yodel 2.4",
        "name": "yodel",
    },
}

_USER_AGENT = (
    "Dalvik/2.1.0 (Linux; U; Android 6.0; "
    "Samsung Galaxy S7 for keyaki messages Build/MRA58K)"
)

_TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
_TIMELINE_TIMEOUT_SEC = 10


class TemporaryAPIError(RuntimeError):
    """
    Raised when the MSG API is temporarily unavailable and a later retry may succeed.
    """


class UnauthorizedAPIError(RuntimeError):
    """
    Raised when the current access token is no longer accepted by the MSG API.
    """


@dataclass
class TokenResponse:
    access_token: str
    expires_in: int
    refresh_token: str


@dataclass
class GroupSubscription:
    auto_renewing: bool
    start_at: str
    subscription_type: str
    end_at: Optional[str] = None


@dataclass
class Group:
    id: int
    name: str
    thumbnail: str
    state: str
    tags: list
    priority: int
    is_letter_destination: bool
    updated_at: str
    subscription: Optional[GroupSubscription] = None
    phone_image: Optional[str] = None
    trial_days: Optional[int] = None

    @property
    def is_subscribed(self) -> bool:
        return self.subscription is not None


@dataclass
class LinkParams:
    url: str
    method: str
    parameters: list
    send_id: Optional[int] = None


@dataclass
class TimelineMessage:
    id: int
    group_id: int
    messages_type: str
    state: str
    published_at: str
    updated_at: str
    is_favorite: bool
    is_silent: bool
    text: Optional[str] = None
    file: Optional[str] = None
    thumbnail: Optional[str] = None
    thumbnail_width: Optional[int] = None
    thumbnail_height: Optional[int] = None
    member_id: Optional[int] = None
    publish_type: Optional[str] = None
    link_params: Optional[LinkParams] = None


@dataclass
class Timeline:
    messages: list
    letters: list
    comments: list
    queried_at: str


class IdolMessageClient:
    """
    HTTP client for the MSG API.
    """

    def __init__(self, app_key: str):
        if app_key not in APP_CONFIGS:
            raise ValueError(f"Unknown app_key: {app_key}. Choose from: {list(APP_CONFIGS.keys())}")

        config = APP_CONFIGS[app_key]
        self.app_key = app_key
        self.app_name = config["name"]
        self.base_url = config["base_url"].rstrip("/")
        self._x_talk_app_id = config["x_talk_app_id"]
        self._session = requests.Session()

    def _base_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Talk-App-ID": self._x_talk_app_id,
            "Accept-Language": "ja-JP",
            "User-Agent": _USER_AGENT,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "TE": "gzip, deflate; q=0.5",
        }

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {access_token}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _describe_response_error(self, response: requests.Response) -> str:
        status = response.status_code
        reason = response.reason or "Unknown error"

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("detail") or payload.get("error")
            if message:
                return f"HTTP {status} {reason}: {message}"

        text = response.text.strip()
        if text:
            text = text.replace("\n", " ")
            if len(text) > 160:
                text = text[:157] + "..."
            return f"HTTP {status} {reason}: {text}"

        return f"HTTP {status} {reason}"

    def _request(
        self,
        method: str,
        url_or_path: str,
        *,
        headers: dict[str, str],
        timeout: int,
        **kwargs,
    ) -> requests.Response:
        url = url_or_path if url_or_path.startswith("http") else self._url(url_or_path)

        try:
            response = self._session.request(
                method=method,
                url=url,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except requests.HTTPError as exc:
            response = exc.response
            if response is None:
                raise TemporaryAPIError(f"{method} {url} failed without a response") from exc

            status = response.status_code
            detail = self._describe_response_error(response)
            if status == 401:
                raise UnauthorizedAPIError(detail) from exc
            if status in _TRANSIENT_STATUS_CODES:
                raise TemporaryAPIError(detail) from exc
            raise
        except requests.RequestException as exc:
            raise TemporaryAPIError(f"{method} {url} failed: {exc}") from exc

    def update_token(self, refresh_token: str) -> TokenResponse:
        response = self._request(
            "POST",
            "/v2/update_token",
            headers=self._base_headers(),
            json={"refresh_token": refresh_token},
            timeout=30,
        )
        data = response.json()
        return TokenResponse(
            access_token=data["access_token"],
            expires_in=data["expires_in"],
            refresh_token=data["refresh_token"],
        )

    def get_groups(self, access_token: str) -> list[Group]:
        response = self._request(
            "GET",
            "/v2/groups",
            headers=self._auth_headers(access_token),
            timeout=30,
        )
        return [self._parse_group(group) for group in response.json()]

    def get_timeline(
        self,
        access_token: str,
        group_id: int,
        updated_from: str = "2000-01-01T00:00:00Z",
        count: int = 100,
    ) -> Timeline:
        response = self._request(
            "GET",
            f"/v2/groups/{group_id}/timeline",
            headers=self._auth_headers(access_token),
            params={
                "created_from": "2000-01-01T00:00:00Z",
                "updated_from": updated_from,
                "count": str(count),
                "order": "asc",
            },
            timeout=_TIMELINE_TIMEOUT_SEC,
        )
        data = response.json()
        return Timeline(
            messages=[self._parse_message(message) for message in data.get("messages", [])],
            letters=data.get("letters", []),
            comments=data.get("comments", []),
            queried_at=data.get("queried_at", ""),
        )

    def get_past_messages(self, access_token: str, group_id: int) -> list[TimelineMessage]:
        response = self._request(
            "GET",
            f"/v2/groups/{group_id}/past_messages",
            headers=self._auth_headers(access_token),
            params={"order": "asc"},
            timeout=30,
        )
        data = response.json()
        return [self._parse_message(message) for message in data.get("messages", [])]

    def download_file(self, url: str, access_token: str) -> bytes:
        last_error: Optional[Exception] = None

        for attempt in range(5):
            try:
                response = self._request(
                    "GET",
                    url,
                    headers=self._auth_headers(access_token),
                    timeout=120,
                    stream=True,
                )
                content = response.content
                if not content:
                    raise ValueError("Downloaded file is empty.")
                return content
            except TemporaryAPIError as exc:
                last_error = exc
                if attempt == 4:
                    break
                wait_seconds = (attempt + 1) * 5
                logger.warning(
                    "Temporary download failure, retry in %ds (%d/5): %s",
                    wait_seconds,
                    attempt + 1,
                    exc,
                )
                time.sleep(wait_seconds)

        if last_error:
            raise last_error
        raise RuntimeError("Failed to download file.")

    def _parse_group(self, data: dict) -> Group:
        subscription_data = data.get("subscription")
        subscription = None
        if subscription_data:
            subscription = GroupSubscription(
                auto_renewing=subscription_data.get("auto_renewing", False),
                start_at=subscription_data.get("start_at", ""),
                subscription_type=subscription_data.get("type", ""),
                end_at=subscription_data.get("end_at"),
            )
        return Group(
            id=data["id"],
            name=data["name"],
            thumbnail=data.get("thumbnail", ""),
            state=data.get("state", ""),
            tags=data.get("tags", []),
            priority=data.get("priority", 0),
            is_letter_destination=data.get("is_letter_destination", False),
            updated_at=data.get("updated_at", ""),
            subscription=subscription,
            phone_image=data.get("phone_image"),
            trial_days=data.get("trial_days"),
        )

    def _parse_message(self, data: dict) -> TimelineMessage:
        link_params_data = data.get("link_params")
        link_params = None
        if link_params_data:
            link_params = LinkParams(
                url=link_params_data.get("url", ""),
                method=link_params_data.get("method", ""),
                parameters=link_params_data.get("parameters", []),
                send_id=link_params_data.get("sendid"),
            )
        return TimelineMessage(
            id=data["id"],
            group_id=data["group_id"],
            messages_type=data["type"],
            state=data.get("state", ""),
            published_at=data.get("published_at", ""),
            updated_at=data.get("updated_at", ""),
            is_favorite=data.get("is_favorite", False),
            is_silent=data.get("is_silent", False),
            text=data.get("text"),
            file=data.get("file"),
            thumbnail=data.get("thumbnail"),
            thumbnail_width=data.get("thumbnail_width"),
            thumbnail_height=data.get("thumbnail_height"),
            member_id=data.get("member_id"),
            publish_type=data.get("publish_type"),
            link_params=link_params,
        )
