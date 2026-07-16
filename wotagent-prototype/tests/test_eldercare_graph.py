"""Functional-unit tests for Butler dispatch and explicit child subgraphs."""

import asyncio

from wotagent.eldercare import (
    EldercareActionService,
    EldercareStore,
    Resident,
    RiskEvent,
    build_eldercare_butler_graph,
)
from wotagent.eldercare.models import DelegatedAction, DelegatedTask


def setup_graph(tmp_path):
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        caregiver="王护理员",
    ))
    return store, build_eldercare_butler_graph(EldercareActionService(store))


def test_room_overheat_fans_out_to_two_subgraphs_and_aggregates(tmp_path) -> None:
    store, graph = setup_graph(tmp_path)
    event = RiskEvent(
        resident_id="elderly-001",
        event_type="room_overheat",
        severity="medium",
        value=30.5,
        message="房间温度过高",
        context={"latest": {"room_temperature": {"value": 30.5}}},
    )

    result = asyncio.run(graph.ainvoke({"event": event.model_dump(mode="json")}))

    assert result["status"] == "succeeded"
    assert {item["agent_id"] for item in result["child_results"]} == {
        "care_agent", "environment_agent"
    }
    assert {item["action"] for item in store.list_actions("elderly-001")} == {
        "notify_caregiver", "adjust_temperature"
    }


def test_delegation_policy_blocks_cross_agent_mcp_scope(tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(resident_id="elderly-001", name="张爷爷", room="A-101"))

    def malicious_plan(_event):
        return [DelegatedTask(
            agent_id="environment_agent",
            objective="越权通知护理员",
            resident_scope=["elderly-001"],
            allowed_mcp="eldercare-environment",
            actions=[DelegatedAction(
                name="notify_caregiver",
                arguments={
                    "resident_id": "elderly-001",
                    "message": "test",
                    "urgency": "high",
                },
            )],
        )]

    graph = build_eldercare_butler_graph(
        EldercareActionService(store),
        planner=malicious_plan,
    )
    event = RiskEvent(
        resident_id="elderly-001",
        event_type="fall_detected",
        severity="critical",
        value=True,
        message="疑似跌倒",
    )

    result = asyncio.run(graph.ainvoke({"event": event.model_dump(mode="json")}))

    assert result["status"] == "denied"
    assert result["denied_delegations"][0]["reason"] == "action is outside agent allowlist"
    assert store.list_actions() == []


def test_care_event_creates_notification_and_care_task(tmp_path) -> None:
    store, graph = setup_graph(tmp_path)
    event = RiskEvent(
        resident_id="elderly-001",
        event_type="fall_detected",
        severity="critical",
        value=True,
        message="疑似跌倒",
    )

    result = asyncio.run(graph.ainvoke({"event": event.model_dump(mode="json")}))

    assert result["status"] == "succeeded"
    assert len(result["child_results"]) == 1
    assert {item["action"] for item in store.list_actions("elderly-001")} == {
        "notify_caregiver", "create_care_task"
    }
