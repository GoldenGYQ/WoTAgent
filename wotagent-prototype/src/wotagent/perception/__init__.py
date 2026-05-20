"""Perception module — rule-based environment monitoring.

Provides:
- ``EnvironmentRule`` / ``Condition`` / ``RuleAction`` — rule model
- ``DeviceStateStore`` — simulated device state management
- ``PerceptionEngine`` — polling, rule evaluation, event emission
- ``get_perception_engine`` — global singleton
"""

from __future__ import annotations

from .engine import DeviceStateStore, PerceptionEngine, get_perception_engine
from .rules import Condition, EnvironmentRule, RuleAction, default_rules

__all__ = [
    "Condition",
    "EnvironmentRule",
    "RuleAction",
    "DeviceStateStore",
    "PerceptionEngine",
    "default_rules",
    "get_perception_engine",
]
