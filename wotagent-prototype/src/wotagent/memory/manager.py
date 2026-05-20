"""Session-scoped memory management.

LangChain 1.3.1 delegates memory to LangGraph's checkpointer & store.
This module provides a lightweight in-memory message buffer for session
history, which integrates with the ``astream_events`` invocation pattern.

Also provides ``SessionTranscript`` for dual JSONL logging (full + chat).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage


class ConversationMemory:
    """Windowed conversation message buffer, scoped to a session.

    Does NOT depend on ``langchain.memory`` (removed in LangChain 1.3+).
    Instead it keeps an in-message list and provides the same interface.
    The checkpointer in ``create_agent`` handles LangGraph-level persistence.
    """

    def __init__(
        self,
        session_id: str,
        window_size: int = 10,
        memory_key: str = "history",
    ) -> None:
        self.session_id = session_id
        self._memory_key = memory_key
        self._window_size = window_size
        self._messages: list[BaseMessage] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_user_message(self, content: str) -> None:
        self._messages.append(HumanMessage(content=content))
        self._trim()

    def add_ai_message(self, content: str) -> None:
        self._messages.append(AIMessage(content=content))
        self._trim()

    def add_system_message(self, content: str) -> None:
        self._messages.append(SystemMessage(content=content))

    def add_message(self, message: BaseMessage) -> None:
        self._messages.append(message)
        self._trim()

    def _trim(self) -> None:
        """Keep only the last N messages (system message always stays)."""
        if len(self._messages) <= self._window_size * 2 + 1:
            return
        # Keep system message if present, then last window_size exchanges
        if isinstance(self._messages[0], SystemMessage):
            self._messages = (
                [self._messages[0]] + self._messages[-(self._window_size * 2):]
            )
        else:
            self._messages = self._messages[-(self._window_size * 2):]

    @property
    def messages(self) -> list[BaseMessage]:
        return list(self._messages)

    def truncate(self, to_index: int) -> None:
        """Keep messages 0..to_index (inclusive), discarding the rest."""
        if to_index < 0:
            to_index = len(self._messages) + to_index
        to_index = max(0, min(to_index, len(self._messages) - 1))
        self._messages = self._messages[:to_index + 1]
        self.save()

    def clear(self) -> None:
        self._messages.clear()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist messages to ``data/memory/{session_id}.json``."""
        path = self._data_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            messages = []
            for m in self._messages:
                t = "human" if isinstance(m, HumanMessage) else "ai" if isinstance(m, AIMessage) else "system"
                messages.append({"type": t, "content": m.content})
            path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logging.getLogger(__name__).exception("Failed to save memory for %s", self.session_id)

    def load(self) -> None:
        """Load messages from ``data/memory/{session_id}.json`` (if exists)."""
        path = self._data_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._messages = []
            for item in data:
                t = item["type"]
                if t == "human":
                    self._messages.append(HumanMessage(content=item["content"]))
                elif t == "ai":
                    self._messages.append(AIMessage(content=item["content"]))
                elif t == "system":
                    self._messages.append(SystemMessage(content=item["content"]))
        except Exception:
            logging.getLogger(__name__).exception("Failed to load memory for %s, starting fresh", self.session_id)
            self._messages = []

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    def _data_path(self) -> Path:
        return self._project_root() / "data" / "memory" / f"{self.session_id}.json"

    def load_memory_variables(self, **kwargs: Any) -> dict[str, Any]:
        return {self._memory_key: self.messages}

    @property
    def message_count(self) -> int:
        return len(self._messages)


# ---------------------------------------------------------------------------
# SessionTranscript — dual JSONL logger
# ---------------------------------------------------------------------------


