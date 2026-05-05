"""
telegram_sender.py
Telegram Bot API wrapper for the original multi-group bot.
"""

import io
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote, urlparse

import requests

from api_client import IdolMessageClient, TimelineMessage
from translator import GeminiTranslator, TranslationError

logger = logging.getLogger(__name__)

_PHOTO_MAX_BYTES = 10 * 1024 * 1024
_VIDEO_MAX_BYTES = 50 * 1024 * 1024
_SEND_INTERVAL_SEC = 1.0
_VOICE_MIME_TYPE = "audio/mp4"


class TelegramSender:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        translator: GeminiTranslator | None = None,
    ):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._translator = translator
        self._api_base = f"https://api.telegram.org/bot{bot_token}"
        self._session = requests.Session()

    def _api_url(self, method: str) -> str:
        return f"{self._api_base}/{method}"

    @staticmethod
    def _rewind_files(files: object) -> None:
        if not isinstance(files, dict):
            return

        for value in files.values():
            file_obj = None
            if isinstance(value, tuple):
                if len(value) >= 2:
                    file_obj = value[1]
            else:
                file_obj = value

            if hasattr(file_obj, "seek"):
                file_obj.seek(0)

    def _post(self, method: str, **kwargs) -> dict:
        for attempt in range(5):
            try:
                self._rewind_files(kwargs.get("files"))
                response = self._session.post(
                    self._api_url(method),
                    timeout=60,
                    **kwargs,
                )
                data = response.json()
                if data.get("ok"):
                    return data
                if response.status_code == 429:
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.warning("Telegram rate limit hit. Retry after %s seconds", retry_after)
                    time.sleep(retry_after)
                    continue
                logger.error("Telegram API error payload: %s", data)
                raise RuntimeError(f"Telegram API error: {data.get('description', 'Unknown error')}")
            except RuntimeError:
                raise
            except Exception as exc:
                if attempt == 4:
                    raise
                wait_seconds = (attempt + 1) * 3
                logger.warning(
                    "Telegram send failed, retry in %s seconds (%s/5): %s",
                    wait_seconds,
                    attempt + 1,
                    exc,
                )
                time.sleep(wait_seconds)
        raise RuntimeError("Telegram API retries exhausted")

    def send_message(
        self,
        msg: TimelineMessage,
        member_name: str,
        app_name: str,
        api_client: IdolMessageClient,
        access_token: str,
    ) -> None:
        header = self._format_header(msg, member_name, app_name)
        msg_type = msg.messages_type
        app_key = api_client.app_key

        if msg_type == "text":
            self._send_text(header, msg.text, app_key, member_name)
        elif msg_type == "picture":
            self._send_picture(header, msg, api_client, access_token, app_key, member_name)
        elif msg_type == "video":
            self._send_video(header, msg, api_client, access_token, app_key, member_name)
        elif msg_type == "voice":
            self._send_voice(header, msg, api_client, access_token, app_key, member_name)
        elif msg_type == "link":
            self._send_link(header, msg, app_key, member_name)
        else:
            logger.warning("Unknown message type: %s", msg_type)

        time.sleep(_SEND_INTERVAL_SEC)

    def _send_text(
        self,
        header: str,
        text: Optional[str],
        app_key: str,
        member_name: str,
    ) -> None:
        body = self._compose_body(text, app_key, member_name)
        payload = header if not body else f"{header}\n\n{body}"
        self._send_text_raw(payload)

    def _send_link(
        self,
        header: str,
        msg: TimelineMessage,
        app_key: str,
        member_name: str,
    ) -> None:
        parts = []
        body = self._compose_body(msg.text, app_key, member_name)
        if body:
            parts.append(body)
        if msg.link_params and msg.link_params.url:
            parts.append(msg.link_params.url)
        payload = header if not parts else f"{header}\n\n" + "\n\n".join(parts)
        self._send_text_raw(payload)

    def _send_picture(
        self,
        header: str,
        msg: TimelineMessage,
        client: IdolMessageClient,
        access_token: str,
        app_key: str,
        member_name: str,
    ) -> None:
        if not msg.file:
            self._send_text_raw(f"{header}\n\n[image unavailable]")
            return

        file_bytes = client.download_file(msg.file, access_token)
        caption, overflow_text = self._build_caption(header, msg.text, app_key, member_name)
        if len(file_bytes) <= _PHOTO_MAX_BYTES:
            self._post(
                "sendPhoto",
                data={"chat_id": self._chat_id, "caption": caption},
                files={"photo": ("image.jpg", io.BytesIO(file_bytes), "image/jpeg")},
            )
            if overflow_text:
                self._send_text_raw(overflow_text)
            return

        self._post(
            "sendDocument",
            data={"chat_id": self._chat_id, "caption": caption},
            files={"document": ("image.jpg", io.BytesIO(file_bytes), "image/jpeg")},
        )
        if overflow_text:
            self._send_text_raw(overflow_text)

    def _send_video(
        self,
        header: str,
        msg: TimelineMessage,
        client: IdolMessageClient,
        access_token: str,
        app_key: str,
        member_name: str,
    ) -> None:
        if not msg.file:
            self._send_text_raw(f"{header}\n\n[video unavailable]")
            return

        file_bytes = client.download_file(msg.file, access_token)
        caption, overflow_text = self._build_caption(header, msg.text, app_key, member_name)
        if len(file_bytes) <= _VIDEO_MAX_BYTES:
            self._post(
                "sendVideo",
                data={"chat_id": self._chat_id, "caption": caption},
                files={"video": ("video.mp4", io.BytesIO(file_bytes), "video/mp4")},
            )
            if overflow_text:
                self._send_text_raw(overflow_text)
            return

        logger.warning("Video exceeds Telegram limit (%d bytes), send text notice instead", len(file_bytes))
        self._send_text_raw(
            f"{header}\n\nVideo file is too large to send directly ({len(file_bytes) / 1024 / 1024:.1f} MB)"
        )
        if overflow_text:
            self._send_text_raw(overflow_text)

    def _send_voice(
        self,
        header: str,
        msg: TimelineMessage,
        client: IdolMessageClient,
        access_token: str,
        app_key: str,
        member_name: str,
    ) -> None:
        if not msg.file:
            self._send_text_raw(f"{header}\n\n[audio unavailable]")
            return

        file_bytes = client.download_file(msg.file, access_token)
        caption, overflow_text = self._build_caption(header, msg.text, app_key, member_name)
        filename = self._build_voice_filename(msg.file)
        self._post(
            "sendAudio",
            data={"chat_id": self._chat_id, "caption": caption},
            files={"audio": (filename, io.BytesIO(file_bytes), _VOICE_MIME_TYPE)},
        )
        if overflow_text:
            self._send_text_raw(overflow_text)

    def _send_text_raw(self, text: str) -> None:
        if len(text) > 4096:
            text = text[:4090] + "\n..."
        self._post(
            "sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": text,
                "disable_web_page_preview": False,
            },
        )

    @staticmethod
    def _format_header(msg: TimelineMessage, member_name: str, app_name: str) -> str:
        del app_name
        jst = timezone(timedelta(hours=9))
        dt = datetime.strptime(msg.published_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        published = dt.astimezone(jst).strftime("%Y/%m/%d %H:%M:%S")
        member_tag = member_name.replace(" ", "").replace("　", "")
        return f"#{member_tag} {published}"

    def _build_caption(
        self,
        header: str,
        text: Optional[str],
        app_key: str,
        member_name: str,
    ) -> tuple[str, str | None]:
        body = self._compose_body(text, app_key, member_name)
        caption = header if not body else f"{header}\n\n{body}"
        if len(caption) <= 1024:
            return caption, None

        logger.warning("Caption too long after translation, sending text as a follow-up message")
        return header, f"{header}\n\n{body}" if body else None

    @staticmethod
    def _normalize_text(text: Optional[str]) -> str:
        if not text:
            return ""
        return text.replace("\\r\\n", "\n").replace("\r\n", "\n").strip()

    def _compose_body(
        self,
        text: Optional[str],
        app_key: str,
        member_name: str,
    ) -> str:
        original = self._normalize_text(text)
        if not original:
            return ""

        translated = self._translate_text(original, app_key, member_name)
        if not translated or self._is_same_text(original, translated):
            return original

        return f"{original}\n\n{translated}"

    def _translate_text(self, text: str, app_key: str, member_name: str) -> str:
        if not self._translator or not self._translator.enabled:
            return ""

        try:
            return self._translator.translate_to_chinese(
                text,
                app_key=app_key,
                sender_member_name=member_name,
            )
        except TranslationError as exc:
            logger.warning("Translation skipped for current message: %s", exc)
            return ""

    @staticmethod
    def _is_same_text(source: str, translated: str) -> bool:
        def compact(value: str) -> str:
            return "".join(value.split())

        return compact(source) == compact(translated)

    @staticmethod
    def _build_voice_filename(file_url: Optional[str]) -> str:
        if not file_url:
            return "voice.m4a"

        parsed = urlparse(file_url)
        basename = unquote(parsed.path.rstrip("/").split("/")[-1]).strip()
        if not basename:
            return "voice.m4a"

        stem = basename.rsplit(".", 1)[0].strip() or basename
        return f"{stem}.m4a"

    def send_system_notification(self, text: str) -> None:
        try:
            self._send_text_raw(f"[system]\n{text}")
        except Exception as exc:
            logger.error("Failed to send system notification: %s", exc)

    def describe_translation_mode(self) -> str:
        if not self._translator:
            return "translation: disabled"
        return self._translator.describe_status()
