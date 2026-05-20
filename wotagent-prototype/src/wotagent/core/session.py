"""Session management — create, manage, and clean up agent sessions.

Each session holds:
- A LangChain agent instance (CompiledStateGraph)
- Conversation memory
- RBAC context (user role)
- Event cursor for SSE replay
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ..memory import ConversationMemory
from .state import ConversationState


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class SessionInfo(BaseModel):
    """Public information about a session."""
    session_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_role: str = "operator"
    message_count: int = 0
    last_active: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentSession:
    """An active agent session with memory and context."""

    def __init__(
        self,
        agent: Any,
        session_id: str | None = None,
        user_role: str = "operator",
        window_size: int = 10,
        timeout_minutes: int = 30,
    ) -> None:
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        self.agent = agent
        self.user_role = user_role
        self.memory = ConversationMemory(self.session_id, window_size=window_size)
        self.info = SessionInfo(
            session_id=self.session_id,
            user_role=user_role,
        )
        self.created_at = datetime.now(timezone.utc)
        self.last_active = self.created_at
        self.event_cursor = 0  # for SSE replay
        self.timeout_minutes = timeout_minutes
        self.state = ConversationState()  # intent continuity across turns

    def touch(self) -> None:
        self.last_active = datetime.now(timezone.utc)
        self.info.last_active = self.last_active
        self.info.message_count = len(self.memory.messages) // 2

    @property
    def is_expired(self) -> bool:
        """Check if session has been idle beyond the timeout."""
        elapsed = (datetime.now(timezone.utc) - self.last_active).total_seconds()
        return elapsed >= self.timeout_minutes * 60


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------

class SessionManager:
    """In-memory session registry with lifecycle management."""

    def __init__(self, timeout_minutes: int = 30) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._timeout_minutes = timeout_minutes

    def create(self, agent: Any, user_role: str = "operator", *, session_id: str | None = None) -> AgentSession:
        """Create a new session.

        Args:
            agent: LangChain agent instance.
            user_role: RBAC role.
            session_id: Optional explicit session ID. If omitted, a random ID is generated.
        """
        session = AgentSession(agent, session_id=session_id, user_role=user_role, timeout_minutes=self._timeout_minutes)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> AgentSession | None:
        """Get a session by ID, or None."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired:
            self.delete(session_id)
            return None
        session.touch()
        return session

    def get_or_create(self, agent: Any, session_id: str | None = None, user_role: str = "operator") -> AgentSession:
        """Get existing session or create new one."""
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            if not session.is_expired:
                session.touch()
                return session
            # Expired — remove and recreate
            self.delete(session_id)
        return self.create(agent, user_role)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def list_active(self) -> list[SessionInfo]:
        now = datetime.now(timezone.utc)
        active = []
        for sid, sess in self._sessions.items():
            if (now - sess.last_active).total_seconds() <= self._timeout_minutes * 60:
                sess.info.message_count = len(sess.memory.messages) // 2
                active.append(sess.info)
        return active

    def cleanup_expired(self) -> int:
        """Remove expired sessions. Returns count of removed sessions."""
        expired = [sid for sid, sess in self._sessions.items() if sess.is_expired(self._timeout_minutes)]
        for sid in expired:
            self.delete(sid)
        return len(expired)

    @property
    def count(self) -> int:
        return len(self._sessions)


# Singleton
_manager: SessionManager | None = None


def get_session_manager(timeout_minutes: int = 30) -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager(timeout_minutes=timeout_minutes)
    return _manager
