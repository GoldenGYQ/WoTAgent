"""Perception engine — background environment monitoring and rule evaluation.

Architecture::

                    ┌──────────────────────────────────┐
                    │       PerceptionEngine            │
                    │  ┌──────────┐   ┌──────────────┐  │
                    │  │ drift()  │   │ evaluate()   │  │
                    │  │ (simulate│   │ (match rules) │  │
                    │  │  change) │   └──────┬───────┘  │
                    │  └──────────┘          │          │
                    │         │              ▼          │
                    │         │      ┌──────────────┐  │
                    │         │      │ emit Plan via │  │
                    │         │      │  EventBus     │  │
                    │         │      └──────────────┘  │
                    └─────────┼────────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  Executor → Obs  │ (skips Planner)
                    └──────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from ..events import Event, get_bus
from . import rules as perception_rules

_SIM_SRC = Path(__file__).resolve().parents[4] / "wot-device-simulator" / "src"
if _SIM_SRC.is_dir() and str(_SIM_SRC) not in sys.path:
    sys.path.insert(0, str(_SIM_SRC))

from wot_device_simulator.simulator import DeviceStateStore  # noqa: E402  # type: ignore[reportMissingImports]

EnvironmentRule = Any
default_rules = perception_rules.default_rules

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Perception engine
# ---------------------------------------------------------------------------

class PerceptionEngine:
    """Background engine that polls device state and evaluates rules.

    Usage::

        engine = PerceptionEngine(polling_interval=30)
        engine.start()       # launch background asyncio task
        await engine.poll_once()  # manual poll
        summary = engine.get_context()  # text summary for LLM injection
        engine.stop()        # cancel background task
    """

    def __init__(
        self,
        rules: list[EnvironmentRule] | None = None,
        polling_interval: int = 60,
    ) -> None:
        self.rules: list[EnvironmentRule] = rules or default_rules()
        self.polling_interval = polling_interval
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._stats: dict[str, int] = {"polls": 0, "triggers": 0}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling loop (idempotent)."""
        if self._running:
            return
        self._running = True
        DeviceStateStore.initialize()
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self._task = loop.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Perception engine stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Background loop — drift, evaluate, sleep."""
        logger.info(
            "Perception engine started | interval=%ds rules=%d",
            self.polling_interval,
            len(self.rules),
        )
        try:
            while self._running:
                DeviceStateStore.drift()
                await self._evaluate_rules()
                self._stats["polls"] += 1
                await asyncio.sleep(self.polling_interval)
        except asyncio.CancelledError:
            pass

    async def poll_once(self) -> list[dict[str, Any]]:
        """Drift state and evaluate rules once. Returns triggered plans."""
        DeviceStateStore.drift()
        self._stats["polls"] += 1
        return await self._evaluate_rules()

    async def _evaluate_rules(self) -> list[dict[str, Any]]:
        """Check all rules; emit events for triggered ones. Return plan list."""
        bus = get_bus()
        triggered: list[dict[str, Any]] = []

        for rule in self.rules:
            if not rule.enabled:
                continue

            prop_val = DeviceStateStore.get_property(
                rule.condition.device_id,
                rule.condition.property_name,
            )
            if prop_val is None:
                continue

            if rule.condition.evaluate(prop_val) and rule.is_ready():
                rule.mark_triggered()
                plan = {
                    "intent": rule.action.intent,
                    "rationale": rule.description,
                    "steps": rule.action.steps,
                    "triggered_by": rule.name,
                }
                triggered.append(plan)
                self._stats["triggers"] += 1

                await bus.emit(Event(
                    source="wotagent/perception",
                    type="wot.perception.rule_triggered",
                    data={
                        "rule": rule.name,
                        "description": rule.description,
                        "device_id": rule.condition.device_id,
                        "property": rule.condition.property_name,
                        "value": prop_val,
                        "threshold": rule.condition.value,
                        "plan": plan,
                    },
                ))
                logger.info(
                    "Rule triggered: %s (%s = %s %s)",
                    rule.name,
                    rule.condition.property_name,
                    prop_val,
                    rule.condition.operator,
                )

        return triggered

    # ------------------------------------------------------------------
    # LLM context
    # ------------------------------------------------------------------

    def get_context(self, max_length: int = 500) -> str:
        """Return a short environment summary for LLM prompt injection."""
        summary = DeviceStateStore.format_summary()
        if len(summary) > max_length:
            summary = summary[:max_length] + "..."
        if not summary:
            return ""
        return f"[Current environment state]\n{summary}"

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def get_rule(self, name: str) -> EnvironmentRule | None:
        for r in self.rules:
            if r.name == name:
                return r
        return None

    def toggle_rule(self, name: str, enabled: bool | None = None) -> bool:
        """Enable/disable a rule. Toggle if *enabled* is None."""
        rule = self.get_rule(name)
        if rule is None:
            return False
        if enabled is not None:
            rule.enabled = enabled
        else:
            rule.enabled = not rule.enabled
        return True


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_engine: PerceptionEngine | None = None


def get_perception_engine(
    polling_interval: int = 60,
    auto_start: bool = True,
) -> PerceptionEngine:
    """Return the global ``PerceptionEngine`` singleton."""
    global _engine
    if _engine is None:
        _engine = PerceptionEngine(polling_interval=polling_interval)
        if auto_start:
            _engine.start()
    return _engine
