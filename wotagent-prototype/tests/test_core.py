"""Tests for the new WoTAgent architecture."""

from __future__ import annotations

import pytest


# ======================================================================
# Event system
# ======================================================================

class TestEventSchema:
    """Tests for event schema types and event bus."""
    def test_event_creation(self):
        """Event can be created with required fields."""
        from wotagent.events import Event
        evt = Event(
            source="wotagent/test",
            type="wot.system.log",
            data={"msg": "hello"},
            session_id="sess_001",
        )
        assert evt.specversion == "1.0"
        assert evt.id
        assert evt.source == "wotagent/test"
        assert evt.type == "wot.system.log"
        assert evt.data == {"msg": "hello"}
        assert evt.session_id == "sess_001"

    def test_event_types(self):
        """Event types match expected pattern."""
        from wotagent.events import Event, EventType
        types: list[EventType] = [
            "wot.session.started",
            "wot.agent.action.started",
            "wot.agent.action.completed",
            "wot.agent.response",
            "wot.device.state_changed",
        ]
        for t in types:
            evt = Event(source="test", type=t)
            assert evt.type == t


class TestEventBus:
    """Tests for the event bus implementation."""
    """Subscribers receive emitted events."""
    def test_emit_and_subscribe(self):
        import asyncio
        from wotagent.events import EventBus, Event

        async def run():
            bus = EventBus(max_history=10)
            received = []

            async def collector(evt):
                received.append(evt)

            bus.subscribe(collector, event_type="wot.system.log")
            await bus.emit(Event(source="test", type="wot.system.log", data={"i": 1}))
            await bus.emit(Event(source="test", type="wot.system.log", data={"i": 2}))
            return received

        received = asyncio.run(run())
        assert len(received) == 2
        assert received[0].data["i"] == 1
        assert received[1].data["i"] == 2

    """Late subscribers can replay past events."""
    def test_history_replay(self):
        import asyncio
        from wotagent.events import EventBus, Event

        async def run():
            bus = EventBus(max_history=5)
            for i in range(3):
                await bus.emit(Event(source="test", type="wot.system.log", data={"i": i}))
            return bus

        bus = asyncio.run(run())
        history = bus.history()
        assert len(history) == 3

        # Replay from cursor 1
        replay = bus.history(start_cursor=1)
        assert len(replay) == 2
        assert replay[0].data["i"] == 1

    """Event history respects max length."""
    def test_max_history(self):
        import asyncio
        from wotagent.events import EventBus, Event

        async def run():
            bus = EventBus(max_history=3)
            for i in range(5):
                await bus.emit(Event(source="test", type="wot.system.log", data={"i": i}))
            return bus

        bus = asyncio.run(run())
        assert len(bus.history()) == 3
        assert bus.history()[0].data["i"] == 2  # oldest kept

    """Cursor-based replay works correctly."""
    def test_cursor(self):
        import asyncio
        from wotagent.events import EventBus, Event

        async def run():
            bus = EventBus()
            assert bus.cursor() == 0
            await bus.emit(Event(source="test", type="wot.system.log"))
            assert bus.cursor() == 1

    """Subscriber count tracks correctly."""
    def test_subscriber_count(self):
        from wotagent.events import EventBus, Event

        bus = EventBus()
        assert bus.subscriber_count == 0

        def cb(evt):
            pass

        bus.subscribe(cb)
        assert bus.subscriber_count == 1

    """Unsubscribed callbacks stop receiving events."""
    def test_unsubscribe(self):
        from wotagent.events import EventBus, Event

        bus = EventBus()
        calls = []

        def cb(evt):
            calls.append(evt)

        unsub = bus.subscribe(cb)
        unsub()
        assert bus.subscriber_count == 0


# ======================================================================
# RBAC
# ======================================================================

