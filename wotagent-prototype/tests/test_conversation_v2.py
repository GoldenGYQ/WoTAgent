"""Conversation persistence and v2 context-injection tests."""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from wotagent.agent_graph import AgentGraphDependencies, build_agent_graph
from wotagent.application import RunService
from wotagent.conversation import ConversationStore
from wotagent.domain import PlanStep, TaskPlan
from wotagent.orchestration import InMemoryRunStore, TaskOrchestrator
from wotagent.policy import PolicyEngine

app_module = importlib.import_module("wotagent.api.app")


def test_conversation_store_persists_bounded_completed_history(tmp_path) -> None:
    path = tmp_path / "conversations.db"
    store = ConversationStore(path, history_turns=1)
    conversation = store.create(role="operator")
    conversation_id = conversation["conversation_id"]

    store.start_turn(
        conversation_id=conversation_id,
        run_id="run-1",
        user_message="查询客厅灯",
    )
    store.finish_turn("run-1", response="客厅灯已开启", status="completed")
    store.start_turn(
        conversation_id=conversation_id,
        run_id="run-2",
        user_message="把它关闭",
    )
    store.start_turn(
        conversation_id=conversation_id,
        run_id="run-failed",
        user_message="一次失败请求",
    )
    store.finish_turn("run-failed", response="不可信的失败结果", status="failed")

    reopened = ConversationStore(path, history_turns=1)
    assert reopened.get(conversation_id)["turn_count"] == 3
    assert reopened.context(conversation_id, exclude_run_id="run-2") == [
        {"role": "user", "content": "查询客厅灯"},
        {"role": "assistant", "content": "客厅灯已开启"},
    ]


def test_conversation_store_keeps_pending_goal_and_current_role_as_structured_facts(tmp_path) -> None:
    store = ConversationStore(tmp_path / "pending-goal.db")
    conversation_id = store.create(role="viewer")["conversation_id"]
    plan = TaskPlan(intent="control", summary="关闭全部灯", steps=[PlanStep(
        id="off-1",
        kind="action",
        device_id="light-001",
        action="turnOff",
    )]).model_dump(mode="json")

    store.start_turn(
        conversation_id=conversation_id,
        run_id="run-denied",
        user_message="全关掉",
        role="viewer",
    )
    store.finish_turn(
        "run-denied",
        response="权限不足",
        status="denied",
        result={
            "status": "denied",
            "plan": plan,
            "policy_decisions": [{
                "effect": "deny",
                "reason_code": "RBAC_ACTION_DENIED",
                "reason": "viewer cannot control",
            }],
        },
    )

    runtime = store.runtime_context(conversation_id, current_role="operator")

    assert runtime["current_role"] == "operator"
    assert runtime["pending_goal"]["user_message"] == "全关掉"
    assert runtime["pending_goal"]["role"] == "viewer"
    assert runtime["pending_goal"]["plan"]["steps"][0]["action"] == "turnOff"
    assert runtime["pending_goal"]["policy_decisions"][0]["reason_code"] == "RBAC_ACTION_DENIED"

    query_plan = TaskPlan(intent="query", summary="查询灯状态", steps=[PlanStep(
        id="query-1",
        kind="query",
        device_id="light-001",
        action="queryState",
    )]).model_dump(mode="json")
    store.start_turn(
        conversation_id=conversation_id,
        run_id="run-query",
        user_message="现在灯是什么状态",
        role="operator",
    )
    store.finish_turn(
        "run-query",
        response="灯已开启",
        status="succeeded",
        result={"status": "succeeded", "plan": query_plan},
    )
    assert store.runtime_context(
        conversation_id, current_role="operator"
    )["pending_goal"]["user_message"] == "全关掉"

    store.start_turn(
        conversation_id=conversation_id,
        run_id="run-succeeded",
        user_message="现在呢",
        role="operator",
    )
    store.finish_turn(
        "run-succeeded",
        response="已关闭",
        status="succeeded",
        result={"status": "succeeded", "plan": plan},
    )

    assert store.runtime_context(
        conversation_id, current_role="operator"
    )["pending_goal"] is None


def test_needs_more_information_turn_is_retained_for_follow_up_context(tmp_path) -> None:
    store = ConversationStore(tmp_path / "clarify-context.db", history_turns=2)
    conversation_id = store.create(role="operator")["conversation_id"]
    plan = TaskPlan(intent="control", summary="调整空调温度", steps=[PlanStep(
        id="temp-1",
        kind="action",
        device_id="ac-002",
        action="setTemperature",
    )]).model_dump(mode="json")

    store.start_turn(
        conversation_id=conversation_id,
        run_id="run-clarify",
        user_message="把张爷爷房间温度调到 24 度",
        role="operator",
    )
    store.finish_turn(
        "run-clarify",
        response="请问张爷爷住在哪个房间？是 A-101 还是 A-102？",
        status="needs_more_information",
        result={
            "status": "needs_more_information",
            "plan": plan,
        },
    )

    assert store.context(conversation_id, exclude_run_id="run-next") == [
        {"role": "user", "content": "把张爷爷房间温度调到 24 度"},
        {"role": "assistant", "content": "请问张爷爷住在哪个房间？是 A-101 还是 A-102？"},
    ]
    runtime = store.runtime_context(conversation_id, current_role="operator")
    assert runtime["pending_goal"]["status"] == "needs_more_information"
    assert runtime["pending_goal"]["user_message"] == "把张爷爷房间温度调到 24 度"


def test_v2_reuses_conversation_context_but_isolates_runs(monkeypatch, tmp_path) -> None:
    conversation_store = ConversationStore(tmp_path / "api-conversations.db")
    run_store = InMemoryRunStore()
    captured_contexts: list[dict[str, Any]] = []

    class CapturingCoordinator:
        def __init__(self, context):
            captured_contexts.append(context)

        async def execute(self, *, run_id, **_kwargs):
            return await run_store.update_run(
                run_id,
                lambda run: _complete_run(run),
            )

    service = RunService(
        conversation_store,
        run_store,
        CapturingCoordinator,
    )
    monkeypatch.setattr(
        app_module._runtime,
        "conversation_store",
        conversation_store,
    )
    monkeypatch.setattr(app_module, "_run_store", run_store)
    monkeypatch.setattr(app_module, "_run_service", service)
    client = TestClient(app_module.app)

    created = client.post("/api/v2/conversations", json={"role": "operator"})
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]

    first = client.post("/api/v2/runs", json={
        "conversation_id": conversation_id,
        "message": "查询客厅灯",
        "role": "operator",
    })
    second = client.post("/api/v2/runs", json={
        "conversation_id": conversation_id,
        "message": "把它关闭",
        "role": "operator",
    })

    assert first.status_code == second.status_code == 202
    assert captured_contexts[0]["history"] == []
    assert captured_contexts[1]["history"] == [
        {"role": "user", "content": "查询客厅灯"},
        {"role": "assistant", "content": "回复：查询客厅灯"},
    ]
    assert captured_contexts[1]["runtime"]["current_role"] == "operator"
    assert first.json()["run_id"] != second.json()["run_id"]
    assert first.json()["turn_id"] != second.json()["turn_id"]

    turns = client.get(f"/api/v2/conversations/{conversation_id}/turns")
    assert turns.status_code == 200
    assert len(turns.json()["turns"]) == 2


def _complete_run(run) -> None:
    run.status = "completed"
    run.final_response = f"回复：{run.root_goal}"


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
