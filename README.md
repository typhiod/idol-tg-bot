# idol-tg-bot

乃木坂46 / 櫻坂46 / 日向坂46 などのメッセージアプリの新着メッセージを  
自動的に Telegram Bot 経由でチャンネル・グループに転送するツールです。

colmsg (Rust版) の API 解析結果をベースに Python で再実装しています。

---

## アーキテクチャ

```
refresh_token (初回のみ手動取得)
    │
    ▼
POST /v2/update_token  ← TokenManager が自動管理・ローリング更新
    │
    ▼ access_token (3600秒有効)
    │
    ├─ GET /v2/groups           → 購読中メンバー一覧
    └─ GET /v2/groups/{id}/timeline → 新着メッセージ
                │
                ▼
        StateManager (SQLite)  ← 送信済みID管理・カーソル管理
                │
                ▼
        TelegramSender
        ├─ text   → sendMessage
        ├─ picture → sendPhoto / sendDocument
        ├─ video  → sendVideo
        ├─ voice  → sendAudio
        └─ link   → sendMessage
```

---

## セットアップ

### 1. refresh_token の取得

colmsg の公式ドキュメントを参照してください:  
https://github.com/proshunsuke/colmsg/blob/main/doc/how_to_get_refresh_token.md

要点:
1. アプリで外部サービス連携（Google アカウント推奨）を必ず行う
2. mitmproxy でアプリ通信を傍受する
3. アカウント引き継ぎ操作中に `POST /v2/signin` の Response から `refresh_token` を取得

### 2. Telegram Bot の準備

1. [@BotFather](https://t.me/botfather) で Bot を作成し、`TELEGRAM_BOT_TOKEN` を取得
2. 転送先のチャンネル/グループに Bot を管理者として追加
3. `chat_id` の取得方法:
   - チャンネルの場合: `@username` 形式か、Bot を追加後に `https://api.telegram.org/bot<TOKEN>/getUpdates` で確認
   - グループの場合: 同様に getUpdates で確認（マイナス始まりの数字）

### 3. 環境設定

```bash
cp .env.example .env
# .env を編集して各値を入力
```

```env
TELEGRAM_BOT_TOKEN=<your_telegram_bot_token>
TELEGRAM_CHAT_ID=<your_telegram_chat_id>
POLL_INTERVAL_SECONDS=300

# 使用するアプリの refresh_token のみ設定
REFRESH_TOKEN_NOGIZAKA=<your_nogizaka_refresh_token>
REFRESH_TOKEN_HINATAZAKA=<your_hinatazaka_refresh_token>
```

---

## 起動方法

### Python 直接実行

```bash
pip install -r requirements.txt
python main.py
```

### Docker (推奨)

```bash
mkdir -p data
docker-compose up -d

# ログ確認
docker-compose logs -f
```

---

## ファイル構成

```
idol-tg-bot/
├── main.py            # エントリーポイント・設定読み込み
├── bot.py             # ポーリングループ・BotRunner
├── api_client.py      # メッセージアプリ API クライアント
├── token_manager.py   # refresh/access token ライフサイクル管理
├── state_manager.py   # 送信済みID・カーソル管理 (SQLite)
├── telegram_sender.py # Telegram Bot API ラッパー
├── requirements.txt
├── .env.example
├── Dockerfile
└── docker-compose.yml
```

生成されるデータファイル:
```
tokens/
└── nogizaka_tokens.json   # app_key ごとの token ファイル
state.db                   # 送信済みID・カーソル (SQLite)
bot.log                    # ログファイル
```

---

## メッセージ表示例

```
🖼 山下美月 (乃木坂46)
📅 2024-01-15 12:34:56

[画像が送信されます]
```

```
💬 小坂菜緒 (日向坂46)
📅 2024-01-15 13:00:00

今日もがんばります！
```

---

## 注意事項

- アプリの利用規約 第8条（禁止事項）に自動化アクセスの禁止が含まれています。自己責任でご利用ください
- refresh_token は機密情報です。`.env` ファイルを Git にコミットしないよう注意してください（`.gitignore` に追加推奨）
- access_token は3600秒（1時間）で期限切れになりますが、TokenManager が自動的に更新します
- 動画ファイルが 50MB を超える場合は Telegram に直接送信できないため、テキスト通知のみになります

---

## トラブルシューティング

**「refresh_token が未設定です」エラー**  
→ `.env` の `REFRESH_TOKEN_*` を確認してください

**「401 Unauthorized」エラーが頻発する**  
→ refresh_token が失効している可能性があります。再取得してください

**動画が送信されない**  
→ Telegram Bot API の上限(50MB)を超えている可能性があります。ログを確認してください

**メッセージが重複して届く**  
→ `state.db` が正しく保存されているか確認してください
