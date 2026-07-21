"""HTTP contract tests for the Run -> Task -> AgentRun API."""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from wotagent.agents import AgentFinding, Evidence
from wotagent.application import RunService
from wotagent.conversation import ConversationStore
from wotagent.orchestration import (
    InMemoryRunStore,
    RunCoordinator,
    RunRecord,
    SupervisorDecision,
    TaskScheduler,
)

app_module = importlib.import_module("wotagent.api.app")


@pytest.fixture
def run_client(monkeypatch, tmp_path):
    store = InMemoryRunStore()
    conversations = ConversationStore(tmp_path / "conversations.db")

    async def execute(task):
        return AgentFinding(
            agent=task.agent_id,
            objective=task.objective,
            status="completed",
            finding="客厅灯当前为开启状态。",
            confidence=1,
            evidence=[Evidence(
                source="fake-device",
                value={"on": True},
                reliability=1,
                description="可重复的测试设备状态",
            )],
        )

    def coordinator_factory(_context):
        decisions = iter([
            SupervisorDecision(
                action="create_tasks",
                goal="查询客厅灯",
                tasks=[{
                    "local_id": "inspect-light",
                    "agent_id": "environment_agent",
                    "objective": "读取客厅灯当前状态",
                    "reason": "需要设备状态证据",
                    "expected_evidence": ["开关状态"],
                    "success_criteria": ["获得客厅灯当前状态"],
                    "resource_claims": [{
                        "key": "device:light-001",
                        "mode": "read",
                    }],
                }],
                success_criteria=["获得客厅灯当前状态"],
                reason="委派环境专业 Agent",
            ),
            SupervisorDecision(
                action="finish",
                goal_completed=True,
                goal="查询客厅灯",
                final_response="客厅灯当前为开启状态。",
                reason="成功条件已满足",
            ),
        ])
        return RunCoordinator(
            store,
            TaskScheduler(available_agents={"environment_agent"}),
            lambda _state: next(decisions),
            {"environment_agent": execute},
        )

    service = RunService(
        conversations,
        store,
        coordinator_factory,
        thinking_supported=lambda: True,
    )
    monkeypatch.setattr(app_module, "_run_store", store)
    monkeypatch.setattr(app_module, "_run_service", service)
    return TestClient(app_module.app), store, conversations


def test_run_api_exposes_task_and_agent_run_hierarchy(run_client) -> None:
    client, _store, _conversations = run_client

    response = client.post("/api/v2/runs", json={
        "message": "查询客厅灯",
        "conversation_id": "conv-api-test",
        "role": "viewer",
        "phone_mode": True,
    })

    assert response.status_code == 202
    created = response.json()
    assert created["run_id"].startswith("run_")
    assert created["conversation_id"] == "conv-api-test"
    assert created["turn_id"].startswith("turn_")
    assert created["source_context"]["interaction_mode"]["channel"] == "phone"
    assert created["source_context"]["response_style"]["spoken"] is True

    run_id = created["run_id"]
    run = client.get(f"/api/v2/runs/{run_id}")
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    assert run.json()["final_response"] == "客厅灯当前为开启状态。"

    tasks = client.get(f"/api/v2/runs/{run_id}/tasks").json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["agent_id"] == "environment_agent"
    assert tasks[0]["status"] == "completed"

    task_id = tasks[0]["task_id"]
    agent_runs = client.get(
        f"/api/v2/runs/{run_id}/tasks/{task_id}/agent-runs"
    ).json()["agent_runs"]
    assert len(agent_runs) == 1
    assert agent_runs[0]["agent_id"] == "environment_agent"
    assert agent_runs[0]["status"] == "completed"

    events = client.get(f"/api/v2/runs/{run_id}/events").json()
    event_types = [item["type"] for item in events["events"]]
    assert event_types[0] == "run.started"
    assert "task.created" in event_types
    assert "task.completed" in event_types
    assert event_types[-1] == "run.completed"