class SessionTranscript:
    """Records session transcripts to JSONL files.

    Two files per session in ``data/memory/``:

    - ``{session_id}.full.jsonl`` — every pipeline event (plan, tool calls,
      model I/O, timings).  One JSON object per line, append-only.
    - ``{session_id}.chat.jsonl`` — simple user / assistant turns only.

    Usage::

        transcript = SessionTranscript("sess_xxx")
        transcript.record("user_message", content="开灯")
        transcript.record("plan", intent="control", rationale="...", steps=[])
        transcript.record("tool_call", tool="control_device", input={...}, output={...})
        transcript.record("assistant_response", content="已打开客厅灯")
        transcript.save()
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._full_buffer: list[dict[str, Any]] = []
        self._chat_buffer: list[dict[str, Any]] = []
        self._start = time.time()

    # ── Events ──────────────────────────────────────────────────────────

    def record(self, event: str, **kwargs: Any) -> None:
        """Append a structured event to the in-memory buffer.

        Call ``save()`` to flush to disk.
        """
        entry: dict[str, Any] = {"event": event, "ts": time.time()}
        entry.update(kwargs)
        self._full_buffer.append(entry)

        # Maintain chat transcript
        if event == "user_message":
            self._chat_buffer.append({
                "role": "user",
                "content": kwargs.get("content", ""),
                "ts": entry["ts"],
            })
        elif event == "assistant_response":
            self._chat_buffer.append({
                "role": "assistant",
                "content": kwargs.get("content", ""),
                "ts": entry["ts"],
            })

    # ── Session metadata ────────────────────────────────────────────────

    def record_start(self, **metadata: Any) -> None:
        """Record a session-start marker (model, role, etc.)."""
        self.record("session_start", **metadata)

    def record_end(self, **metadata: Any) -> None:
        """Record a session-end marker with duration."""
        self.record("session_end", duration=time.time() - self._start, **metadata)

    # ── Convenience helpers ─────────────────────────────────────────────

    def record_system_prompt(
        self,
        agent_name: str,
        template: str,
        resolved: str,
        **extra: Any,
    ) -> None:
        self.record("system_prompt", agent=agent_name, template=template,
                     resolved=resolved, **extra)

    def record_user_message(self, content: str) -> None:
        self.record("user_message", content=content)

    def record_plan(self, intent: str, rationale: str, steps: list[dict]) -> None:
        self.record("plan", intent=intent, rationale=rationale, steps=steps)

    def record_tool_call(self, tool: str, input: Any, output: Any) -> None:
        self.record("tool_call", tool=tool, input=input, output=output)

    def record_llm_input(self, agent: str, messages: list[dict]) -> None:
        """Record the full message list sent to the LLM (for fine-tuning)."""
        self.record("llm_input", agent=agent, messages=messages)

    def record_assistant_response(self, content: str) -> None:
        self.record("assistant_response", content=content)

    def record_error(self, error: str) -> None:
        self.record("error", content=error)

    # ── Persistence ─────────────────────────────────────────────────────

    @staticmethod
    def _sanitize(obj: Any) -> Any:
        """Recursively convert non-serializable objects to strings."""
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, dict):
            return {k: SessionTranscript._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [SessionTranscript._sanitize(v) for v in obj]
        # LangChain message objects and anything else → string repr
        try:
            s = str(obj)
            return s if s else repr(obj)
        except Exception:
            return repr(obj)

    def _json_dumps(self, obj: dict[str, Any]) -> str:
        """JSON serialization with automatic object sanitization."""
        return json.dumps(self._sanitize(obj), ensure_ascii=False)

    def save(self) -> None:
        """Append buffered events to both JSONL files."""
        base_path = self._data_path()
        base_path.parent.mkdir(parents=True, exist_ok=True)

        if self._full_buffer:
            full_path = base_path.with_suffix(".full.jsonl")
            with open(full_path, "a", encoding="utf-8") as f:
                for evt in self._full_buffer:
                    f.write(self._json_dumps(evt) + "\n")
            self._full_buffer.clear()

        if self._chat_buffer:
            chat_path = base_path.with_suffix(".chat.jsonl")
            with open(chat_path, "a", encoding="utf-8") as f:
                for evt in self._chat_buffer:
                    f.write(self._json_dumps(evt) + "\n")
            self._chat_buffer.clear()

    @staticmethod
    def load_chat(session_id: str) -> list[dict[str, Any]]:
        """Load chat transcript from ``data/memory/{session_id}.chat.jsonl``."""
        path = SessionTranscript._project_root() / "data" / "memory" / f"{session_id}.chat.jsonl"
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            text = path.read_text(encoding="utf-8")
            for line in text.strip().split("\n"):
                if not line.strip():
                    continue
                entries.append(json.loads(line))
        except Exception:
            logging.getLogger(__name__).exception("Failed to load chat transcript for %s", session_id)
        return entries

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    def _data_path(self) -> Path:
        return self._project_root() / "data" / "memory" / self.session_id
