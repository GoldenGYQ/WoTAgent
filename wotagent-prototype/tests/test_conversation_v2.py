"""Conversation persistence and v2 context-injection tests."""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from wotagent.agent_graph import AgentGraphDependencies, build_agent_graph
from wotagent.conversation import ConversationStore
from wotagent.domain import PlanStep, TaskPlan
from wotagent.orchestration import TaskOrchestrator
from wotagent.policy import PolicyEngine
from wotagent.telemetry import InMemoryTraceStore

app_module = importlib.import_module("wotagent.api.app")


def test_conversation_store_persists_bounded_completed_history(tmp_path) -> None:
    path = tmp_path / "conversations.db"
    store = ConversationStore(path, history_turns=1)
    conversation = store.create(role="operator")
    conversation_id = conversation["conversation_id"]

    store.start_turn(
        conversation_id=conversation_id,
        task_id="task-1",
        user_message="查询客厅灯",
    )
    store.finish_turn("task-1", response="客厅灯已开启", status="succeeded")
    store.start_turn(
        conversation_id=conversation_id,
        task_id="task-2",
        user_message="把它关闭",
    )
    store.start_turn(
        conversation_id=conversation_id,
        task_id="task-failed",
        user_message="一次失败请求",
    )
    store.finish_turn("task-failed", response="不可信的失败结果", status="failed")

    reopened = ConversationStore(path, history_turns=1)
    assert reopened.get(conversation_id)["turn_count"] == 3
    assert reopened.context(conversation_id, exclude_task_id="task-2") == [
        {"role": "user", "content": "查询客厅灯"},
        {"role": "assistant", "content": "客厅灯已开启"},
    ]


def test_v2_reuses_conversation_context_but_isolates_graph_threads(monkeypatch, tmp_path) -> None:
    class CapturingGraph:
        def __init__(self) -> None:
            self.calls = []

        async def ainvoke(self, state, config=None):
            self.calls.append((state, config))
            return {
                **state,
                "phase": "responded",
                "plan": TaskPlan(intent="chat", summary="ok"),
                "response": f"回复：{state['message']}",
            }

    graph = CapturingGraph()
    store = ConversationStore(tmp_path / "api-conversations.db")
    monkeypatch.setattr(app_module, "_v2_graph", graph)
    monkeypatch.setattr(app_module, "_v2_conversation_store", store)
    monkeypatch.setattr(app_module, "_v2_trace_store", InMemoryTraceStore())
    client = TestClient(app_module.app)

    created = client.post("/api/v2/conversations", json={"role": "operator"})
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]

    first = client.post("/api/v2/tasks", json={
        "conversation_id": conversation_id,
        "message": "查询客厅灯",
        "role": "operator",
    })
    second = client.post("/api/v2/tasks", json={
        "conversation_id": conversation_id,
        "message": "把它关闭",
        "role": "operator",
    })

    assert first.status_code == second.status_code == 202
    assert graph.calls[0][0]["conversation_history"] == []
    assert graph.calls[1][0]["conversation_history"] == [
        {"role": "user", "content": "查询客厅灯"},
        {"role": "assistant", "content": "回复：查询客厅灯"},
    ]
    first_thread = graph.calls[0][1]["configurable"]["thread_id"]
    second_thread = graph.calls[1][1]["configurable"]["thread_id"]
    assert first_thread == first.json()["task_id"]
    assert second_thread == second.json()["task_id"]
    assert first_thread != second_thread

    turns = client.get(f"/api/v2/conversations/{conversation_id}/turns")
    assert turns.status_code == 200
    assert len(turns.json()["turns"]) == 2


def test_sqlite_checkpoint_resumes_confirmation_after_graph_recreation(tmp_path) -> None:
    class Gateway:
        name = "checkpoint-test"

        def __init__(self) -> None:
            self.calls = 0

        async def list_devices(self, location=None):
            return []

        async def query_state(self, device_id: str) -> dict[str, Any]:
            return {"on": True}

        async def control_device(self, *args, **kwargs):
            self.calls += 1
            return {"success": True}

    async def scenario() -> None:
        gateway = Gateway()
        plan = TaskPlan(intent="control", summary="打开灯", steps=[PlanStep(
            id="s1",
            kind="action",
            device_id="light-001",
            action="turnOn",
        )])

        async def planner(_state):
            return plan

        policy = PolicyEngine(high_risk_actions={"turnOn"})
        dependencies = AgentGraphDependencies(
            planner=planner,
            policy=policy,
            orchestrator=TaskOrchestrator(gateway, policy),
        )
        checkpoint_path = str(tmp_path / "checkpoints.db")
        config = {"configurable": {"thread_id": "task-persistent-confirmation"}}

        async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
            await saver.setup()
            graph = build_agent_graph(dependencies, checkpointer=saver)
            waiting = await graph.ainvoke({
                "request_id": "req-1",
                "task_id": "task-persistent-confirmation",
                "session_id": "conv-1",
                "role": "operator",
                "message": "打开灯",
            }, config=config)
            assert waiting["__interrupt__"]

        async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
            await saver.setup()
            recreated_graph = build_agent_graph(dependencies, checkpointer=saver)
            snapshot = await recreated_graph.aget_state(config)
            assert snapshot.interrupts
            completed = await recreated_graph.ainvoke(
                Command(resume={"approved": True}),
                config=config,
            )
            assert completed["trace"].status.value == "succeeded"
            assert gateway.calls == 1

    asyncio.run(scenario())