def test_run_websocket_replays_events_by_run_id(run_client) -> None:
    client, _store, _conversations = run_client
    run_id = client.post(
        "/api/v2/runs",
        json={"message": "查询客厅灯"},
    ).json()["run_id"]

    with client.websocket_connect("/ws/v2") as websocket:
        websocket.send_json({"run_id": run_id, "after": 0})
        messages = []
        while True:
            item = websocket.receive_json()
            messages.append(item)
            if item["type"] == "stream.completed":
                break

    assert messages[0]["run_id"] == run_id
    assert messages[-1]["status"] == "completed"
    assert messages[-1]["cursor"] == len(messages) - 1


def test_trace_artifact_api_keeps_raw_payload_out_of_event_stream(run_client) -> None:
    client, store, _conversations = run_client
    run_id = client.post(
        "/api/v2/runs",
        json={"message": "Inspect the living room light"},
    ).json()["run_id"]

    event = asyncio.run(store.record_event(
        run_id,
        "model.request.completed",
        node="planner",
        data={"purpose": "task_planning", "total_tokens": 42},
        raw_data={
            "messages": [{"role": "user", "content": "raw prompt"}],
            "response": {"intent": "query"},
        },
    ))

    streamed = client.get(f"/api/v2/runs/{run_id}/events").json()["events"][-1]
    assert streamed["data"] == {"purpose": "task_planning", "total_tokens": 42}
    assert "messages" not in streamed["data"]
    assert streamed["raw_artifact_id"] == event.raw_artifact_id

    payload = client.get(
        f"/api/v2/runs/{run_id}/trace-artifacts/{event.raw_artifact_id}"
    ).json()["artifact"]
    assert payload["payload"]["messages"][0]["content"] == "raw prompt"
    assert payload["payload"]["response"] == {"intent": "query"}


def test_old_task_api_is_removed(run_client) -> None:
    client, _store, _conversations = run_client
    assert client.post("/api/v2/tasks", json={"message": "你好"}).status_code == 404


def test_thinking_request_is_rejected_when_model_does_not_support_it(
    monkeypatch,
    tmp_path,
) -> None:
    store = InMemoryRunStore()
    service = RunService(
        ConversationStore(tmp_path / "thinking.db"),
        store,
        lambda _context: None,  # type: ignore[arg-type]
        thinking_supported=lambda: False,
    )
    monkeypatch.setattr(app_module, "_run_store", store)
    monkeypatch.setattr(app_module, "_run_service", service)

    response = TestClient(app_module.app).post("/api/v2/runs", json={
        "message": "深入分析",
        "thinking": True,
    })

    assert response.status_code == 409
    assert "not available" in response.json()["detail"]


def test_run_api_persists_error_on_failed_run(monkeypatch, tmp_path) -> None:
    store = InMemoryRunStore()
    conversations = ConversationStore(tmp_path / "failed-run.db")

    class FailingCoordinator:
        async def execute(self, **_kwargs):
            raise TimeoutError("supervisor model request timed out after 1.0s")

    service = RunService(
        conversations,
        store,
        lambda _context: FailingCoordinator(),
        thinking_supported=lambda: True,
    )
    monkeypatch.setattr(app_module, "_run_store", store)
    monkeypatch.setattr(app_module, "_run_service", service)

    client = TestClient(app_module.app)
    created = client.post("/api/v2/runs", json={
        "message": "把张爷爷房间温度调到 24 度，并确认是否生效",
    }).json()

    run = client.get(f"/api/v2/runs/{created['run_id']}")
    assert run.status_code == 200
    payload = run.json()
    assert payload["status"] == "failed"
    assert payload["error"] == "supervisor model request timed out after 1.0s"
    assert payload["final_response"] == "supervisor model request timed out after 1.0s"


def test_capabilities_report_run_task_scheduler() -> None:
    response = TestClient(app_module.app).get("/api/v2/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["thinking"]["private_reasoning_exposed"] is False
    assert {item["agent_id"] for item in body["agents"]} >= {
        "health_agent",
        "environment_agent",
        "care_agent",
    }
