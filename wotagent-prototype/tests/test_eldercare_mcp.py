from __future__ import annotations

import asyncio
import json

from fastmcp import Client

from wotagent.eldercare import EldercareStore, Resident, SensorReading, build_eldercare_mcp


def test_health_data_is_exposed_as_resources_and_actions_as_tools(tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        caregiver="王护理员",
    ))
    store.add_reading(SensorReading(
        resident_id="elderly-001",
        sensor_type="heart_rate",
        value=82,
        unit="bpm",
    ))
    server = build_eldercare_mcp(store)

    async def run():
        async with Client(server) as client:
            resources = await client.read_resource("elderly://elderly-001/vitals/latest")
            payload = json.loads(resources[0].text)
            tool_result = await client.call_tool("notify_caregiver", {
                "resident_id": "elderly-001",
                "message": "请复测心率",
                "urgency": "medium",
            })
            return payload, tool_result

    payload, tool_result = asyncio.run(run())

    assert payload["heart_rate"]["value"] == 82
    assert tool_result.structured_content["success"] is True
    assert tool_result.structured_content["recipient"] == "王护理员"
    assert store.list_actions("elderly-001")[0]["action"] == "notify_caregiver"
