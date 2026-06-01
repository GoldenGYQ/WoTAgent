"""Tests for ConversationState -- cross-turn intent continuity."""

from __future__ import annotations


class TestConversationState:
    """Tests for ``ConversationState`` -- cross-turn intent continuity."""
    def test_initial_state(self):
        """Fresh state has empty context and no continuation hint."""
        from wotagent.core.state import ConversationState

        state = ConversationState()
        assert state.get_context_str() == ""
        assert state.get_continuation_hint() == ""

    def test_record_turn(self):
        """Recording a turn produces context with the exchange."""
        from wotagent.core.state import ConversationState

        state = ConversationState()
        state.record_turn("turn on the light", "OK, light turned on")
        ctx = state.get_context_str()
        assert "turn on the light" in ctx and "OK" in ctx

    def test_continuation_hint(self):
        """get_continuation_hint returns text when intent is set and agent asked a question."""
        from wotagent.core.state import ConversationState

        state = ConversationState()
        # Need to set intent (non-chat) and trigger agent_asked_question
        state.record_turn("Please turn on the AC", "Which room?", intent="control")
        hint = state.get_continuation_hint()
        assert hint  # Should not be empty when intent is active and agent asked a question

    def test_multiple_turns(self):
        """Only recent exchanges appear in context."""
        from wotagent.core.state import ConversationState

        state = ConversationState()
        state.record_turn("turn on the light", "done")
        state.record_turn("make it brighter", "brightness at 80%")
        state.record_turn("what about the AC", "AC is off")
        ctx = state.get_context_str()
        assert "make it brighter" in ctx
        assert "what about the AC" in ctx

    def test_question_detection(self):
        """Question-ending patterns and keywords are detected."""
        from wotagent.core.state import _is_question

        assert _is_question("what is the temperature?")
        assert _is_question("温度多少？")
        assert not _is_question("turn on the light")
        assert _is_question("does it work in the bedroom?")

    def test_reset_intent(self):
        """Explicit reset clears active intent."""
        from wotagent.core.state import ConversationState

        state = ConversationState()
        state.record_turn("hello", "hi", intent="control")
        assert state.intent == "control"
        state.reset_intent()
        assert state.intent is None

    def test_exchange_limit(self):
        """Should only keep the last 5 exchanges."""
        from wotagent.core.state import ConversationState

        state = ConversationState()
        for i in range(10):
            state.record_turn(f"msg_{i}", f"reply_{i}")
        # get_context_str returns last 2 exchanges only
        ctx = state.get_context_str()
        assert "msg_0" not in ctx  # too old
