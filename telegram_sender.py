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

logger = logging.getLogger(__name__)

_PHOTO_MAX_BYTES = 10 * 1024 * 1024
_VIDEO_MAX_BYTES = 50 * 1024 * 1024
_SEND_INTERVAL_SEC = 1.0
_VOICE_MIME_TYPE = "audio/mp4"


class TelegramSender:
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id
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

        if msg_type == "text":
            self._send_text(header, msg.text)
        elif msg_type == "picture":
            self._send_picture(header, msg, api_client, access_token)
        elif msg_type == "video":
            self._send_video(header, msg, api_client, access_token)
        elif msg_type == "voice":
            self._send_voice(header, msg, api_client, access_token)
        elif msg_type == "link":
            self._send_link(header, msg)
        else:
            logger.warning("Unknown message type: %s", msg_type)

        time.sleep(_SEND_INTERVAL_SEC)

    def _send_text(self, header: str, text: Optional[str]) -> None:
        body = self._normalize_text(text)
        payload = header if not body else f"{header}\n\n{body}"
        self._send_text_raw(payload)

    def _send_link(self, header: str, msg: TimelineMessage) -> None:
        parts = []
        body = self._normalize_text(msg.text)
        if body:
            parts.append(body)
        if msg.link_params and msg.link_params.url:
            parts.append(msg.link_params.url)
        payload = header if not parts else f"{header}\n\n" + "\n".join(parts)
        self._send_text_raw(payload)

    def _send_picture(
        self,
        header: str,
        msg: TimelineMessage,
        client: IdolMessageClient,
        access_token: str,
    ) -> None:
        if not msg.file:
            self._send_text_raw(f"{header}\n\n[image unavailable]")
            return

        file_bytes = client.download_file(msg.file, access_token)
        caption = self._build_caption(header, msg.text)
        if len(file_bytes) <= _PHOTO_MAX_BYTES:
            self._post(
                "sendPhoto",
                data={"chat_id": self._chat_id, "caption": caption},
                files={"photo": ("image.jpg", io.BytesIO(file_bytes), "image/jpeg")},
            )
            return

        self._post(
            "sendDocument",
            data={"chat_id": self._chat_id, "caption": caption},
            files={"document": ("image.jpg", io.BytesIO(file_bytes), "image/jpeg")},
        )

    def _send_video(
        self,
        header: str,
        msg: TimelineMessage,
        client: IdolMessageClient,
        access_token: str,
    ) -> None:
        if not msg.file:
            self._send_text_raw(f"{header}\n\n[video unavailable]")
            return

        file_bytes = client.download_file(msg.file, access_token)
        caption = self._build_caption(header, msg.text)
        if len(file_bytes) <= _VIDEO_MAX_BYTES:
            self._post(
                "sendVideo",
                data={"chat_id": self._chat_id, "caption": caption},
                files={"video": ("video.mp4", io.BytesIO(file_bytes), "video/mp4")},
            )
            return

        logger.warning("Video exceeds Telegram limit (%d bytes), send text notice instead", len(file_bytes))
        self._send_text_raw(
            f"{header}\n\nVideo file is too large to send directly ({len(file_bytes) / 1024 / 1024:.1f} MB)"
        )

    def _send_voice(
        self,
        header: str,
        msg: TimelineMessage,
        client: IdolMessageClient,
        access_token: str,
    ) -> None:
        if not msg.file:
            self._send_text_raw(f"{header}\n\n[audio unavailable]")
            return

        file_bytes = client.download_file(msg.file, access_token)
        caption = self._build_caption(header, msg.text)
        filename = self._build_voice_filename(msg.file)
        self._post(
            "sendAudio",
            data={"chat_id": self._chat_id, "caption": caption},
            files={"audio": (filename, io.BytesIO(file_bytes), _VOICE_MIME_TYPE)},
        )

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

    @staticmethod
    def _build_caption(header: str, text: Optional[str]) -> str:
        body = TelegramSender._normalize_text(text)
        caption = header if not body else f"{header}\n\n{body}"
        if len(caption) > 1024:
            caption = caption[:1020] + "\n..."
        return caption

    @staticmethod
    def _normalize_text(text: Optional[str]) -> str:
        if not text:
            return ""
        return text.replace("\\r\\n", "\n").replace("\r\n", "\n").strip()

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
