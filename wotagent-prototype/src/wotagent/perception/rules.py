"""Rule models for the perception engine -- using Pydantic for validation."""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field


class Condition(BaseModel):
    """A condition that evaluates a device property against a threshold."""
    device_id: str = Field(..., description="Target device ID")
    property_name: str = Field(..., description="Property to evaluate, e.g. 'currentTemperature'")
    operator: Literal["gt", "lt", "gte", "lte", "eq", "ne"] = Field(..., description="Comparison operator")
    value: float | int | str | bool = Field(..., description="Threshold value to compare against")

    def evaluate(self, prop_value: Any) -> bool:
        """Evaluate the condition against an actual property value."""
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

    model_config = {"frozen": False}


class RuleAction(BaseModel):
    """Action to take when a rule condition is met."""
    intent: Literal["control", "query"] = "control"
    steps: list[dict[str, Any]] = Field(default_factory=list, description="Action steps to execute")


class EnvironmentRule(BaseModel):
    """An automated perception rule with cooldown and enable/disable support."""
    name: str = Field(..., description="Unique rule name, e.g. 'high_temperature'")
    description: str = Field(..., description="Human-readable rule description")
    condition: Condition = Field(..., description="Trigger condition")
    action: RuleAction = Field(default_factory=RuleAction, description="Action to execute")
    cooldown_seconds: int = Field(default=300, description="Minimum seconds between triggers")
    enabled: bool = Field(default=True, description="Whether the rule is active")
    last_triggered: float = Field(default=0.0, description="Timestamp of last trigger (unix seconds)")

    def is_ready(self) -> bool:
        """Check whether enough time has passed since the last trigger."""
        return time.time() - self.last_triggered >= self.cooldown_seconds

    def mark_triggered(self) -> None:
        """Record the current time as last trigger."""
        self.last_triggered = time.time()

    model_config = {"frozen": False}


def default_rules() -> list[EnvironmentRule]:
    """Return the default set of perception rules."""
    return [
        EnvironmentRule(
            name="high_temperature",
            description="High temperature -- auto-start AC cooling",
            condition=Condition(
                device_id="ac-001",
                property_name="currentTemperature",
                operator="gt",
                value=32,
            ),
            action=RuleAction(
                intent="control",
                steps=[
                    {
                        "action": "control_device",
                        "target": "ac-001",
                        "params": {"action": "setMode", "parameters": {"mode": "cool"}},
                    },
                    {
                        "action": "control_device",
                        "target": "ac-001",
                        "params": {"action": "setTemperature", "parameters": {"temp": 26}},
                    },
                ],
            ),
            cooldown_seconds=600,
        ),
        EnvironmentRule(
            name="low_humidity",
            description="Low humidity -- auto-start humidifier",
            condition=Condition(
                device_id="humidifier-001",
                property_name="currentHumidity",
                operator="lt",
                value=30,
            ),
            action=RuleAction(
                intent="control",
                steps=[
                    {
                        "action": "control_device",
                        "target": "humidifier-001",
                        "params": {"action": "turnOn", "parameters": {"on": True}},
                    },
                ],
            ),
            cooldown_seconds=600,
        ),
    ]


__all__ = ["Condition", "EnvironmentRule", "RuleAction", "default_rules"]
