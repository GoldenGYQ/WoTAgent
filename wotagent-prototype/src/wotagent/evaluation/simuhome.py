"""SimuHome episode alignment without importing or modifying the benchmark."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CanonicalAction:
    action: str
    parameters: dict[str, Any]


@dataclass
class SimuHomeAlignment:
    query_type: str
    case: str
    temporal: bool = False
    assertion_count: int = 0
    mapped_assertion_count: int = 0
    unsupported_attributes: list[str] = field(default_factory=list)
    required_features: set[str] = field(default_factory=set)

    @property
    def td_action_coverage(self) -> float:
        if self.assertion_count == 0:
            return 1.0
        return self.mapped_assertion_count / self.assertion_count

    @property
    def td_action_ready(self) -> bool:
        """True when the episode's action goals translate, not when its backend is connected."""
        return self.required_features <= {"device_control"} and self.td_action_coverage == 1.0


def translate_matter_assertion(attribute: str, value: Any) -> CanonicalAction | None:
    """Translate common Matter benchmark assertions to WoTAgent action semantics."""
    if attribute.endswith(".OnOff.OnOff"):
        return CanonicalAction("turnOn" if bool(value) else "turnOff", {})
    if attribute.endswith(".LevelControl.CurrentLevel"):
        return CanonicalAction("setBrightness", {"brightness": value})
    if attribute.endswith(".FanControl.PercentSetting"):
        return CanonicalAction("setSpeed", {"speed": value})
    if attribute.endswith(".Thermostat.OccupiedCoolingSetpoint"):
        return CanonicalAction("setTemperature", {"temperature": _matter_temperature(value)})
    if attribute.endswith(".Thermostat.OccupiedHeatingSetpoint"):
        return CanonicalAction("setTemperature", {"temperature": _matter_temperature(value)})
    if attribute.endswith(".Thermostat.SystemMode"):
        modes = {0: "off", 1: "auto", 3: "cool", 4: "heat", 7: "fan", 8: "dry"}
        mode = modes.get(value)
        return CanonicalAction("setMode", {"mode": mode}) if mode is not None else None
    if attribute.endswith(".WindowCovering.TargetPositionLiftPercent100ths"):
        return CanonicalAction("setPosition", {"position": value / 100})
    if attribute.endswith(".TemperatureControl.TemperatureSetpoint"):
        return CanonicalAction("setTemperature", {"temperature": _matter_temperature(value)})
    if attribute.endswith(".LaundryWasherMode.CurrentMode"):
        return CanonicalAction("setWasherMode", {"mode": value})
    if attribute.endswith(".LaundryDryerControls.SelectedDrynessLevel"):
        return CanonicalAction("setDrynessLevel", {"level": value})
    if attribute.endswith(".MediaPlayback.CurrentState") and value == 0:
        return CanonicalAction("play", {})
    if attribute.endswith(".OperationalState.OperationalState") or attribute.endswith(
        ".RVCOperationalState.OperationalState"
    ):
        operations = {0: "stop", 1: "start", 2: "pause", 3: "resume"}
        action = operations.get(value)
        return CanonicalAction(action, {}) if action is not None else None
    return None


def _matter_temperature(value: Any) -> Any:
    if isinstance(value, (int, float)) and abs(value) >= 100:
        return value / 100
    return value


def analyze_simuhome_episode(payload: dict[str, Any]) -> SimuHomeAlignment:
    meta = payload.get("meta") or {}
    alignment = SimuHomeAlignment(
        query_type=str(meta.get("query_type", "")),
        case=str(meta.get("case", "")),
    )
    if alignment.query_type == "qt1":
        alignment.required_features.add("room_and_attribute_query")
    elif alignment.query_type == "qt2":
        alignment.required_features.update({"room_and_attribute_query", "environment_control"})
    elif alignment.query_type == "qt3":
        alignment.required_features.add("device_control")
    elif alignment.query_type.startswith("qt4"):
        alignment.required_features.update({"device_control", "temporal_workflow"})

    for goal in (payload.get("eval") or {}).get("goals", []):
        if goal.get("when"):
            alignment.temporal = True
        targets = goal.get("targets") if isinstance(goal, dict) else None
        assertion_groups = [target.get("asserts", []) for target in (targets or [])]
        if isinstance(goal, dict) and goal.get("asserts"):
            assertion_groups.append(goal["asserts"])
        for assertions in assertion_groups:
            for assertion in assertions:
                attribute = str(assertion.get("attribute", ""))
                alignment.assertion_count += 1
                if translate_matter_assertion(attribute, assertion.get("value")) is not None:
                    alignment.mapped_assertion_count += 1
                else:
                    alignment.unsupported_attributes.append(attribute)
    return alignment


