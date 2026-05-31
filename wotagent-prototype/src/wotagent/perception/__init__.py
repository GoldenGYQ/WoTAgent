"""Perception module — rule-based environment monitoring.

All device state access goes through MCP to the simulator project.
No direct imports from ``wot-device-simulator``.

Provides:
- ``EnvironmentRule`` / ``Condition`` / ``RuleAction`` — rule model
- ``PerceptionEngine`` — polling, rule evaluation, event emission
- ``get_perception_engine`` — global singleton
"""

from __future__ import annotations

from .engine import PerceptionEngine, get_perception_engine
from .rules import Condition, EnvironmentRule, RuleAction, default_rules

__all__ = [
    "Condition",
    "EnvironmentRule",
    "RuleAction",
    "PerceptionEngine",
    "default_rules",
    "get_perception_engine",
]