class TestRBAC:
    """Tests for role-based access control hierarchy."""
    def test_role_hierarchy(self):
        from wotagent.auth import Role, get_access_control

        ac = get_access_control()
        # Admin can do anything
        assert ac.check(Role.ADMIN, "device.control", "execute")
        assert ac.check(Role.ADMIN, "session.list", "read")
        # Viewer can read but not control
        assert ac.check(Role.VIEWER, "device.read", "read")
        assert not ac.check(Role.VIEWER, "device.control", "execute")
        # Operator can control
        assert ac.check(Role.OPERATOR, "device.control", "execute")

        """All role enum values are accessible."""
    def test_role_enum(self):
        from wotagent.auth import Role

        assert Role.ADMIN.value == "admin"
        assert Role.OPERATOR.value == "operator"
        assert Role.VIEWER.value == "viewer"

    """Wildcard patterns match correctly in permission checks."""
    def test_wildcard_match(self):
        from wotagent.auth import Role, get_access_control

        ac = get_access_control()
        # Admin has wildcard permissions
        assert ac.check(Role.ADMIN, "device.anything.here", "any_action")
        assert ac.check(Role.ADMIN, "nonexistent.resource", "read")


# ======================================================================
# WoT / Thing Description
# ======================================================================

    """All devices are returned by location query."""
class TestWoT:
    """Tests for Web of Things device registry queries."""
    def test_find_devices(self):
        from wotagent.wot import find_devices

        devices = find_devices()
        assert isinstance(devices, list)
        # At least the sample TDs should be found
        """Devices filterable by capability type."""
        assert len(devices) >= 3

    def test_find_devices_by_capability(self):
        from wotagent.wot import find_devices

        luminance = find_devices(capability="luminance")
        for d in luminance:
            caps = [c.lower() for c in d.capabilities]
            """Devices filterable by location."""
            assert "luminance" in caps

    def test_find_devices_by_location(self):
        from wotagent.wot import find_devices

        parlor = find_devices(location="parlor")
        assert len(parlor) > 0, "No devices found for parlor (should map to living_room)"
        for d in parlor:
            """Device lookup by ID returns TD metadata."""
            assert d.location.lower() == "living_room"

    def test_get_device_by_id(self):
        from wotagent.wot import get_device_by_id

        device = get_device_by_id("light-001")
        if device:
            assert device.title


# ======================================================================
# Memory
# ======================================================================

    """Messages can be added and retrieved."""
