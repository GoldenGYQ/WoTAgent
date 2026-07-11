"""API contract tests for the side-by-side v2 task endpoint."""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from wotagent.domain import TaskPlan

app_module = importlib.import_module("wotagent.api.app")


class FakeGraph:
    async def ainvoke(self, state):
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

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "sess-api-test"
    assert body["status"] == "responded"
    assert body["response"] == "你好"
    assert body["plan"]["intent"] == "chat"
    assert body["trace"] is None
