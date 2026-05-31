"""Perception engine — background environment monitoring and rule evaluation.

All device state queries go through MCP to the simulator project.
No direct imports from ``wot-device-simulator``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..events import Event, get_bus
from . import rules as perception_rules

EnvironmentRule = Any
default_rules = perception_rules.default_rules

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Perception engine
# ---------------------------------------------------------------------------


class PerceptionEngine:
    """Background engine that polls device state and evaluates rules.

    All state access is via MCP — no direct simulator dependency.

    Usage::

        engine = PerceptionEngine(polling_interval=30)
        engine.start()       # launch background asyncio task
        await engine.poll_once()  # manual poll
        summary = await engine.get_context()  # text summary for LLM injection
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
        self._cached_states: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling loop (idempotent)."""
        if self._running:
            return
        self._running = True
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
    # MCP-backed state access
    # ------------------------------------------------------------------

    async def _fetch_all_states(self) -> dict[str, dict[str, Any]]:
        """Fetch all device runtime states via MCP and update local cache."""
        try:
            from ..tools.mcp_client import get_mcp_client

            client = await get_mcp_client()
            result = await client.call_tool("query_all_states", {})
            text = result.get("text", "")
            if text:
                import json

                states = json.loads(text)
                if isinstance(states, dict):
                    self._cached_states = states
                    return states
        except Exception as exc:
            logger.debug("MCP query_all_states failed (will retry): %s", exc)
        return self._cached_states

    async def _trigger_drift(self) -> None:
        """Trigger one drift cycle via MCP."""
        try:
            from ..tools.mcp_client import get_mcp_client

            client = await get_mcp_client()
            await client.call_tool("drift", {})
        except Exception as exc:
            logger.debug("MCP drift failed (will retry): %s", exc)

    async def get_all_states(self) -> dict[str, dict[str, Any]]:
        """Return all device runtime states (public API, e.g. for HTTP endpoints)."""
        return await self._fetch_all_states()

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Background loop — drift via MCP, evaluate rules, sleep."""
        logger.info(
            "Perception engine started | interval=%ds rules=%d",
            self.polling_interval,
            len(self.rules),
        )
        try:
            while self._running:
                await self._trigger_drift()
                await self._evaluate_rules()
                self._stats["polls"] += 1
                await asyncio.sleep(self.polling_interval)
        except asyncio.CancelledError:
            pass

    async def poll_once(self) -> list[dict[str, Any]]:
        """Drift state and evaluate rules once. Returns triggered plans."""
        await self._trigger_drift()
        self._stats["polls"] += 1
        return await self._evaluate_rules()

    async def _evaluate_rules(self) -> list[dict[str, Any]]:
        """Check all rules against current device states; emit events for triggered ones."""
        states = await self._fetch_all_states()
        bus = get_bus()
        triggered: list[dict[str, Any]] = []

        for rule in self.rules:
            if not rule.enabled:
                continue

            device_state = states.get(rule.condition.device_id, {})
            prop_val = device_state.get(rule.condition.property_name)
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

    async def get_context(self, max_length: int = 500) -> str:
        """Return a short environment summary for LLM prompt injection.

        Fetches fresh state via MCP. If MCP is unavailable, returns cached data
        or empty string.
        """
        states = await self._fetch_all_states()
        parts: list[str] = []
        for device_id, state in states.items():
            items = ", ".join(f"{k}={v}" for k, v in state.items())
            parts.append(f"{device_id}({items})")
        summary = " | ".join(parts)
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
