"""Adapter for the public HomeBench smart-home instruction dataset."""

from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import ValidationError, validate

from ..wot import get_device_by_id
from .models import EvalAction, EvalCase


_ROOM_DEVICE_IDS: dict[tuple[str, str], str] = {
    ("living_room", "light"): "light-001",
    ("living_room", "air_conditioner"): "ac-001",
    ("master_bedroom", "light"): "light-002",
    ("master_bedroom", "air_conditioner"): "ac-002",
    ("guest_bedroom", "light"): "light-003",
    ("guest_bedroom", "air_conditioner"): "ac-003",
    ("study_room", "light"): "light-004",
    ("kitchen", "light"): "light-005",
}

_OPERATION_MAP: dict[tuple[str, str], tuple[str, str | None]] = {
    ("light", "turn_on"): ("turnOn", None),
    ("light", "turn_off"): ("turnOff", None),
    ("light", "set_brightness"): ("setBrightness", "brightness"),
    ("air_conditioner", "turn_on"): ("turnOn", None),
    ("air_conditioner", "set_temperature"): ("setTemperature", "temperature"),
    ("air_conditioner", "set_mode"): ("setMode", "mode"),
}

_CALL = re.compile(r"([a-z0-9_]+)\.([a-z0-9_]+)\.([a-z0-9_]+)\((.*?)\)")
_RELATIVE_TERMS = ("increase", "decrease", "raise", "lower", "reduce", "by ")


class HomeBenchAdapter:
    """Select the subset whose rooms, devices, and actions exist in WoTAgent."""

    def load(self, path: str | Path) -> list[EvalCase]:
        cases: list[EvalCase] = []
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                raw = json.loads(line)
                case = self.convert(raw)
                if case is not None:
                    cases.append(case)
        return cases

    def convert(self, raw: dict) -> EvalCase | None:
        category = str(raw.get("type", ""))
        if category != "normal" and not category.endswith("_normal"):
            return None
        instruction = str(raw.get("input", ""))
        if any(term in instruction.lower() for term in _RELATIVE_TERMS):
            return None
        output = str(raw.get("output", "")).replace("'''", "")
        if "error_input" in output:
            return None
        actions: list[EvalAction] = []
        calls = [item.strip() for item in output.split(",") if item.strip()]
        if not calls:
            return None
        for call in calls:
            match = _CALL.fullmatch(call)
            if match is None:
                return None
            room, device, operation, argument = match.groups()
            device_id = _ROOM_DEVICE_IDS.get((room, device))
            mapped = _OPERATION_MAP.get((device, operation))
            if device_id is None or mapped is None:
                return None
            action, parameter = mapped
            parameters = {}
            if parameter is not None:
                value = self._parse_argument(argument)
                if value is None:
                    return None
                if action == "setMode" and value == "fan_only":
                    value = "fan"
                parameters[parameter] = value
            td = get_device_by_id(device_id)
            declared = next((item for item in td.actions if item.name == action), None) if td else None
            if declared is None:
                return None
            try:
                validate(instance=parameters, schema=declared.input_schema)
            except ValidationError:
                return None
            actions.append(EvalAction(device_id=device_id, action=action, input=parameters))
        return EvalCase(
            case_id=str(raw["id"]),
            source="HomeBench-adapted",
            category=category,
            instruction=instruction,
            expected_actions=actions,
        )

    @staticmethod
    def _parse_argument(value: str):
        value = value.strip().strip("'\"")
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value
