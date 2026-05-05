"""
Polling loop for the original multi-group forwarding bot.
"""

import logging
import time
from datetime import datetime, timezone

from api_client import (
    APP_CONFIGS,
    IdolMessageClient,
    TemporaryAPIError,
    UnauthorizedAPIError,
)
from state_manager import StateManager
from telegram_sender import TelegramSender
from token_manager import TokenManager

logger = logging.getLogger(__name__)

DEFAULT_COUNT = 100
MAX_COUNT = 1000


class IdolMessageBot:
    """
    Poll and forward messages for a single app.
    """

    def __init__(
        self,
        app_key: str,
        sender: TelegramSender,
        state: StateManager,
        poll_interval: int = 300,
    ):
        if app_key not in APP_CONFIGS:
            raise ValueError(f"Unknown app_key: {app_key}")

        self.app_key = app_key
        self.app_name = APP_CONFIGS[app_key]["name"]
        self._sender = sender
        self._state = state
        self._poll_interval = poll_interval
        self._api_client = IdolMessageClient(app_key)
        self._token_manager = TokenManager(app_key, self._api_client)

    def set_refresh_token(self, refresh_token: str) -> None:
        self._token_manager.set_refresh_token(refresh_token)

    def _get_token(self) -> str:
        return self._token_manager.get_access_token()

    def _fetch_timeline_with_retry(
        self,
        *,
        group_id: int,
        member_name: str,
        updated_from: str,
        count: int,
    ):
        try:
            token = self._get_token()
            timeline = self._api_client.get_timeline(
                token,
                group_id,
                updated_from=updated_from,
                count=count,
            )
            return token, timeline
        except UnauthorizedAPIError:
            logger.warning("[%s] access token expired, refresh and retry", self.app_key)
            self._token_manager.invalidate()
            token = self._get_token()
            timeline = self._api_client.get_timeline(
                token,
                group_id,
                updated_from=updated_from,
                count=count,
            )
            return token, timeline
        except TemporaryAPIError:
            raise

    def run_once(self) -> int:
        sent_count = 0
        try:
            token = self._get_token()
            groups = self._api_client.get_groups(token)
            subscribed_groups = [group for group in groups if self._is_active(group)]

            logger.info("[%s] active subscribed groups: %d", self.app_key, len(subscribed_groups))
            for group in subscribed_groups:
                logger.info("[%s] polling %s (id=%d)", self.app_key, group.name, group.id)
                sent_count += self._poll_member(group.id, group.name)
        except TemporaryAPIError as exc:
            logger.warning("[%s] MSG API temporarily unavailable: %s", self.app_key, exc)
        except Exception as exc:
            logger.error("[%s] polling failed: %s", self.app_key, exc, exc_info=True)

        return sent_count

    def _poll_member(self, group_id: int, member_name: str) -> int:
        sent_count = 0
        cursor = self._state.get_cursor(self.app_key, group_id)
        count = DEFAULT_COUNT

        while True:
            try:
                token, timeline = self._fetch_timeline_with_retry(
                    group_id=group_id,
                    member_name=member_name,
                    updated_from=cursor,
                    count=count,
                )
            except TemporaryAPIError as exc:
                logger.warning(
                    "[%s] temporary timeline fetch failure for %s (id=%d): %s",
                    self.app_key,
                    member_name,
                    group_id,
                    exc,
                )
                break

            messages = timeline.messages
            if len(messages) >= count and self._all_same_updated_at(messages):
                if count >= MAX_COUNT:
                    logger.warning(
                        "[%s] %s hit count cap (%d), some messages at the same timestamp may be skipped",
                        self.app_key,
                        member_name,
                        MAX_COUNT,
                    )
                    break
                count += DEFAULT_COUNT
                logger.debug(
                    "[%s] %s returned identical updated_at values, retry with count=%d",
                    self.app_key,
                    member_name,
                    count,
                )
                continue

            latest_successful_updated_at = None
            send_failed = False

            for msg in messages:
                if self._state.is_sent(self.app_key, msg.id):
                    continue

                logger.info(
                    "[%s] %s new message id=%d type=%s",
                    self.app_key,
                    member_name,
                    msg.id,
                    msg.messages_type,
                )

                try:
                    self._sender.send_message(
                        msg=msg,
                        member_name=member_name,
                        app_name=self.app_name,
                        api_client=self._api_client,
                        access_token=token,
                    )
                except UnauthorizedAPIError:
                    logger.warning(
                        "[%s] access token expired while sending message %d, refresh and retry once",
                        self.app_key,
                        msg.id,
                    )
                    self._token_manager.invalidate()
                    token = self._get_token()
                    self._sender.send_message(
                        msg=msg,
                        member_name=member_name,
                        app_name=self.app_name,
                        api_client=self._api_client,
                        access_token=token,
                    )
                except TemporaryAPIError as exc:
                    logger.warning(
                        "[%s] temporary MSG API failure while sending message %d for %s: %s",
                        self.app_key,
                        msg.id,
                        member_name,
                        exc,
                    )
                    send_failed = True
                    break
                except Exception as exc:
                    logger.error(
                        "[%s] failed to send message %d for %s: %s",
                        self.app_key,
                        msg.id,
                        member_name,
                        exc,
                    )
                    send_failed = True
                    break

                self._state.mark_sent(self.app_key, msg.id)
                latest_successful_updated_at = msg.updated_at
                sent_count += 1

            if latest_successful_updated_at:
                self._state.set_cursor(self.app_key, group_id, latest_successful_updated_at)

            if send_failed or len(messages) < DEFAULT_COUNT:
                break

            cursor = messages[-1].updated_at
            count = DEFAULT_COUNT

        return sent_count

    @staticmethod
    def _is_active(group) -> bool:
        if not group.subscription:
            return False
        end_at = group.subscription.end_at
        if end_at is None:
            return True
        end_dt = datetime.strptime(end_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return end_dt > datetime.now(timezone.utc)

    @staticmethod
    def _all_same_updated_at(messages: list) -> bool:
        if not messages:
            return False
        first_updated_at = messages[0].updated_at
        return all(message.updated_at == first_updated_at for message in messages)


class BotRunner:
    """
    Manage one or more pollers that share a Telegram sender.
    """

    def __init__(
        self,
        sender: TelegramSender,
        state: StateManager,
        poll_interval: int = 300,
    ):
        self._sender = sender
        self._state = state
        self._poll_interval = poll_interval
        self._bots: list[IdolMessageBot] = []

    def add_app(self, app_key: str, refresh_token: str) -> "BotRunner":
        bot = IdolMessageBot(
            app_key=app_key,
            sender=self._sender,
            state=self._state,
            poll_interval=self._poll_interval,
        )
        bot.set_refresh_token(refresh_token)
        self._bots.append(bot)
        logger.info("registered app: %s (%s)", APP_CONFIGS[app_key]["name"], app_key)
        return self

    def run(self) -> None:
        if not self._bots:
            raise RuntimeError("No apps registered.")

        self._sender.send_system_notification(
            "Bot started\n"
            f"apps: {', '.join(bot.app_name for bot in self._bots)}\n"
            f"poll interval: {self._poll_interval} seconds\n"
            f"{self._sender.describe_translation_mode()}"
        )

        logger.info("=== Bot started ===")
        while True:
            start_time = time.time()
            total_sent = 0

            for bot in self._bots:
                total_sent += bot.run_once()

            elapsed = time.time() - start_time
            logger.info(
                "Polling finished (sent=%d, elapsed=%.1fs, next=%ds)",
                total_sent,
                elapsed,
                self._poll_interval,
            )
            time.sleep(max(0, self._poll_interval - elapsed))
