"""
Token manager for refresh/access tokens.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from api_client import IdolMessageClient, TemporaryAPIError, TokenResponse

logger = logging.getLogger(__name__)

TOKEN_DIR = Path("data/tokens")
TOKEN_DIR.mkdir(parents=True, exist_ok=True)


class TokenManager:
    """
    Manage refresh/access tokens for one app.
    """

    _REFRESH_MARGIN_SEC = 300
    _TRANSIENT_RETRY_MIN_SEC = 15
    _TRANSIENT_RETRY_MAX_SEC = 300

    def __init__(self, app_key: str, client: IdolMessageClient):
        self.app_key = app_key
        self._client = client
        self._token_file = TOKEN_DIR / f"{app_key}_tokens.json"

        self._refresh_token: Optional[str] = None
        self._bootstrap_refresh_token: Optional[str] = None
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._refresh_retry_not_before: float = 0.0
        self._refresh_retry_delay_sec = self._TRANSIENT_RETRY_MIN_SEC

        self._load_from_file()

    def set_refresh_token(self, refresh_token: str) -> None:
        """
        Register the env refresh token.

        The first available token becomes the active token. After that, a saved
        rolling token stays primary and the env token is only kept as fallback.
        """
        token = refresh_token.strip()
        if not token:
            return

        self._bootstrap_refresh_token = token

        if not self._refresh_token:
            self._refresh_token = token
            self._access_token = None
            self._expires_at = 0.0
            self._save_to_file()
            logger.info("[%s] refresh token initialized from env", self.app_key)
            return

        if self._refresh_token == token:
            logger.info("[%s] env refresh token matches stored token", self.app_key)
            return

        logger.info(
            "[%s] keeping stored refresh token and registering env token as fallback",
            self.app_key,
        )

    def get_access_token(self) -> str:
        """
        Return a valid access token, refreshing when needed.
        """
        if self._needs_refresh():
            self._refresh_access_token()

        if not self._access_token:
            raise RuntimeError(f"[{self.app_key}] access token is unavailable")

        return self._access_token

    def invalidate(self) -> None:
        """
        Force access token refresh on the next request.
        """
        logger.warning("[%s] invalidating cached access token", self.app_key)
        self._access_token = None
        self._expires_at = 0.0
        self._refresh_retry_not_before = 0.0

    def _needs_refresh(self) -> bool:
        if not self._access_token:
            return True
        return time.time() >= self._expires_at - self._REFRESH_MARGIN_SEC

    def _has_usable_access_token(self) -> bool:
        return bool(self._access_token) and time.time() < self._expires_at

    def _refresh_access_token(self) -> None:
        now = time.time()
        if self._refresh_retry_not_before and now < self._refresh_retry_not_before:
            if self._has_usable_access_token():
                return

            wait_seconds = int(self._refresh_retry_not_before - now)
            raise TemporaryAPIError(
                f"[{self.app_key}] token refresh cooldown active, retry in {wait_seconds}s"
            )

        try:
            self._do_refresh()
        except TemporaryAPIError as exc:
            wait_seconds = self._schedule_refresh_retry()
            if self._has_usable_access_token():
                logger.warning(
                    "[%s] MSG API temporarily unavailable during token refresh. "
                    "Using cached access token and retrying in %ds: %s",
                    self.app_key,
                    wait_seconds,
                    exc,
                )
                return

            raise TemporaryAPIError(
                f"[{self.app_key}] token refresh temporarily unavailable, retry in {wait_seconds}s: {exc}"
            ) from exc
        else:
            self._clear_refresh_retry()

    def _schedule_refresh_retry(self) -> int:
        wait_seconds = self._refresh_retry_delay_sec
        self._refresh_retry_not_before = time.time() + wait_seconds
        self._refresh_retry_delay_sec = min(
            self._refresh_retry_delay_sec * 2,
            self._TRANSIENT_RETRY_MAX_SEC,
        )
        return wait_seconds

    def _clear_refresh_retry(self) -> None:
        self._refresh_retry_not_before = 0.0
        self._refresh_retry_delay_sec = self._TRANSIENT_RETRY_MIN_SEC

    def _do_refresh(self) -> None:
        tokens_to_try: list[str] = []
        if self._refresh_token:
            tokens_to_try.append(self._refresh_token)
        if (
            self._bootstrap_refresh_token
            and self._bootstrap_refresh_token not in tokens_to_try
        ):
            tokens_to_try.append(self._bootstrap_refresh_token)

        if not tokens_to_try:
            raise RuntimeError(
                f"[{self.app_key}] refresh token is not configured. "
                "Call set_refresh_token() first."
            )

        logger.info("[%s] refreshing access token...", self.app_key)
        last_error: Optional[Exception] = None

        for index, refresh_token in enumerate(tokens_to_try):
            try:
                result: TokenResponse = self._client.update_token(refresh_token)
            except TemporaryAPIError:
                raise
            except Exception as exc:
                last_error = exc
                if index + 1 < len(tokens_to_try):
                    logger.warning(
                        "[%s] stored refresh token failed, retrying with env fallback: %s",
                        self.app_key,
                        exc,
                    )
                    continue
                raise RuntimeError(f"[{self.app_key}] token refresh failed: {exc}") from exc

            self._access_token = result.access_token
            self._expires_at = time.time() + result.expires_in
            self._refresh_token = result.refresh_token or refresh_token

            if index > 0:
                logger.warning("[%s] env refresh token fallback succeeded", self.app_key)

            if result.refresh_token and result.refresh_token != refresh_token:
                logger.info("[%s] refresh token rotated by server", self.app_key)

            self._save_to_file()
            logger.info(
                "[%s] access token refreshed. valid for %d seconds",
                self.app_key,
                result.expires_in,
            )
            return

        raise RuntimeError(f"[{self.app_key}] token refresh failed: {last_error}")

    def _save_to_file(self) -> None:
        data = {
            "app_key": self.app_key,
            "refresh_token": self._refresh_token,
            "access_token": self._access_token,
            "expires_at": self._expires_at,
        }
        self._token_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("[%s] token saved to %s", self.app_key, self._token_file)

    def _load_from_file(self) -> None:
        if not self._token_file.exists():
            return

        try:
            data = json.loads(self._token_file.read_text(encoding="utf-8"))
            self._refresh_token = data.get("refresh_token")
            self._access_token = data.get("access_token")
            self._expires_at = data.get("expires_at", 0.0)
            logger.info("[%s] token loaded from %s", self.app_key, self._token_file)
        except Exception as exc:
            logger.warning("[%s] failed to load token file: %s", self.app_key, exc)
