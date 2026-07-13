"""Durable conversation and turn storage for the Agent 2.0 runtime."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    """Small SQLite store that separates durable dialogue from task execution."""

    def __init__(self, path: str | Path | None = None, *, history_turns: int = 6) -> None:
        root = Path(__file__).resolve().parents[2]
        self.path = Path(path) if path is not None else root / "data" / "conversations.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.history_turns = history_turns
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    turn_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    task_id TEXT NOT NULL UNIQUE,
                    user_message TEXT NOT NULL,
                    assistant_message TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_turns_conversation_created
                    ON conversation_turns(conversation_id, created_at);
                """
            )

    def create(
        self,
        *,
        user_id: str = "local-user",
        role: str = "operator",
        title: str = "新会话",
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        conversation_id = conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO conversations
                   (conversation_id, user_id, role, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (conversation_id, user_id, role, title.strip() or "新会话", now, now),
            )
        return self.get(conversation_id) or {}

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["turn_count"] = connection.execute(
                "SELECT COUNT(*) FROM conversation_turns WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            return result

    def list(self, *, user_id: str = "local-user", limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT c.*, COUNT(t.turn_id) AS turn_count
                   FROM conversations c
                   LEFT JOIN conversation_turns t ON t.conversation_id = c.conversation_id
                   WHERE c.user_id = ?
                   GROUP BY c.conversation_id
                   ORDER BY c.updated_at DESC
                   LIMIT ?""",
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def delete(self, conversation_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
            return cursor.rowcount > 0

    def start_turn(
        self,
        *,
        conversation_id: str,
        task_id: str,
        user_message: str,
    ) -> dict[str, Any]:
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        title = " ".join(user_message.strip().split())[:36] or "新会话"
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO conversation_turns
                   (turn_id, conversation_id, task_id, user_message, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'running', ?, ?)""",
                (turn_id, conversation_id, task_id, user_message, now, now),
            )
            connection.execute(
                """UPDATE conversations
                   SET title = CASE WHEN title = '新会话' THEN ? ELSE title END,
                       updated_at = ?
                   WHERE conversation_id = ?""",
                (title, now, conversation_id),
            )
        return self.turn_for_task(task_id) or {}

    def finish_turn(self, task_id: str, *, response: str, status: str) -> None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT conversation_id FROM conversation_turns WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """UPDATE conversation_turns
                   SET assistant_message = ?, status = ?, updated_at = ?
                   WHERE task_id = ?""",
                (response, status, now, task_id),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, row["conversation_id"]),
            )

    def turn_for_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_turns WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return dict(row) if row else None

    def turns(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM conversation_turns
                   WHERE conversation_id = ? ORDER BY created_at ASC""",
                (conversation_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def context(self, conversation_id: str, *, exclude_task_id: str = "") -> list[dict[str, str]]:
        """Return a bounded, completed dialogue window suitable for model input."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT user_message, assistant_message
                   FROM conversation_turns
                   WHERE conversation_id = ? AND task_id != ?
                     AND status IN ('succeeded', 'partial_success', 'responded', 'denied')
                     AND assistant_message != ''
                   ORDER BY created_at DESC LIMIT ?""",
                (conversation_id, exclude_task_id, self.history_turns),
            ).fetchall()
        history: list[dict[str, str]] = []
        for row in reversed(rows):
            history.append({"role": "user", "content": row["user_message"][:1200]})
            history.append({"role": "assistant", "content": row["assistant_message"][:1200]})
        return history
