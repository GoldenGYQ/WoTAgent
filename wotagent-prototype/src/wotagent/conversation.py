"""Durable conversation and turn storage for the Agent 2.0 runtime."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .conversation_state import (
    ConversationState,
    EntityMemory,
    GoalMemory,
    VerifiedFactMemory,
)
from .paths import data_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    """Small SQLite store that separates durable dialogue from task execution."""

    def __init__(self, path: str | Path | None = None, *, history_turns: int = 6) -> None:
        self.path = Path(path) if path is not None else data_path("conversations.db")
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
                    pending_goal_json TEXT NOT NULL DEFAULT '',
                    state_json TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    turn_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    user_message TEXT NOT NULL,
                    assistant_message TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'operator',
                    result_json TEXT NOT NULL DEFAULT '',
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
            self._migrate_task_turns(connection)
            self._ensure_column(
                connection,
                "conversations",
                "pending_goal_json",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "conversations",
                "state_json",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "conversation_turns",
                "role",
                "TEXT NOT NULL DEFAULT 'operator'",
            )
            self._ensure_column(
                connection,
                "conversation_turns",
                "result_json",
                "TEXT NOT NULL DEFAULT ''",
            )

    @staticmethod
    def _migrate_task_turns(connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(conversation_turns)"
            ).fetchall()
        }
        if "task_id" not in columns or "run_id" in columns:
            return
        connection.executescript(
            """
            ALTER TABLE conversation_turns RENAME TO conversation_turns_legacy;
            CREATE TABLE conversation_turns (
                turn_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                run_id TEXT NOT NULL UNIQUE,
                user_message TEXT NOT NULL,
                assistant_message TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'operator',
                result_json TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE
            );
            INSERT INTO conversation_turns (
                turn_id, conversation_id, run_id, user_message,
                assistant_message, role, result_json, status,
                created_at, updated_at
            )
            SELECT
                turn_id, conversation_id, task_id, user_message,
                assistant_message, role, result_json, status,
                created_at, updated_at
            FROM conversation_turns_legacy;
            DROP TABLE conversation_turns_legacy;
            CREATE INDEX IF NOT EXISTS idx_turns_conversation_created
                ON conversation_turns(conversation_id, created_at);
            """
        )
    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

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
        run_id: str,
        user_message: str,
        role: str = "operator",
    ) -> dict[str, Any]:
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        title = " ".join(user_message.strip().split())[:36] or "新会话"
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO conversation_turns
                   (turn_id, conversation_id, run_id, user_message, role, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
                (turn_id, conversation_id, run_id, user_message, role, now, now),
            )
            connection.execute(
                """UPDATE conversations
                   SET title = CASE WHEN title = '新会话' THEN ? ELSE title END,
                       role = ?,
                       updated_at = ?
                   WHERE conversation_id = ?""",
                (title, role, now, conversation_id),
            )
        return self.turn_for_run(run_id) or {}

    def finish_turn(
        self,
        run_id: str,
        *,
        response: str,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        now = _utc_now()
        result_json = json.dumps(result or {}, ensure_ascii=False)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT conversation_id, user_message, role
                   FROM conversation_turns WHERE run_id = ?""",
                (run_id,),
            ).fetchone()
            if row is None:
                return
            conversation = connection.execute(
                """SELECT pending_goal_json, state_json FROM conversations
                   WHERE conversation_id = ?""",
                (row["conversation_id"],),
            ).fetchone()
            existing_pending = self._load_json(
                conversation["pending_goal_json"] if conversation is not None else ""
            )
            existing_state = ConversationState.model_validate(
                self._load_json(
                    conversation["state_json"]
                    if conversation is not None else ""
                )
            )
            connection.execute(
                """UPDATE conversation_turns
                   SET assistant_message = ?, status = ?, result_json = ?, updated_at = ?
                   WHERE run_id = ?""",
                (response, status, result_json, now, run_id),
            )
            pending_goal = self._pending_goal(
                run_id=run_id,
                user_message=row["user_message"],
                role=row["role"],
                status=status,
                result=result or {},
            )
            if pending_goal is not None:
                connection.execute(
                    """UPDATE conversations SET pending_goal_json = ?
                       WHERE conversation_id = ?""",
                    (json.dumps(pending_goal, ensure_ascii=False), row["conversation_id"]),
                )
            elif self._resolves_pending_goal(existing_pending, result or {}, status):
                connection.execute(
                    """UPDATE conversations SET pending_goal_json = ''
                       WHERE conversation_id = ?""",
                    (row["conversation_id"],),
                )
            state = self._project_state(
                existing_state,
                run_id=run_id,
                user_message=row["user_message"],
                status=status,
                result=result or {},
            )
            connection.execute(
                """UPDATE conversations SET state_json = ?
                   WHERE conversation_id = ?""",
                (
                    state.model_dump_json(),
                    row["conversation_id"],
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, row["conversation_id"]),
            )

    def turn_for_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_turns WHERE run_id = ?",
                (run_id,),
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

    def context(
        self,
        conversation_id: str,
        *,
        exclude_run_id: str = "",
    ) -> list[dict[str, str]]:
        """Return a bounded, completed dialogue window suitable for model input."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT user_message, assistant_message
                   FROM conversation_turns
                   WHERE conversation_id = ? AND run_id != ?
                     AND status IN (
                         'completed', 'partial_success', 'succeeded',
                         'needs_more_information',
                         'responded', 'denied'
                     )
                     AND assistant_message != ''
                   ORDER BY created_at DESC LIMIT ?""",
                (conversation_id, exclude_run_id, self.history_turns),
            ).fetchall()
        history: list[dict[str, str]] = []
        for row in reversed(rows):
            history.append({"role": "user", "content": row["user_message"][:1200]})
            history.append({"role": "assistant", "content": row["assistant_message"][:1200]})
        return history

    def runtime_context(
        self,
        conversation_id: str,
        *,
        current_role: str,
        exclude_run_id: str = "",
    ) -> dict[str, Any]:
        """Return structured facts that should not be inferred from chat prose."""
        with self._lock, self._connect() as connection:
            conversation = connection.execute(
                """SELECT pending_goal_json, state_json FROM conversations
                   WHERE conversation_id = ?""",
                (conversation_id,),
            ).fetchone()
            latest = connection.execute(
                """SELECT run_id, user_message, role, status, result_json
                   FROM conversation_turns
                   WHERE conversation_id = ? AND run_id != ? AND result_json != ''
                   ORDER BY created_at DESC LIMIT 1""",
                (conversation_id, exclude_run_id),
            ).fetchone()

        pending_goal = self._load_json(
            conversation["pending_goal_json"] if conversation is not None else ""
        )
        state = ConversationState.model_validate(self._load_json(
            conversation["state_json"] if conversation is not None else ""
        ))
        if not pending_goal and state.active_goal is not None:
            pending_goal = state.active_goal.model_dump(mode="json")
        last_run: dict[str, Any] | None = None
        if latest is not None:
            result = self._load_json(latest["result_json"])
            last_run = {
                "run_id": latest["run_id"],
                "user_message": latest["user_message"],
                "role": latest["role"],
                "status": latest["status"],
                "plan": result.get("plan"),
                "policy_decisions": self._policy_decisions(result),
            }
        return {
            "current_role": current_role,
            "pending_goal": pending_goal or None,
            "last_run": last_run,
            "conversation_state": state.model_dump(mode="json"),
        }

    @staticmethod
    def _project_state(
        state: ConversationState,
        *,
        run_id: str,
        user_message: str,
        status: str,
        result: dict[str, Any],
    ) -> ConversationState:
        tasks = [
            item for item in result.get("tasks", [])
            if isinstance(item, dict)
        ]
        entities = list(state.entities)
        facts = list(state.verified_facts)
        target_ids: list[str] = []
        capabilities: list[str] = []
        for task in tasks:
            target = task.get("target")
            if isinstance(target, dict) and target.get("type") and target.get("id"):
                target_id = str(target["id"])
                target_ids.append(target_id)
                entity = EntityMemory(
                    type=str(target["type"]),
                    id=target_id,
                    display_name=(
                        str(target["display_name"])
                        if target.get("display_name") else None
                    ),
                    source_run_id=run_id,
                )
                entities = [
                    item for item in entities
                    if (item.type, item.id) != (entity.type, entity.id)
                ] + [entity]
            capability = str(task.get("requested_capability", "")).strip()
            if capability:
                capabilities.append(capability)
            finding = task.get("finding")
            if not isinstance(finding, dict):
                continue
            for call in finding.get("tool_calls", []):
                if not isinstance(call, dict) or call.get("verified") is not True:
                    continue
                arguments = call.get("arguments")
                resource_id = ""
                if isinstance(arguments, dict):
                    resource_id = str(
                        arguments.get("device_id")
                        or arguments.get("resident_id")
                        or ""
                    )
                fact = VerifiedFactMemory(
                    resource_id=resource_id or str(call.get("tool_name", "")),
                    value=call.get("observed_state", call.get("result")),
                    source_run_id=run_id,
                    source_task_id=str(task.get("task_id", "")),
                )
                facts = [
                    item for item in facts
                    if item.resource_id != fact.resource_id
                ] + [fact]

        unresolved_statuses = {
            "failed",
            "partial_success",
            "waiting_confirmation",
            "needs_more_information",
        }
        active_goal = (
            GoalMemory(
                source_run_id=run_id,
                goal=str(result.get("root_goal") or user_message),
                status=status,
                target_ids=sorted(set(target_ids)),
                capabilities=sorted(set(capabilities)),
            )
            if status in unresolved_statuses
            else None
        )
        return ConversationState(
            active_goal=active_goal,
            entities=entities[-12:],
            verified_facts=facts[-12:],
            last_outcome={
                "run_id": run_id,
                "goal": str(result.get("root_goal") or user_message),
                "status": status,
                "response": str(result.get("final_response", ""))[:1200],
                "target_ids": sorted(set(target_ids)),
                "capabilities": sorted(set(capabilities)),
            },
        )

    @staticmethod
    def _load_json(value: str) -> dict[str, Any]:
        try:
            parsed = json.loads(value) if value else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _pending_goal(
        cls,
        *,
        run_id: str,
        user_message: str,
        role: str,
        status: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        plan = result.get("plan")
        if not isinstance(plan, dict) or plan.get("intent") not in {"control", "query"}:
            return None
        if status not in {
            "denied",
            "failed",
            "partial_success",
            "waiting_confirmation",
            "needs_more_information",
        }:
            return None
        return {
            "source_run_id": run_id,
            "user_message": user_message,
            "role": role,
            "status": status,
            "plan": plan,
            "policy_decisions": cls._policy_decisions(result),
        }

    @staticmethod
    def _resolves_pending_goal(
        pending: dict[str, Any],
        result: dict[str, Any],
        status: str,
    ) -> bool:
        if status not in {"completed", "succeeded"}:
            return False
        pending_plan = pending.get("plan")
        completed_plan = result.get("plan")
        if not isinstance(pending_plan, dict) or not isinstance(completed_plan, dict):
            return False

        def signature(plan: dict[str, Any]) -> list[tuple[str, str, str, str]]:
            return sorted(
                (
                    str(step.get("kind", "")),
                    str(step.get("device_id", "")),
                    str(step.get("action", "")),
                    json.dumps(step.get("input", {}), ensure_ascii=False, sort_keys=True),
                )
                for step in plan.get("steps", [])
                if isinstance(step, dict)
            )

        expected = signature(pending_plan)
        return bool(expected) and expected == signature(completed_plan)

    @staticmethod
    def _policy_decisions(result: dict[str, Any]) -> list[dict[str, Any]]:
        trace = result.get("trace")
        if isinstance(trace, dict):
            decisions = [
                step.get("policy")
                for step in trace.get("steps", [])
                if isinstance(step, dict) and isinstance(step.get("policy"), dict)
            ]
            if decisions:
                return decisions
        decisions = result.get("policy_decisions", [])
        return decisions if isinstance(decisions, list) else []
