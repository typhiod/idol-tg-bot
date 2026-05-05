# idol-tg-bot

A Telegram bot that forwards messages from Sakamichi Series (Nogizaka46, Sakurazaka46, Hinatazaka46) mobile apps to a Telegram channel/group, with optional Japanese-to-Chinese translation via Gemini.

Based on the [colmsg](https://github.com/proshunsuke/colmsg) API analysis, re-implemented in Python.

## Architecture

```
refresh_token (manual, one-time)
    │
    ▼
POST /v2/update_token  ← TokenManager handles rolling refresh
    │
    ▼ access_token (valid 3600s)
    │
    ├─ GET /v2/groups            → subscribed members
    └─ GET /v2/groups/{id}/timeline → new messages
                │
                ▼
        StateManager (SQLite)  ← sent IDs & cursor tracking
                │
                ▼
        TelegramSender
        │
        ├─ text/link → GeminiTranslator (JP → ZH) → sendMessage
        ├─ picture   → sendPhoto / sendDocument
        ├─ video     → sendVideo
        └─ voice     → sendAudio
```

## Features

- Polls multiple idol groups and forwards new messages to Telegram
- Automatic token refresh (access tokens expire every hour)
- Per-member timeline cursor stored in SQLite — no duplicate messages on restart
- **Gemini translation**: Japanese → Chinese for text and link messages, keeping original text alongside the translation
- **Idol-aware glossary**: member names, honorifics (さん→桑), nicknames preserved in hiragana
- **Rate limiting**: enforces minimum interval between Gemini API calls to stay within free-tier RPM limits
- Media attachments: photos, videos, and voice messages forwarded directly

## Setup

### 1. Get refresh tokens

Follow the colmsg guide: https://github.com/proshunsuke/colmsg/blob/main/doc/how_to_get_refresh_token.md

TL;DR:
1. Link your app account to an external service (Google recommended)
2. Use mitmproxy to intercept app traffic
3. Extract `refresh_token` from the `POST /v2/signin` response during account transfer

### 2. Create a Telegram bot

1. Create a bot via [@BotFather](https://t.me/botfather) and get the `TELEGRAM_BOT_TOKEN`
2. Add the bot as an **admin** to your target channel/group
3. Find the `chat_id`:
   - Channel: use `@username` or check `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Group: same approach (chat_id will be a negative number)

### 3. Configure environment

```bash
cp .env.example .env
# edit .env with your values
```

```env
# Telegram
TELEGRAM_BOT_TOKEN=<your_telegram_bot_token>
TELEGRAM_CHAT_ID=<your_telegram_chat_id>
POLL_INTERVAL_SECONDS=15

# Gemini translation (optional)
ENABLE_GEMINI_TRANSLATION=false
GEMINI_API_KEY=<your_gemini_api_key>
GEMINI_MODEL=gemini-3.1-flash-lite-preview
GEMINI_TIMEOUT_SECONDS=30

# Refresh tokens — fill only the groups you want to follow
REFRESH_TOKEN_NOGIZAKA=<your_nogizaka_refresh_token>
REFRESH_TOKEN_SAKURAZAKA=<your_sakurazaka_refresh_token>
REFRESH_TOKEN_HINATAZAKA=<your_hinatazaka_refresh_token>
```

## Running

### Docker (recommended)

```bash
mkdir -p data
docker compose up -d --build

# View logs
docker compose logs -f --tail 50
```

### Bare Python

```bash
pip install -r requirements.txt
python main.py
```

## Files

```
idol-tg-bot/
├── main.py              # Entry point, config loading
├── bot.py               # Polling loop, BotRunner
├── api_client.py        # Message app API client
├── token_manager.py     # Refresh/access token lifecycle
├── state_manager.py     # Sent-message IDs & cursors (SQLite)
├── telegram_sender.py   # Telegram Bot API wrapper, translation wiring
├── translator.py        # Gemini API client with rate limiting
├── member_glossary.py   # Idol name mappings & translation rules
├── requirements.txt
├── .env.example
├── Dockerfile
└── docker-compose.yml
```

Generated at runtime:
```
data/
├── tokens/              # token JSON files per app
├── state.db             # sent IDs & cursors (SQLite)
└── bot.log              # log output
```

## Translation

When `ENABLE_GEMINI_TRANSLATION=true` and a valid `GEMINI_API_KEY` is set, text and link messages are translated from Japanese to Chinese. The original Japanese text is preserved, with the Chinese translation appended below:

```
#菅原咲月 (乃木坂46)
2026-05-06 21:00:00

今日も一日お疲れ様でした！

今天也辛苦了！
```

A custom glossary (`member_glossary.py`) provides member name mappings and domain-specific terminology for all supported groups.

### Gemini quota

The free tier has an **RPD (Requests Per Day) limit** that resets at **midnight Pacific Time** (JST 4pm summer / 5pm winter). The bot enforces a minimum interval between API calls to stay within the free-tier RPM limit.

## Notes

- The app's Terms of Service (Article 8) prohibit automated access. **Use at your own risk.**
- Never commit `.env` or the `tokens/` directory — they are already in `.gitignore`.
- Access tokens expire every 3600 seconds; `TokenManager` refreshes them automatically.
- Videos over 50 MB cannot be sent directly via Telegram Bot API; a text notice is sent instead.