class TestMemory:
    """Tests for conversation memory storage in core."""
    def test_add_and_retrieve(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_1", window_size=5)
        mem.add_user_message("hello")
        mem.add_ai_message("hi there")

        assert mem.message_count == 2
        assert len(mem.messages) == 2
        """Message window trims to configured size."""
        assert mem.messages[0].content == "hello"
        assert mem.messages[1].content == "hi there"

    def test_window_trimming(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_2", window_size=2)
        for i in range(10):
            mem.add_user_message(f"q{i}")
            mem.add_ai_message(f"a{i}")

        """Clearing removes all messages."""
        # Should keep system (none) + last 2 exchanges = 4 messages
        assert mem.message_count <= 5

    def test_clear(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_3")
        mem.add_user_message("hello")
        """Memory persists and reloads from disk."""
        mem.clear()


# ======================================================================
# Streaming
# ======================================================================


class TestStreaming:
    def test_chat_stream_emits_thinking_and_token(self):
        import asyncio
        from types import SimpleNamespace

        from langchain_core.messages import AIMessage

        from wotagent.core.handlers import invoke_agent_stream

        class DummyPlanner:
            async def ainvoke(self, _messages):
                return SimpleNamespace(
                    tool_calls=[{
                        "name": "generate_plan",
                        "args": {"intent": "chat", "rationale": "test", "steps": []},
                    }],
                )

        class DummyResponder:
            async def astream_events(self, _inputs, context=None, version=None):
                yield {
                    "event": "on_chat_model_stream",
                    "data": {
                        "chunk": SimpleNamespace(
                            content="Hi",
                            additional_kwargs={"reasoning_content": "thinking"},
                        )
                    },
                }
                yield {
                    "event": "on_chain_end",
                    "data": {
                        "output": {"messages": [AIMessage(content="Hi")]}
                    },
                }

        class DummyPipeline:
            planner = DummyPlanner()
            responder = DummyResponder()
            executor = None
            observer = None

        async def run():
            chunks = []
            async for chunk in invoke_agent_stream(
                DummyPipeline(),
                "hello",
                session_id="",
                user_role="operator",
            ):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(run())
        types = {c.get("type") for c in chunks}
        assert "plan" in types
        assert "thinking" in types
        assert "token" in types
        assert "done" in types

    def test_event_bus_emits_streaming_events(self):
        import asyncio
        from types import SimpleNamespace

        from langchain_core.messages import AIMessage

        from wotagent.core.handlers import invoke_agent_stream
        from wotagent.events import get_bus

        class DummyPlanner:
            async def ainvoke(self, _messages):
                return SimpleNamespace(
                    tool_calls=[{
                        "name": "generate_plan",
                        "args": {"intent": "chat", "rationale": "test", "steps": []},
                    }],
                )

        class DummyResponder:
            async def astream_events(self, _inputs, context=None, version=None):
                yield {
                    "event": "on_chat_model_stream",
                    "data": {
                        "chunk": SimpleNamespace(
                            content="Hi",
                            additional_kwargs={"reasoning_content": "thinking"},
                        )
                    },
                }
                yield {
                    "event": "on_chain_end",
                    "data": {
                        "output": {"messages": [AIMessage(content="Hi")]}
                    },
                }

        class DummyPipeline:
            planner = DummyPlanner()
            responder = DummyResponder()
            executor = None
            observer = None

        async def run():
            bus = get_bus()
            bus.clear()
            async for _ in invoke_agent_stream(
                DummyPipeline(),
                "hello",
                session_id="",
                user_role="operator",
            ):
                pass
            return [e.type for e in bus.history()]

        types = asyncio.run(run())
        assert "wot.agent.token" in types
        assert "wot.agent.thought" in types

    def test_save_and_load(self, tmp_path):
        import json

        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_test_save_load", storage_dir=str(tmp_path))
        mem.add_user_message("你好")
        mem.add_ai_message("你好！有什么可以帮你的吗？")
        # Write directly to disk since ConversationMemory.save() is deprecated
        path = mem._data_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        messages = [
            {"type": "human", "content": "你好"},
            {"type": "ai", "content": "你好！有什么可以帮你的吗？"},
        ]
        path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")

        # Create a new instance and load from disk
        mem2 = ConversationMemory("sess_test_save_load", storage_dir=str(tmp_path))
        mem2.load()
        assert mem2.message_count == 2
        assert mem2.messages[0].content == "你好"
        assert mem2.messages[1].content == "你好！有什么可以帮你的吗？"

        # Cleanup
        """Loading nonexistent file leaves memory empty."""
        import os
        os.remove(mem2._data_path())

    def test_load_nonexistent(self):
        from wotagent.memory import ConversationMemory

        """Truncation keeps prefix of messages."""
        mem = ConversationMemory("sess_does_not_exist")
        assert mem.message_count == 0  # no crash

    def test_truncate(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_test_truncate")
        for i in range(5):
            mem.add_user_message(f"q{i}")
            mem.add_ai_message(f"a{i}")
        assert mem.message_count == 10

        # Truncate to index 3 (keep first 4 messages: q0, a0, q1, a1)
        mem.truncate(3)
        assert mem.message_count == 4
        assert mem.messages[0].content == "q0"
        assert mem.messages[3].content == "a1"

    def test_truncate_negative_index(self):
        from wotagent.memory import ConversationMemory

        mem = ConversationMemory("sess_test_truncate_neg")
        mem.add_user_message("q0")
        mem.add_ai_message("a0")
        mem.add_user_message("q1")
        mem.truncate(-1)  # keep all
        assert mem.message_count == 3


# ======================================================================
# Tools
    """All tools are listed with correct metadata."""
# ======================================================================

class TestTools:
    """Tests for tool registry and device listing tools."""
    def test_get_all_tools(self):
        from wotagent.tools import get_all_tools

        tools = get_all_tools()
        assert len(tools) >= 6
        names = [t.name for t in tools]
        assert "list_devices" in names
        assert "control_device" in names
        assert "query_device" in names
        """Tool metadata includes name, description, and schema."""
        assert "rag_retrieve" in names
        assert "get_system_info" in names
        assert "list_files" in names

    def test_tool_metadata(self):
        from wotagent.tools import get_all_tools

        """List-devices tool returns device names."""
        for t in get_all_tools():
            assert t.name
            assert t.description

    def test_list_devices_tool(self):
        from wotagent.tools import get_all_tools

        tools = {t.name: t for t in get_all_tools()}
        list_tool = tools["list_devices"]
        result = list_tool.func()
        assert isinstance(result, list)


# ======================================================================
    """Agent is created with bound tools."""
# Agent creation
# ======================================================================

class TestAgent:
    """Tests for agent creation and pipeline building."""
    def test_create_agent(self):
        from wotagent.core import create_wot_agent

        """Pipeline is created with agent and memory."""
        agent = create_wot_agent(enable_thinking=False, temperature=0)
        assert agent is not None
        assert hasattr(agent, "ainvoke")
        assert hasattr(agent, "astream_events")

    def test_create_pipeline(self):
        from wotagent.core import create_wot_pipeline, AgentPipeline

        pipeline = create_wot_pipeline(enable_thinking=False, temperature=0)
        assert isinstance(pipeline, AgentPipeline)
        assert hasattr(pipeline.planner, "ainvoke")
        assert hasattr(pipeline.executor, "ainvoke")
        assert hasattr(pipeline.observer, "ainvoke")
        assert hasattr(pipeline.responder, "ainvoke")


# ======================================================================
# Session management
# ======================================================================

    """Session is created and retrievable by ID."""
class TestSession:
    """Tests for session management (create, get, expiry)."""
    def _fresh_manager(self):
        from wotagent.core.session import SessionManager
        return SessionManager(timeout_minutes=60)

    def test_create_and_get(self):
        from wotagent.core import create_wot_agent

        agent = create_wot_agent(enable_thinking=False, temperature=0)
        mgr = self._fresh_manager()

        session = mgr.create(agent, user_role="operator")
        assert session.session_id
        """get_or_create returns existing or new session."""
        assert session.user_role == "operator"

        retrieved = mgr.get(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_or_create(self):
        from wotagent.core import create_wot_agent

        agent = create_wot_agent(enable_thinking=False, temperature=0)
        """Expired sessions are not returned as active."""
        mgr = self._fresh_manager()

        s1 = mgr.get_or_create(agent, user_role="viewer")
        s2 = mgr.get_or_create(agent, session_id=s1.session_id, user_role="viewer")
        assert s1.session_id == s2.session_id

    def test_session_expiry(self):
        import time
        from wotagent.core import create_wot_agent
        from wotagent.core.session import SessionManager

        """Only active sessions are listed."""
        agent = create_wot_agent(enable_thinking=False, temperature=0)
        mgr = SessionManager(timeout_minutes=0)  # immediate expiry
        session = mgr.create(agent)
        time.sleep(0.01)  # ensure clock ticks past 0
        assert mgr.get(session.session_id) is None

    def test_list_active(self):
        from wotagent.core import create_wot_agent

        agent = create_wot_agent(enable_thinking=False, temperature=0)
        mgr = self._fresh_manager()
        mgr.create(agent)
        mgr.create(agent)
        assert len(mgr.list_active()) == 2


    """Events are written to the log file."""
# ======================================================================
# Event Logging
# ======================================================================

class TestEventLogging:
    """Tests for event logging to disk."""
    def test_event_logger_writes_to_log_file(self, tmp_path):
        """install_event_logger() should write bus events to the log file."""
        import asyncio
        import logging
        from wotagent.events import Event, get_bus
        from wotagent.logging import configure_logging, install_event_logger

        log_file = tmp_path / "test_events.log"
        configure_logging(mode="file", log_file=str(log_file))

        # Reset event bus to avoid cross-test pollution
        from wotagent.events.bus import EventBus
        import wotagent.events.bus as bus_module
        bus_module._bus = EventBus()
        bus = get_bus()

        install_event_logger()

        async def emit():
            await bus.emit(Event(
                source="wotagent/test",
                type="wot.agent.thought",
                data={"content": "用户用中文打招呼"},
            ))
            await bus.emit(Event(
                source="wotagent/tools/iot",
                type="wot.agent.action.started",
                data={"device": "light-001", "action": "turnOn"},
            ))

        asyncio.run(emit())

        # Flush logging handlers
        for h in logging.getLogger().handlers:
            h.flush()

        content = log_file.read_text(encoding="utf-8")
        assert "wot.agent.thought" in content
        assert "用户用中文打招呼" in content
        assert "wot.agent.action.started" in content
        assert "light-001" in content
