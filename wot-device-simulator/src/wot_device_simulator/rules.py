from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class Condition:
    device_id: str
    property_name: str
    operator: Literal["gt", "lt", "gte", "lte", "eq", "ne"]
    value: float | int | str | bool

    def evaluate(self, prop_value: Any) -> bool:
        if not isinstance(prop_value, (int, float)):
            return False
        v = float(prop_value)
        t = float(self.value)
        ops = {
            "gt": lambda: v > t,
            "lt": lambda: v < t,
            "gte": lambda: v >= t,
            "lte": lambda: v <= t,
            "eq": lambda: v == t,
            "ne": lambda: v != t,
        }
        return ops.get(self.operator, lambda: False)()


@dataclass
class RuleAction:
    intent: Literal["control", "query"] = "control"
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EnvironmentRule:
    name: str
    description: str
    condition: Condition
    action: RuleAction
    cooldown_seconds: int = 300
    enabled: bool = True
    last_triggered: float = 0.0

    def is_ready(self) -> bool:
        return time.time() - self.last_triggered >= self.cooldown_seconds

    def mark_triggered(self) -> None:
        self.last_triggered = time.time()


def default_rules() -> list[EnvironmentRule]:
    return [
        EnvironmentRule(
            name="high_temperature",
            description="高温自动开启空调制冷",
            condition=Condition("ac-001", "currentTemperature", "gt", 32),
            action=RuleAction(
                intent="control",
                steps=[
                    {
                        "action": "control_device",
                        "target": "ac-001",
                        "params": {
                            "action": "setMode",
                            "parameters": {"mode": "cool"},
                        },
                    },
                    {
                        "action": "control_device",
                        "target": "ac-001",
                        "params": {
                            "action": "setTemperature",
                            "parameters": {"temp": 26},
                        },
                    },
                ],
            ),
            cooldown_seconds=600,
        ),
        EnvironmentRule(
            name="low_humidity",
            description="低湿度自动开启加湿器",
            condition=Condition("humidifier-001", "currentHumidity", "lt", 30),
            action=RuleAction(
                intent="control",
                steps=[
                    {
                        "action": "control_device",
                        "target": "humidifier-001",
                        "params": {
                            "action": "turnOn",
                            "parameters": {"on": True},
                        },
                    },
                ],
            ),
            cooldown_seconds=600,
        ),
    ]
