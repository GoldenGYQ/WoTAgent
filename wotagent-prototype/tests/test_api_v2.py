"""API contract tests for the side-by-side v2 task endpoint."""

from __future__ import annotations

import importlib
from typing import Any

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from wotagent.agent_graph import AgentGraphDependencies, build_agent_graph
from wotagent.domain import PlanStep, TaskPlan
from wotagent.orchestration import TaskOrchestrator
from wotagent.policy import PolicyEngine

app_module = importlib.import_module("wotagent.api.app")


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
    })

    assert response.status_code == 202
    body = response.json()
    assert body["session_id"] == "sess-api-test"
    assert body["status"] == "accepted"
    assert body["response"] == ""
    assert body["plan"] is None
    assert body["trace"] is None
    assert body["confirmation"] is None
    task_id = body["task_id"]
    task = client.get(f"/api/v2/tasks/{task_id}")
    assert task.status_code == 200
    assert task.json()["status"] == "responded"

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