def analyze_simuhome_benchmark(root: Path) -> dict[str, Any]:
    alignments = [
        analyze_simuhome_episode(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(root.glob("*.json"))
    ]
    query_types = Counter(item.query_type for item in alignments)
    unsupported = Counter(
        attribute for item in alignments for attribute in item.unsupported_attributes
    )
    total_assertions = sum(item.assertion_count for item in alignments)
    mapped_assertions = sum(item.mapped_assertion_count for item in alignments)
    return {
        "episodes": len(alignments),
        "query_types": dict(sorted(query_types.items())),
        "td_action_assertions": {
            "total": total_assertions,
            "mapped": mapped_assertions,
            "coverage": mapped_assertions / total_assertions if total_assertions else 1.0,
        },
        "td_action_ready_episodes": sum(item.td_action_ready for item in alignments),
        "simuhome_runtime_connected": False,
        "temporal_episodes": sum(item.temporal for item in alignments),
        "unsupported_attributes": dict(unsupported.most_common()),
        "required_features": sorted({feature for item in alignments for feature in item.required_features}),
    }


def evaluate_qt3_runtime(
    episode: dict[str, Any],
    final_state: dict[str, Any],
    tools_invoked: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply SimuHome qt3 required-action and exact final-state semantics."""
    required_results = []
    for required in episode.get("eval", {}).get("required_actions", []):
        found = any(
            item.get("tool") == required.get("tool")
            and _is_subset(required.get("params", {}), item.get("params", {}))
            for item in tools_invoked
        )
        required_results.append({**required, "invoked": found})

    target_results = []
    passed = 0
    total = 0
    for goal in episode.get("eval", {}).get("goals", []):
        differences = []
        for assertion in goal.get("asserts", []):
            total += 1
            actual = _read_device_attribute(final_state, goal["device_id"], assertion["attribute"])
            expected = assertion.get("value")
            ok = actual == expected
            passed += int(ok)
            if not ok:
                differences.append({
                    "attribute": assertion["attribute"],
                    "expected": expected,
                    "actual": actual,
                })
        target_results.append({
            "device_id": goal["device_id"],
            "ok": not differences,
            "diffs": differences,
        })

    required_ok = all(item["invoked"] for item in required_results)
    goals_ok = all(item["ok"] for item in target_results)
    return {
        "score": int(required_ok and goals_ok),
        "goal_success": goals_ok,
        "required_action_success": required_ok,
        "assertions_passed": passed,
        "assertions_total": total,
        "required_actions": required_results,
        "targets_eval": target_results,
    }


def _is_subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and isinstance(actual, dict):
        return all(key in actual and _is_subset(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list) and isinstance(actual, list):
        return all(item in actual for item in expected)
    return expected == actual


def _read_device_attribute(state: dict[str, Any], device_id: str, attribute: str) -> Any:
    for room in (state.get("rooms") or {}).values():
        for device in room.get("devices", []):
            if str(device.get("device_id")) != device_id:
                continue
            attrs = device.get("attributes") or {}
            if attribute in attrs:
                return attrs[attribute]
            parts = attribute.split(".")
            for index in range(len(parts) - 1, 1, -1):
                value = attrs.get(".".join(parts[:index]))
                for key in parts[index:]:
                    if isinstance(value, dict):
                        value = value.get(key)
                    else:
                        value = getattr(value, key, None)
                if value is not None:
                    return value
    return None


__all__ = [
    "CanonicalAction",
    "SimuHomeAlignment",
    "analyze_simuhome_benchmark",
    "analyze_simuhome_episode",
    "evaluate_qt3_runtime",
    "translate_matter_assertion",
]
