"""API contract tests for the side-by-side v2 task endpoint."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from wotagent.agent_graph import AgentGraphDependencies, build_agent_graph
from wotagent.conversation import ConversationStore
from wotagent.domain import PlanStep, PolicyDecision, TaskPlan
from wotagent.orchestration import TaskOrchestrator
from wotagent.policy import PolicyEngine

app_module = importlib.import_module("wotagent.api.app")


@pytest.fixture(autouse=True)
def isolated_conversation_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        app_module,
        "_v2_conversation_store",
        ConversationStore(tmp_path / "api-v2-conversations.db"),
    )


class FakeGraph:
    async def ainvoke(self, state, config=None):
        return {
            **state,
            "phase": "responded",
            "plan": TaskPlan(intent="chat", summary="你好"),
            "response": "你好",
        }


def test_v2_task_endpoint_returns_real_graph_result(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_v2_graph", FakeGraph())
    client = TestClient(app_module.app)

    response = client.post("/api/v2/tasks", json={
        "message": "你好",
        "session_id": "sess-api-test",
        "role": "viewer",
        "thinking": True,
    })

    assert response.status_code == 202
    body = response.json()
    assert body["session_id"] == "sess-api-test"
    assert body["status"] == "accepted"
    assert body["response"] == ""
    assert body["plan"] is None
    assert body["trace"] is None
    assert body["confirmation"] is None
    assert body["thinking"] is True
    task_id = body["task_id"]
    task = client.get(f"/api/v2/tasks/{task_id}")
    assert task.status_code == 200
    assert task.json()["status"] == "responded"
    assert task.json()["result"]["thinking"] is True

    events = client.get(f"/api/v2/tasks/{task_id}/events")
    assert events.status_code == 200
    assert events.json()["events"][0]["type"] == "task.started"
    assert events.json()["next_cursor"] >= 2

    with client.stream("GET", f"/api/v2/tasks/{task_id}/events/stream") as stream:
        text = "".join(stream.iter_text())
    assert "event: task.started" in text
    assert f'"task_id":"{task_id}"' in text

    with client.websocket_connect("/ws/v2") as websocket:
        websocket.send_json({"task_id": task_id, "after": 1})
        messages = []
        while True:
            item = websocket.receive_json()
            messages.append(item)
            if item["type"] == "stream.completed":
                break
    assert messages[-1]["status"] == "responded"
    assert all(item.get("sequence", 2) > 1 for item in messages[:-1])


def test_v2_capabilities_reports_thinking_without_exposing_private_reasoning(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_pipeline", SimpleNamespace(thinking_planner=object()))
    response = TestClient(app_module.app).get("/api/v2/capabilities")

    assert response.status_code == 200
    assert response.json()["thinking"]["supported"] is True
    assert response.json()["thinking"]["default"] is False
    assert response.json()["thinking"]["private_reasoning_exposed"] is False


def test_v2_task_rejects_thinking_when_server_capability_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_pipeline", SimpleNamespace(thinking_planner=None))
    response = TestClient(app_module.app).post("/api/v2/tasks", json={
        "message": "分析一下",
        "thinking": True,
    })

    assert response.status_code == 409
    assert "not available" in response.json()["detail"]


def test_policy_denial_has_semantic_status_and_persists_pending_goal(monkeypatch) -> None:
    class DeniedGraph:
        async def ainvoke(self, state, config=None):
            return {
                **state,
                "phase": "responded",
                "plan": TaskPlan(intent="control", summary="关闭全部灯", steps=[PlanStep(
                    id="off-1",
                    kind="action",
                    device_id="light-001",
                    action="turnOff",
                )]),
                "policy_decisions": [PolicyDecision(
                    effect="deny",
                    reason_code="RBAC_ACTION_DENIED",
                    reason="viewer cannot control",
                )],
                "response": "权限不足",
            }

    monkeypatch.setattr(app_module, "_v2_graph", DeniedGraph())
    client = TestClient(app_module.app)
    conversation = client.post("/api/v2/conversations", json={"role": "viewer"}).json()

    created = client.post("/api/v2/tasks", json={
        "conversation_id": conversation["conversation_id"],
        "message": "全关掉",
        "role": "viewer",
    })
    task = client.get(f"/api/v2/tasks/{created.json()['task_id']}").json()
    runtime = app_module._v2_conversation_store.runtime_context(
        conversation["conversation_id"],
        current_role="operator",
    )

    assert task["status"] == "denied"
    assert task["result"]["policy_decisions"][0]["reason_code"] == "RBAC_ACTION_DENIED"
    assert runtime["pending_goal"]["plan"]["summary"] == "关闭全部灯"
    assert runtime["current_role"] == "operator"


def test_v2_confirmation_endpoint_resumes_same_checkpoint(monkeypatch) -> None:
    class Gateway:
        name = "api-test"
        calls = 0

        async def list_devices(self, location=None):
            return []

        async def query_state(self, device_id: str) -> dict[str, Any]:
            return {"on": True}

        async def control_device(self, *args, **kwargs):
            self.calls += 1
            return {"success": True}

    gateway = Gateway()
    plan = TaskPlan(intent="control", summary="高风险开灯", steps=[PlanStep(
        id="s1", kind="action", device_id="light-001", action="turnOn"
    )])

    async def planner(_state):
        return plan

    policy = PolicyEngine(high_risk_actions={"turnOn"})
    graph = build_agent_graph(
        AgentGraphDependencies(
            planner=planner,
            policy=policy,
            orchestrator=TaskOrchestrator(gateway, policy),
        ),
        checkpointer=InMemorySaver(),
    )
    monkeypatch.setattr(app_module, "_v2_graph", graph)
    client = TestClient(app_module.app)

    created = client.post("/api/v2/tasks", json={
        "message": "打开灯",
        "session_id": "sess-confirm-api",
        "role": "operator",
    })

    assert created.status_code == 202
    task_id = created.json()["task_id"]
    waiting = client.get(f"/api/v2/tasks/{task_id}")
    assert waiting.json()["status"] == "waiting_confirmation"
    assert gateway.calls == 0

    confirmed = client.post(
        f"/api/v2/tasks/{task_id}/confirm",
        json={"approved": True},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "succeeded"
    assert gateway.calls == 1
