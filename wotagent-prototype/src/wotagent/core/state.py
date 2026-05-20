"""Conversation state tracker — intent continuity across turns.

方案 A: Inject recent message pairs into intent classification context
方案 B: Detect follow-up messages and reuse active intent
"""

from __future__ import annotations

from typing import Any


# Question-ending patterns (Chinese + English)
_QUESTION_SUFFIXES = ("?", "？", "吗", "么", "吧", "呢")
_QUESTION_KW = ("哪个", "哪间", "几号", "什么", "啥", "who", "what", "which")


def _is_question(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if text.endswith("?") or text.endswith("？"):
        return True
    last = text[-1]
    if last in ("吗", "么", "吧"):
        return True
    if text.endswith("呢"):
        return True
    for w in _QUESTION_KW:
        if w in text:
            return True
    return False


class ConversationState:
    """Tracks conversational context across turns.

    Usage in pipeline::

        state = ConversationState()
        # Before intent classification:
        context_str = state.get_context_str()          # 方案A
        context_hint = state.get_continuation_hint()   # 方案B
        # After turn:
        state.record_turn(user_msg, agent_msg, intent)
    """

    def __init__(self) -> None:
        self.intent: str | None = None  # Last non-chat intent
        self._exchanges: list[tuple[str, str]] = []
        self._last_agent_msg: str = ""
        self._agent_asked_question: bool = False
        self._last_user_msg: str = ""

    # ── Recording ───────────────────────────────────────────────────────

    def record_turn(
        self,
        user_msg: str,
        agent_msg: str,
        intent: str | None = None,
    ) -> None:
        """Record a completed turn (user + agent exchange)."""
        self._exchanges.append((user_msg, agent_msg))
        if len(self._exchanges) > 5:
            self._exchanges = self._exchanges[-5:]
        self._last_user_msg = user_msg
        self._last_agent_msg = agent_msg
        self._agent_asked_question = _is_question(agent_msg)

        # Track non-chat intents
        if intent and intent != "chat":
            self.intent = intent
        # If agent completed the intent or went back to chat, reset
        if intent == "chat" and not self._agent_asked_question:
            self.intent = None

    # ── 方案A: Context for intent classifier ────────────────────────────

    def get_context_str(self) -> str:
        """Return formatted context from recent exchanges."""
        if not self._exchanges:
            return ""
        parts: list[str] = []
        for user_msg, agent_msg in self._exchanges[-2:]:
            parts.append(f"用户：{user_msg[:100]}")
            parts.append(f"助手：{agent_msg[:200]}")
        return "\n".join(parts)

    # ── 方案B: Intent continuation detection ────────────────────────────

    def get_continuation_hint(self) -> str:
        """Return a hint for the classifier if the current message is likely
        a follow-up to a previous intent."""
        if self.intent is None:
            return ""
        if not self._agent_asked_question:
            return ""
        # The user's message is short and the agent asked a question —
        # likely a follow-up answer
        return (
            "⚠️ 提示：上一步的操作意图是「{intent}」，"
            "助手刚问了用户一个问题，现在用户在回答。"
            "如果用户在回答助手的问题，意图应延续上一步的「{intent}」，而不是chat。"
        ).format(intent=self.intent)

    def reset_intent(self) -> None:
        """Explicitly reset active intent."""
        self.intent = None
        self._agent_asked_question = False
