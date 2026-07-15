"""SimuHome REST/Matter binding used behind TD-generated MCP tools."""

from __future__ import annotations

from typing import Any

import httpx


_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
_MODE_TO_MATTER = {"off": 0, "auto": 1, "cool": 3, "heat": 4, "fan": 7, "dry": 8}
_MATTER_TO_MODE = {value: key for key, value in _MODE_TO_MATTER.items()}
_OPERATION_TO_STATE = {"stop": 0, "start": 1, "pause": 2, "resume": 1}
_STATE_TO_OPERATION = {0: "stopped", 1: "running", 2: "paused", 3: "error"}


def _number_schema(name: str, minimum: float, maximum: float) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": "number", "minimum": minimum, "maximum": maximum}},
        "required": [name],
        "additionalProperties": False,
    }


def _integer_schema(name: str, minimum: int, maximum: int) -> dict[str, Any]:
    schema = _number_schema(name, minimum, maximum)
    schema["properties"][name]["type"] = "integer"
    return schema


class SimuHomeBinding:
    """Translate normalized WoT actions to an unmodified SimuHome Runtime."""

    name = "simuhome"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:18081/api",
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._episode: dict[str, Any] | None = None
        self._devices: dict[str, dict[str, Any]] = {}
        self._capabilities: list[dict[str, Any]] | None = None
        self._requested_locations: set[str] = set()
        self.tool_invocations: list[dict[str, Any]] = []

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> bool:
        try:
            await self._request("GET", "/__health__")
            return True
        except (httpx.HTTPError, RuntimeError):
            return False

    async def reset_episode(self, episode: dict[str, Any]) -> dict[str, Any]:
        config = episode.get("initial_home_config")
        if not isinstance(config, dict):
            raise ValueError("SimuHome episode has no initial_home_config")
        self._episode = episode
        self._devices = _episode_devices(config)
        self._capabilities = None
        self._requested_locations.clear()
        self.tool_invocations.clear()
        return await self._request("POST", "/simulation/reset", json=config)

    def set_request(self, message: str) -> None:
        """Scope runtime discovery to rooms explicitly named by the user."""
        text = message.lower().replace("-", " ")
        self._requested_locations = {
            str(device["location"])
            for device in self._devices.values()
            if str(device["location"]).replace("_", " ").lower() in text
        }

    async def initialize(self) -> None:
        if self._episode is None:
            raise RuntimeError("reset_episode must be called before using SimuHomeBinding")
        if not await self.health():
            raise ConnectionError(f"SimuHome runtime is unavailable at {self.base_url}")

    async def capabilities(self) -> list[dict[str, Any]]:
        await self.initialize()
        if self._capabilities is not None:
            return self._capabilities
        rooms = sorted(self._requested_locations)
        for room in rooms:
            await self.list_devices(room)
        capabilities = [_device_capability(device) for device in self._devices.values()]
        self._capabilities = [item for item in capabilities if item["actions"]]
        return self._capabilities

    async def list_devices(self, location: str | None = None) -> list[dict[str, Any]]:
        await self.initialize()
        if location is None:
            return list(self._devices.values())
        try:
            data = await self._request("GET", f"/rooms/{location}/devices")
            self._record_tool("get_room_devices", {"room_id": location}, ok=True, status_code=200)
        except Exception as exc:
            self._record_tool(
                "get_room_devices",
                {"room_id": location},
                ok=False,
                status_code=None,
                error_type=type(exc).__name__,
            )
            raise
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            devices = data.get("devices", data.get("data", []))
            return devices if isinstance(devices, list) else []
        return []

    async def query_state(self, device_id: str) -> dict[str, Any]:
        attrs = await self.raw_attributes(device_id)
        state: dict[str, Any] = {"raw_attributes": attrs}
        on = _find_attribute(attrs, "OnOff.OnOff")
        if on is not None:
            state["on"] = bool(on)
        brightness = _find_attribute(attrs, "LevelControl.CurrentLevel")
        if brightness is not None:
            state["brightness"] = brightness
        speed = _find_attribute(attrs, "FanControl.PercentSetting")
        if speed is not None:
            state["speed"] = speed
        mode = _find_attribute(attrs, "Thermostat.SystemMode")
        if mode is not None:
            state["mode"] = _MATTER_TO_MODE.get(mode, mode)
        target = _temperature_target(attrs)
        if target is not None:
            state["targetTemp"] = target / 100
        position = _find_attribute(attrs, "WindowCovering.TargetPositionLiftPercent100ths")
        if position is not None:
            state["position"] = position / 100
        operation = _find_attribute(attrs, "OperationalState.OperationalState")
        if operation is not None:
            state["operationalState"] = _STATE_TO_OPERATION.get(operation, operation)
        return state

    async def raw_attributes(self, device_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/devices/{device_id}/attributes")
        if not isinstance(data, dict):
            raise RuntimeError(f"SimuHome returned invalid attributes for {device_id}")
        return data

    async def home_state(self) -> dict[str, Any]:
        data = await self._request("GET", "/home/state")
        if not isinstance(data, dict):
            raise RuntimeError("SimuHome returned invalid home state")
        return data

    async def control_device(
        self,
        device_id: str,
        action: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        attrs = self._device_attributes(device_id)
        endpoint, cluster, command, args = await self._binding(device_id, attrs, action, parameters)
        if command.startswith("write:"):
            attribute = command.split(":", 1)[1]
            data = await self._request("POST", f"/devices/{device_id}/attributes/write", json={
                "endpoint_id": endpoint,
                "cluster_id": cluster,
                "attribute_id": attribute,
                "value": args["value"],
            })
            tool = "write_attribute"
        else:
            data = await self._request("POST", f"/devices/{device_id}/commands", json={
                "endpoint_id": endpoint,
                "cluster_id": cluster,
                "command_id": command,
                "args": args,
            })
            tool = "execute_command"
        self._record_tool(tool, {
            "device_id": device_id,
            "endpoint_id": endpoint,
            "cluster_id": cluster,
            "action": action,
            **parameters,
        }, ok=True, status_code=200)
        return {
            "success": True,
            "accepted": True,
            "idempotency_key": idempotency_key,
            "binding": {"tool": tool, "cluster": cluster, "operation": command},
            "result": data,
        }

    async def _binding(
        self,
        device_id: str,
        attrs: dict[str, Any],
        action: str,
        parameters: dict[str, Any],
    ) -> tuple[int, str, str, dict[str, Any]]:
        if action in {"turnOn", "turnOff"}:
            endpoint = _attribute_endpoint(attrs, "OnOff.OnOff")
            return endpoint, "OnOff", "On" if action == "turnOn" else "Off", {}
        if action == "setBrightness":
            endpoint = _attribute_endpoint(attrs, "LevelControl.CurrentLevel")
            return endpoint, "LevelControl", "MoveToLevel", {
                "Level": int(parameters["brightness"]), "TransitionTime": 0,
            }
        if action == "setSpeed":
            endpoint = _attribute_endpoint(attrs, "FanControl.PercentSetting")
            return endpoint, "FanControl", "write:PercentSetting", {"value": int(parameters["speed"])}
        if action == "setMode":
            endpoint = _attribute_endpoint(attrs, "Thermostat.SystemMode")
            mode = parameters["mode"]
            value = _MODE_TO_MATTER.get(str(mode).lower(), mode)
            return endpoint, "Thermostat", "write:SystemMode", {"value": int(value)}
        if action == "setTemperature":
            value = int(round(float(parameters["temperature"]) * 100))
            temp_key = _find_key(attrs, "TemperatureControl.TemperatureSetpoint")
            if temp_key:
                endpoint = int(temp_key.split(".", 1)[0])
                return endpoint, "TemperatureControl", "SetTemperature", {"target_temperature": value}
            mode = _find_attribute(await self.raw_attributes(device_id), "Thermostat.SystemMode")
            suffix = (
                "Thermostat.OccupiedHeatingSetpoint"
                if mode == _MODE_TO_MATTER["heat"]
                else "Thermostat.OccupiedCoolingSetpoint"
            )
            endpoint = _attribute_endpoint(attrs, suffix)
            return endpoint, "Thermostat", f"write:{suffix.rsplit('.', 1)[1]}", {"value": value}
        if action == "setPosition":
            endpoint = _attribute_endpoint(attrs, "WindowCovering.TargetPositionLiftPercent100ths")
            return endpoint, "WindowCovering", "write:TargetPositionLiftPercent100ths", {
                "value": int(round(float(parameters["position"]) * 100)),
            }
        if action in _OPERATION_TO_STATE:
            endpoint = _attribute_endpoint(attrs, "OperationalState.OperationalState")
            return endpoint, "OperationalState", action.capitalize(), {}
        if action == "setDrynessLevel":
            endpoint = _attribute_endpoint(attrs, "LaundryDryerControls.SelectedDrynessLevel")
            return endpoint, "LaundryDryerControls", "write:SelectedDrynessLevel", {
                "value": int(parameters["level"]),
            }
        if action == "setWasherMode":
            endpoint = _attribute_endpoint(attrs, "LaundryWasherMode.CurrentMode")
            return endpoint, "LaundryWasherMode", "write:CurrentMode", {"value": int(parameters["mode"])}
        if action == "setChannel":
            endpoint = _attribute_endpoint(attrs, "Channel.CurrentChannel")
            return endpoint, "Channel", "ChangeChannelByNumber", {
                "major_number": int(parameters["major"]),
                "minor_number": int(parameters.get("minor", 0)),
            }
        raise ValueError(f"unsupported SimuHome action: {action}")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("SimuHome response is not an object")
        status = payload.get("status", {})
        if isinstance(status, dict) and int(status.get("code", 200)) >= 400:
            error = payload.get("error") or {}
            raise RuntimeError(str(error.get("detail") or status.get("message") or "SimuHome request failed"))
        return payload.get("data")

    def _device_attributes(self, device_id: str) -> dict[str, Any]:
        try:
            return dict(self._devices[device_id]["attributes"])
        except KeyError as exc:
            raise ValueError(f"unknown SimuHome device: {device_id}") from exc

    def _record_tool(
        self,
        tool: str,
        params: dict[str, Any],
        *,
        ok: bool,
        status_code: int | None,
        error_type: str | None = None,
    ) -> None:
        self.tool_invocations.append({
            "tool": tool,
            "params": params,
            "outcome": {"ok": ok, "status_code": status_code, "error_type": error_type},
        })


def _episode_devices(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    devices: dict[str, dict[str, Any]] = {}
    for room_id, room in (config.get("rooms") or {}).items():
        for raw in room.get("devices", []):
            device = dict(raw)
            device["location"] = room_id
            device["attributes"] = dict(device.get("attributes") or {})
            devices[str(device["device_id"])] = device
    return devices


def _device_capability(device: dict[str, Any]) -> dict[str, Any]:
    attrs = device["attributes"]
    actions: list[dict[str, Any]] = []
    properties: list[str] = []

    def add(name: str, schema: dict[str, Any], *state_properties: str) -> None:
        actions.append({"name": name, "input_schema": schema})
        properties.extend(state_properties)

    if _find_key(attrs, "OnOff.OnOff"):
        add("turnOn", _EMPTY_SCHEMA, "on")
        add("turnOff", _EMPTY_SCHEMA, "on")
    if _find_key(attrs, "LevelControl.CurrentLevel"):
        add("setBrightness", _integer_schema("brightness", 0, 100), "brightness")
    if _find_key(attrs, "FanControl.PercentSetting"):
        add("setSpeed", _integer_schema("speed", 0, 100), "speed")
    if _find_key(attrs, "Thermostat.SystemMode"):
        add("setMode", {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": list(_MODE_TO_MATTER)}},
            "required": ["mode"],
            "additionalProperties": False,
        }, "mode")
    if _temperature_key(attrs):
        add("setTemperature", _number_schema("temperature", -40, 95), "targetTemp")
    if _find_key(attrs, "WindowCovering.TargetPositionLiftPercent100ths"):
        add("setPosition", _number_schema("position", 0, 100), "position")
    if _find_key(attrs, "OperationalState.OperationalState"):
        for action in _OPERATION_TO_STATE:
            add(action, _EMPTY_SCHEMA, "operationalState")
    if _find_key(attrs, "LaundryDryerControls.SelectedDrynessLevel"):
        add("setDrynessLevel", _integer_schema("level", 0, 10), "drynessLevel")
    if _find_key(attrs, "LaundryWasherMode.CurrentMode"):
        add("setWasherMode", _integer_schema("mode", 0, 20), "washerMode")
    if _find_key(attrs, "Channel.CurrentChannel"):
        add("setChannel", {
            "type": "object",
            "properties": {"major": {"type": "integer", "minimum": 0}, "minor": {"type": "integer", "minimum": 0}},
            "required": ["major"],
            "additionalProperties": False,
        }, "channel")
    device_type = str(device.get("device_type", "device"))
    return {
        "device_id": str(device["device_id"]),
        "title": _device_title(str(device["device_id"]), device_type),
        "location": str(device["location"]),
        "device_type": device_type,
        "location_aliases": [str(device["location"]).replace("_", " ")],
        "aliases": _device_aliases(str(device["device_id"]), device_type, str(device["location"])),
        "actions": actions,
        "properties": sorted(set(properties)),
    }


def _device_title(device_id: str, device_type: str) -> str:
    ordinal = device_id.rsplit("_", 1)[-1]
    names = {
        "on_off_light": "Light",
        "dimmable_light": "Dimmer Light",
        "window_covering_controller": "Blinds",
        "air_conditioner": "AC",
        "laundry_dryer": "Dryer",
        "laundry_washer": "Washer",
    }
    name = names.get(device_type, device_type.replace("_", " ").title())
    return f"{name} {ordinal} ({device_type})"


def _device_aliases(device_id: str, device_type: str, location: str) -> list[str]:
    ordinal = device_id.rsplit("_", 1)[-1]
    room = location.replace("_", " ")
    names = {
        "on_off_light": "light",
        "dimmable_light": "dimmer light",
        "window_covering_controller": "blinds",
        "air_conditioner": "AC",
        "laundry_dryer": "dryer",
        "laundry_washer": "washer",
    }
    name = names.get(device_type, device_type.replace("_", " "))
    return [f"{room} {name} {ordinal}", f"{name} {ordinal}"]


def _find_key(attrs: dict[str, Any], suffix: str) -> str | None:
    return next((key for key in attrs if key.endswith(suffix)), None)


def _find_attribute(attrs: dict[str, Any], suffix: str) -> Any:
    key = _find_key(attrs, suffix)
    return attrs.get(key) if key else None


def _attribute_endpoint(attrs: dict[str, Any], suffix: str) -> int:
    key = _find_key(attrs, suffix)
    if key is None:
        raise ValueError(f"device does not expose Matter attribute {suffix}")
    return int(key.split(".", 1)[0])


def _temperature_key(attrs: dict[str, Any]) -> str | None:
    return (
        _find_key(attrs, "TemperatureControl.TemperatureSetpoint")
        or _find_key(attrs, "Thermostat.OccupiedCoolingSetpoint")
        or _find_key(attrs, "Thermostat.OccupiedHeatingSetpoint")
    )


def _temperature_target(attrs: dict[str, Any]) -> Any:
    mode = _find_attribute(attrs, "Thermostat.SystemMode")
    if mode == _MODE_TO_MATTER["heat"]:
        value = _find_attribute(attrs, "Thermostat.OccupiedHeatingSetpoint")
        if value is not None:
            return value
    value = _find_attribute(attrs, "Thermostat.OccupiedCoolingSetpoint")
    if value is not None:
        return value
    return _find_attribute(attrs, "TemperatureControl.TemperatureSetpoint")


__all__ = ["SimuHomeBinding"]
