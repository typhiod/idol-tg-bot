"""
Gemini-based translation helper.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from member_glossary import build_translation_guidance

logger = logging.getLogger(__name__)


class TranslationError(RuntimeError):
    """
    Raised when the translation provider returns an unusable response.
    """


class GeminiTranslator:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3.1-flash-lite-preview",
        timeout_seconds: int = 30,
        min_interval_seconds: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model.strip() or "gemini-3.1-flash-lite-preview"
        self._timeout_seconds = max(5, timeout_seconds)
        self._min_interval_seconds = max(0, min_interval_seconds)
        self._enabled = enabled and bool(self._api_key)
        self._session = requests.Session()
        self._cache: dict[str, str] = {}
        self._max_cache_size = 500
        self._last_request_time: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def model(self) -> str:
        return self._model

    def describe_status(self) -> str:
        if self._enabled:
            return f"translation: enabled ({self._model})"
        return "translation: disabled"

    def _wait_for_rate_limit(self) -> None:
        """Enforce minimum interval between API calls to avoid 429 / RESOURCE_EXHAUSTED."""
        if self._min_interval_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_interval_seconds:
            wait = self._min_interval_seconds - elapsed
            logger.debug("Rate-limit throttle: waiting %.1fs before next Gemini call", wait)
            time.sleep(wait)

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        """Return True if the error looks like a quota / rate-limit rejection."""
        msg = str(exc).lower()
        return any(
            keyword in msg
            for keyword in (
                "429",
                "resource_exhausted",
                "resource has been exhausted",
                "quota",
                "rate limit",
                "rate_limit",
                "too many requests",
            )
        )

    def translate_to_chinese(
        self,
        text: Optional[str],
        *,
        app_key: str | None = None,
        sender_member_name: str | None = None,
    ) -> str:
        normalized = self._normalize_text(text)
        if not self._enabled or not normalized:
            return ""

        cache_key = self._build_cache_key(
            app_key=app_key,
            sender_member_name=sender_member_name,
            text=normalized,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        system_instruction = build_translation_guidance(
            app_key=app_key or "",
            sender_member_name=sender_member_name or "",
        )
        payload = {
            "system_instruction": {
                "parts": [
                    {
                        "text": system_instruction,
                    }
                ]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": normalized,
                        }
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "text/plain",
            },
        }

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                self._wait_for_rate_limit()
                translated = self._request_translation(payload)
                if not translated:
                    raise TranslationError("Gemini returned an empty translation.")
                if len(self._cache) >= self._max_cache_size:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[cache_key] = translated
                return translated
            except Exception as exc:
                last_error = exc
                if attempt == 2:
                    break
                if self._is_rate_limit_error(exc):
                    wait_seconds = 5 * (attempt + 1)  # 5s, 10s, 20s
                else:
                    wait_seconds = attempt + 1  # 1s, 2s
                logger.warning(
                    "Gemini translation failed, retry in %ds (%d/3): %s",
                    wait_seconds,
                    attempt + 1,
                    exc,
                )
                time.sleep(wait_seconds)

        raise TranslationError(f"Gemini translation failed: {last_error}")

    def _request_translation(self, payload: dict) -> str:
        response = self._session.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
            headers={
                "x-goog-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout_seconds,
        )

        try:
            data = response.json()
        except ValueError as exc:
            raise TranslationError(f"Gemini returned non-JSON response: {response.text[:200]}") from exc

        if response.status_code >= 400:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            message = error.get("message") or response.text[:200]
            raise TranslationError(f"Gemini HTTP {response.status_code}: {message}")

        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not candidates:
            prompt_feedback = data.get("promptFeedback") if isinstance(data, dict) else None
            raise TranslationError(f"Gemini returned no candidates: {prompt_feedback}")

        parts = candidates[0].get("content", {}).get("parts", [])
        translated_fragments = [
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and part.get("text")
        ]
        translated = "\n".join(fragment.strip() for fragment in translated_fragments if fragment.strip()).strip()
        if not translated:
            raise TranslationError("Gemini candidate contained no text.")
        return translated

    @staticmethod
    def _normalize_text(text: Optional[str]) -> str:
        if not text:
            return ""
        return text.replace("\\r\\n", "\n").replace("\r\n", "\n").strip()

    @staticmethod
    def _build_cache_key(
        *,
        app_key: str | None,
        sender_member_name: str | None,
        text: str,
    ) -> str:
        return "||".join(
            [
                app_key or "",
                sender_member_name or "",
                text,
            ]
        )
