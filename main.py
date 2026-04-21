"""
main.py
コントロールポイント
.env ファイルまたは環境変数で設定を管理する
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from bot import BotRunner
from state_manager import StateManager
from telegram_sender import TelegramSender

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


def main() -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")

    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))

    sender = TelegramSender(bot_token=bot_token, chat_id=chat_id)
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

    try:
        runner.run()
    except KeyboardInterrupt:
        logger.info("Bot を停止しました")
        sender.send_system_notification("Bot stopped.")
        state.close()


if __name__ == "__main__":
    main()
