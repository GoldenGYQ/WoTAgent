from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from wotagent.domains.eldercare import EldercareRuntime, EldercareStore, Resident
from wotagent.telemetry import InMemoryTraceStore


def make_runtime(tmp_path) -> EldercareRuntime:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        caregiver="王护理员",
    ))
    return EldercareRuntime(store, trace_store=InMemoryTraceStore())


def test_fall_scenario_creates_traceable_butler_task(tmp_path) -> None:
    runtime = make_runtime(tmp_path)

    async def run():
        result = await runtime.simulate("fall", "elderly-001")
        task = result["tasks"][0]
        record = await runtime.trace_store.get(task["task_id"])
        events = await runtime.trace_store.events(task["task_id"])
        return result, record, events

    result, record, events = asyncio.run(run())

    assert len(result["events"]) == 1
    task = result["tasks"][0]
    assert task["status"] == "succeeded"
    assert task["child_results"][0]["agent_id"] == "care_agent"
    event_types = [event.type for event in events]
    assert record is not None and record.status == "succeeded"
    assert "agent.delegation.created" in event_types
    assert event_types.count("agent.tool.completed") == 2
    assert "agent.subgraph.completed" in event_types


def test_hot_room_routes_to_care_and_environment_subgraphs(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    result = asyncio.run(runtime.simulate("hot_room", "elderly-001"))

    agents = {item["agent_id"] for item in result["tasks"][0]["child_results"]}
    assert agents == {"care_agent", "environment_agent"}
    actions = runtime.store.list_actions("elderly-001")
    assert {item["action"] for item in actions} == {
        "notify_caregiver",
        "adjust_temperature",
    }


def test_normal_scenario_does_not_create_agent_task(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    result = asyncio.run(runtime.simulate("normal", "elderly-001"))

    assert result["events"] == []
    assert result["tasks"] == []
    latest = runtime.store.latest("elderly-001")
    assert latest["blood_pressure"]["value"] == "122/78"
    assert latest["room_humidity"]["value"] == 52


def test_high_blood_pressure_scenario_records_trend_and_risk(tmp_path) -> None:
    runtime = make_runtime(tmp_path)

    result = asyncio.run(runtime.simulate("high_blood_pressure", "elderly-001"))

    assert result["events"][0]["event_type"] == "high_blood_pressure"
    assert result["events"][0]["severity"] == "high"
    assert result["tasks"][0]["status"] == "succeeded"
    trend = runtime.store.trend(
        "elderly-001",
        since=datetime.now(UTC) - timedelta(hours=1),
    )
    blood_pressure = next(item for item in trend["series"] if item["sensor_type"] == "blood_pressure")
    assert blood_pressure["points"][-1]["display_value"] == "160/99"


def test_dry_room_scenario_creates_medium_humidity_risk(tmp_path) -> None:
    runtime = make_runtime(tmp_path)

    result = asyncio.run(runtime.simulate("dry_room", "elderly-001"))

    assert result["events"][0]["event_type"] == (
        "room_humidity_out_of_range"
    )
    assert result["events"][0]["severity"] == "medium"
