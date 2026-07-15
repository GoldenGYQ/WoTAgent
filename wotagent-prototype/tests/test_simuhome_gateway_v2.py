from __future__ import annotations

import json
import asyncio

import httpx
from scripts.evaluate_simuhome_v2 import evaluate_qt3
from wotagent.agent_graph.planner import select_capability_context
from wotagent.gateway.simuhome import SimuHomeBinding


def _episode() -> dict:
    return {
        "meta": {"seed": 7, "query_type": "qt3", "case": "feasible"},
        "query": "Power on living room light 1 and set it to 35 percent.",
        "eval": {
            "required_actions": [
                {"tool": "get_room_devices", "params": {"room_id": "living_room"}}
            ],
            "goals": [
                {
                    "device_id": "living_room_dimmable_light_1",
                    "asserts": [
                        {"attribute": "1.OnOff.OnOff", "value": True},
                        {"attribute": "1.LevelControl.CurrentLevel", "value": 35},
                    ],
                }
            ],
        },
        "initial_home_config": {
            "tick_interval": 0.1,
            "base_time": "2025-08-23 14:09:25",
            "rooms": {
                "living_room": {
                    "devices": [
                        {
                            "device_id": "living_room_dimmable_light_1",
                            "device_type": "dimmable_light",
                            "attributes": {
                                "1.OnOff.OnOff": False,
                                "1.LevelControl.CurrentLevel": 20,
                            },
                        }
                    ],
                    "state": {},
                }
            },
        },
    }


def test_gateway_discovers_controls_and_reads_simuhome_runtime() -> None:
    state = _episode()["initial_home_config"]
    requests: list[tuple[str, str, dict]] = []

    def response(data: object) -> httpx.Response:
        return httpx.Response(200, json={"status": {"code": 200}, "data": data, "error": None})

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/__health__"):
            return response({"status": "healthy"})
        if request.url.path.endswith("/simulation/reset"):
            return response({"reset": True})
        if request.url.path.endswith("/rooms/living_room/devices"):
            return response(state["rooms"]["living_room"]["devices"])
        if request.url.path.endswith("/attributes"):
            return response(state["rooms"]["living_room"]["devices"][0]["attributes"])
        if request.url.path.endswith("/commands"):
            attrs = state["rooms"]["living_room"]["devices"][0]["attributes"]
            if body["command_id"] == "On":
                attrs["1.OnOff.OnOff"] = True
            if body["command_id"] == "MoveToLevel":
                attrs["1.LevelControl.CurrentLevel"] = body["args"]["Level"]
            return response({"executed": True})
        if request.url.path.endswith("/home/state"):
            return response(state)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://simuhome.test"
        )
        gateway = SimuHomeBinding("http://simuhome.test/api", client=client)
        await gateway.reset_episode(_episode())
        gateway.set_request(_episode()["query"])
        capabilities = await gateway.capabilities()
        assert [item["name"] for item in capabilities[0]["actions"]] == [
            "turnOn",
            "turnOff",
            "setBrightness",
        ]
        assert capabilities[0]["title"] == "Dimmer Light 1 (dimmable_light)"
        assert capabilities[0]["aliases"] == [
            "living room dimmer light 1",
            "dimmer light 1",
        ]
        await gateway.control_device(
            "living_room_dimmable_light_1", "turnOn", {}, idempotency_key="task:on"
        )
        await gateway.control_device(
            "living_room_dimmable_light_1",
            "setBrightness",
            {"brightness": 35},
            idempotency_key="task:level",
        )
        observed = await gateway.query_state("living_room_dimmable_light_1")
        assert observed["on"] is True
        assert observed["brightness"] == 35
        assert gateway.tool_invocations[0] == {
            "tool": "get_room_devices",
            "params": {"room_id": "living_room"},
            "outcome": {"ok": True, "status_code": 200, "error_type": None},
        }
        command_bodies = [body for method, path, body in requests if path.endswith("/commands")]
        assert command_bodies == [
            {
                "endpoint_id": 1,
                "cluster_id": "OnOff",
                "command_id": "On",
                "args": {},
            },
            {
                "endpoint_id": 1,
                "cluster_id": "LevelControl",
                "command_id": "MoveToLevel",
                "args": {"Level": 35, "TransitionTime": 0},
            },
        ]
        await client.aclose()

    asyncio.run(scenario())


def test_qt3_evaluator_requires_discovery_and_exact_final_state() -> None:
    episode = _episode()
    final_state = episode["initial_home_config"]
    attrs = final_state["rooms"]["living_room"]["devices"][0]["attributes"]
    attrs["1.OnOff.OnOff"] = True
    attrs["1.LevelControl.CurrentLevel"] = 35
    tools = [{"tool": "get_room_devices", "params": {"room_id": "living_room"}}]

    passed = evaluate_qt3(episode, final_state, tools)
    assert passed["score"] == 1
    assert passed["assertions_passed"] == 2

    failed = evaluate_qt3(episode, final_state, [])
    assert failed["score"] == 0
    assert failed["goal_success"] is True
    assert failed["required_action_success"] is False


def test_dynamic_simuhome_location_and_device_type_filtering() -> None:
    devices = [
        {
            "device_id": "living_room_on_off_light_1",
            "title": "On Off Light (on_off_light)",
            "location": "living_room",
            "device_type": "on_off_light",
            "location_aliases": ["living room"],
            "actions": [{"name": "turnOn", "input_schema": {}}],
            "properties": ["on"],
        },
        {
            "device_id": "kitchen_air_conditioner_1",
            "title": "Air Conditioner (air_conditioner)",
            "location": "kitchen",
            "device_type": "air_conditioner",
            "location_aliases": ["kitchen"],
            "actions": [{"name": "setMode", "input_schema": {}}],
            "properties": ["mode"],
        },
    ]
    selected = select_capability_context(devices, "power on the living room light")
    assert [device["device_id"] for device in selected] == ["living_room_on_off_light_1"]
