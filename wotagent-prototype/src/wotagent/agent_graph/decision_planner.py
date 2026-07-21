"""Decision-mode planner wrapper; semantic fast paths are rule-mode only."""

from __future__ import annotations

from typing import Any

from ..agentic_config import AgentDecisionMode
from ..domain import PlanStep, TaskPlan
from ..telemetry import InMemoryTraceStore
from .planner import select_capability_context


class DecisionModeTaskPlanner:
    def __init__(
        self,
        primary: Any,
        *,
        mode: AgentDecisionMode = "hybrid",
        trace_store: InMemoryTraceStore | None = None,
    ) -> None:
        self.primary = primary
        self.mode = mode
        self.trace_store = trace_store

    async def __call__(self, state: dict[str, Any]) -> TaskPlan:
        fast = _simple_device_plan(state)
        if fast is not None and self.mode == "rule":
            task_id = state.get("task_id", "")
            if self.trace_store is not None and task_id:
                await self.trace_store.record(
                    task_id=task_id,
                    type="agent.fast_path.selected",
                    node="rule_planner",
                    data={
                        "mode": self.mode,
                        "reason": "single explicit target and power action",
                        "plan": fast.model_dump(mode="json"),
                    },
                )
            return fast
        return await self.primary(state)


def _simple_device_plan(state: dict[str, Any]) -> TaskPlan | None:
    message = " ".join(str(state.get("message", "")).lower().split())
    action = ""
    if any(term in message for term in ("打开", "开启", "turn on", "switch on")):
        action = "turnOn"
    elif any(term in message for term in ("关闭", "关掉", "turn off", "switch off")):
        action = "turnOff"
    if not action:
        return None
    if any(term in message for term in ("所有", "全部", "其它", "其他", "然后", "并且", "除了")):
        return None
    devices = state.get("context", {}).get("devices", [])
    if not devices:
        return None
    selected = select_capability_context(list(devices), message, max_devices=2)
    if len(selected) != 1:
        return None
    device = selected[0]
    if action not in {item.get("name") for item in device.get("actions", [])}:
        return None
    return TaskPlan(
        intent="control",
        summary=f"{action} {device.get('title') or device['device_id']}",
        assumptions=["规则快速通道仅处理单一明确设备的开关动作"],
        steps=[PlanStep(
            id="fast-path-1",
            kind="action",
            device_id=str(device["device_id"]),
            action=action,
        )],
    )


__all__ = ["DecisionModeTaskPlanner"]
