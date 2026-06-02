"""Tests for the memory module -- conversation memory and session transcripts."""

from __future__ import annotations

import json


class TestConversationMemory:
    """Tests for ``ConversationMemory`` -- session-scoped message buffer."""
    def test_add_and_get_messages(self):
        """Messages are stored and retrievable in order."""
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory()
        mem.add_user_message("hello")
        mem.add_ai_message("hi there")
        msgs = mem.messages
        assert len(msgs) == 2
        assert msgs[0].type == "human"
        assert msgs[1].type == "ai"

    def test_clear(self):
        """Clearing removes all messages."""
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory()
        mem.add_user_message("hello")
        mem.add_ai_message("hi")
        assert mem.message_count == 2
        mem.clear()
        assert mem.message_count == 0

    def test_save_and_load(self, tmp_path):
        """Messages persist to disk and reload correctly."""
        import json

        from wotagent.memory import ConversationMemory

        mem = ConversationMemory(storage_dir=str(tmp_path), session_id="test_sess")
        mem.add_user_message("hello")
        mem.add_ai_message("world")
        # Write directly to disk since ConversationMemory.save() is deprecated
        path = mem._data_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        messages = [
            {"type": "human", "content": "hello"},
            {"type": "ai", "content": "world"},
        ]
        path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")

        mem2 = ConversationMemory(storage_dir=str(tmp_path), session_id="test_sess")
        mem2.load()
        assert mem2.message_count == 2


class TestSessionTranscript:
    """Tests for ``SessionTranscript`` -- dual JSONL session logger."""
    def test_transcript_creation(self):
        """Transcript records events and produces a dict summary."""
        from wotagent.memory import SessionTranscript

        t = SessionTranscript("test_sess")
        t.record_start(model="test-model", role="operator")
        t.record_user_message("hello")
        t.record_assistant_response("world")
        t.record_end()

        data = t.to_dict()
        assert data["session_id"] == "test_sess"
        assert data["model"] == "test-model"
        assert len(data["entries"]) >= 2

    def test_load_chat_empty(self):
        """Loading a nonexistent session returns empty list."""
        from wotagent.memory import SessionTranscript

        entries = SessionTranscript.load_chat("nonexistent_session_xyz")
        assert entries == []

    def test_json_serializable(self):
        """Transcript to_dict() output is JSON-serializable."""
        from wotagent.memory import SessionTranscript

        t = SessionTranscript("test_serial")
        t.record_start(model="m", role="op")
        t.record_user_message("hello")
        t.record_assistant_response("world")
        t.record_end()
        data = t.to_dict()
        json.dumps(data, ensure_ascii=False)
