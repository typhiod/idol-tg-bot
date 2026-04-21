"""
state_manager.py
Persist sent message ids and per-member timeline cursors with SQLite.
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("data/state.db")


class StateManager:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_messages (
                app_key     TEXT NOT NULL,
                message_id  INTEGER NOT NULL,
                sent_at     TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (app_key, message_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS member_cursors (
                app_key           TEXT NOT NULL,
                group_id          INTEGER NOT NULL,
                last_updated_from TEXT NOT NULL DEFAULT '2000-01-01T00:00:00Z',
                PRIMARY KEY (app_key, group_id)
            )
            """
        )
        self._conn.commit()

    def is_sent(self, app_key: str, message_id: int) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM sent_messages WHERE app_key=? AND message_id=?",
            (app_key, message_id),
        )
        return cursor.fetchone() is not None

    def mark_sent(self, app_key: str, message_id: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO sent_messages (app_key, message_id) VALUES (?, ?)",
            (app_key, message_id),
        )
        self._conn.commit()

    def get_cursor(self, app_key: str, group_id: int) -> str:
        cursor = self._conn.execute(
            "SELECT last_updated_from FROM member_cursors WHERE app_key=? AND group_id=?",
            (app_key, group_id),
        )
        row = cursor.fetchone()
        return row["last_updated_from"] if row else "2026-03-05T15:00:00Z"

    def set_cursor(self, app_key: str, group_id: int, updated_from: str) -> None:
        self._conn.execute(
            """
            INSERT INTO member_cursors (app_key, group_id, last_updated_from)
            VALUES (?, ?, ?)
            ON CONFLICT(app_key, group_id) DO UPDATE SET last_updated_from=excluded.last_updated_from
            """,
            (app_key, group_id, updated_from),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
