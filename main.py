"""
main.py
コントロールポイント
.env ファイルまたは環境変数で設定を管理する
"""

import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from bot import BotRunner
from state_manager import StateManager
from telegram_sender import TelegramSender
from translator import GeminiTranslator

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(DATA_DIR / "bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def _require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        logger.error("環境変数 %s が設定されていません。.env ファイルを確認してください。", key)
        sys.exit(1)
    return val


def _get_bool_env(key: str, default: bool = False) -> bool:
    raw_value = os.getenv(key)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _handle_shutdown(signum, frame):
    raise KeyboardInterrupt


def main() -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")

    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
    translation_enabled = _get_bool_env("ENABLE_GEMINI_TRANSLATION", False)
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = (
        os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview").strip()
        or "gemini-3.1-flash-lite-preview"
    )
    gemini_timeout_seconds = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))

    translator = None
    if translation_enabled:
        if gemini_api_key:
            translator = GeminiTranslator(
                api_key=gemini_api_key,
                model=gemini_model,
                timeout_seconds=gemini_timeout_seconds,
                enabled=True,
            )
        else:
            logger.warning(
                "ENABLE_GEMINI_TRANSLATION is true but GEMINI_API_KEY is missing. Translation will stay disabled."
            )

    sender = TelegramSender(
        bot_token=bot_token,
        chat_id=chat_id,
        translator=translator,
    )
    state = StateManager()
    runner = BotRunner(
        sender=sender,
        state=state,
        poll_interval=poll_interval,
    )

    app_env_map = {
        "sakurazaka": "REFRESH_TOKEN_SAKURAZAKA",
        "hinatazaka": "REFRESH_TOKEN_HINATAZAKA",
        "nogizaka": "REFRESH_TOKEN_NOGIZAKA",
        "asukasaito": "REFRESH_TOKEN_ASUKASAITO",
        "maishiraishi": "REFRESH_TOKEN_MAISHIRAISHI",
        "yodel": "REFRESH_TOKEN_YODEL",
    }

    registered = False
    for app_key, env_key in app_env_map.items():
        token = os.getenv(env_key)
        if token and token.strip():
            runner.add_app(app_key, token.strip())
            registered = True

    if not registered:
        logger.error(
            "refresh_token が1つも設定されていません。\n"
            ".env ファイルに以下のいずれかを設定してください:\n"
            + "\n".join(f"  {env_key}=<your_refresh_token>" for env_key in app_env_map.values())
        )
        sys.exit(1)

    signal.signal(signal.SIGTERM, _handle_shutdown)
    try:
        runner.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped")
        sender.send_system_notification("Bot stopped.")
        state.close()


if __name__ == "__main__":
    main()
