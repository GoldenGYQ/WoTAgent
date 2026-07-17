"""Structured LLM adapters for bounded hybrid Butler and Health decisions."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ...prompting import context_message, profile_for, prompt_system
from ...telemetry import InMemoryTraceStore, extract_usage_with_cost
from ..models import (
    AgentFinding,
    GoalEvaluation,
    GoalPlan,
    HealthAgentDecision,
    RiskEvent,
)


GOAL_PLAN_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate_goal_plan",
        "description": "Create a bounded goal and professional-agent delegations",
        "parameters": GoalPlan.model_json_schema(),
    },
}

GOAL_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "evaluate_goal",
        "description": "Evaluate evidence and decide whether another delegation is needed",
        "parameters": GoalEvaluation.model_json_schema(),
    },
}

HEALTH_DECISION_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "decide_health_step",
        "description": "Choose one authorized health resource or finish with a finding",
        "parameters": HealthAgentDecision.model_json_schema(),
    },
}


class StructuredGoalPlanner:
    def __init__(
        self,
        runnable: Any,
        *,
        timeout_seconds: float = 12.0,
        trace_store: InMemoryTraceStore | None = None,
    ) -> None:
        self.runnable = runnable
        self.timeout_seconds = timeout_seconds
        self.trace_store = trace_store

    async def __call__(
        self,
        event: RiskEvent,
        available_agents: list[str],
    ) -> GoalPlan:
        task_id = str(event.context.get("task_id", ""))
        messages = [
            SystemMessage(content=prompt_system(
                profile_for("eldercare_butler"),
                "Goal planning contract: create a goal, verifiable success criteria, and "
                "investigation-first delegations. Select agents only from available_agents. "
                "Do not name MCP tools or bypass Policy. Medical hard alerts must remain active.",
            )),
            HumanMessage(content=context_message("goal_context", {
                "event": event.model_dump(mode="json"),
                "available_agents": available_agents,
            })),
        ]
        result = await asyncio.wait_for(
            self.runnable.ainvoke(messages),
            timeout=self.timeout_seconds,
        )
        await _record_model(
            self.trace_store,
            task_id,
            "butler_goal_planner",
            "butler_goal_plan",
            result,
        )
        plan = GoalPlan.model_validate(_tool_arguments(result, "generate_goal_plan"))
        invalid = [
            item.agent for item in plan.delegations
            if item.agent not in available_agents
        ]
        if invalid:
            raise ValueError(f"goal plan selected unavailable agents: {sorted(set(invalid))}")
        return plan


class StructuredGoalEvaluator:
    def __init__(
        self,
        runnable: Any,
        *,
        timeout_seconds: float = 12.0,
        trace_store: InMemoryTraceStore | None = None,
    ) -> None:
        self.runnable = runnable
        self.timeout_seconds = timeout_seconds
        self.trace_store = trace_store

    async def __call__(
        self,
        goal_plan: GoalPlan,
        findings: list[AgentFinding],
        event: RiskEvent,
        iteration: int,
        available_agents: list[str],
    ) -> GoalEvaluation:
        task_id = str(event.context.get("task_id", ""))
        messages = [
            SystemMessage(content=prompt_system(
                profile_for("eldercare_butler"),
                "Goal evaluation contract: compare AgentFinding evidence with every success "
                "criterion. If evidence is insufficient, create only the minimum next "
                "delegations. Recommendations are not execution authorization. Never mark a "
                "device criterion complete from an unverified tool result.",
            )),
            HumanMessage(content=context_message("goal_evaluation_context", {
                "goal_plan": goal_plan.model_dump(mode="json"),
                "findings": [item.model_dump(mode="json") for item in findings],
                "event": event.model_dump(mode="json"),
                "iteration": iteration,
                "available_agents": available_agents,
            })),
        ]
        result = await asyncio.wait_for(
            self.runnable.ainvoke(messages),
            timeout=self.timeout_seconds,
        )
        await _record_model(
            self.trace_store,
            task_id,
            "butler_goal_evaluator",
            "butler_goal_evaluation",
            result,
        )
        evaluation = GoalEvaluation.model_validate(
            _tool_arguments(result, "evaluate_goal")
        )
        invalid = [
            item.agent for item in evaluation.next_delegations
            if item.agent not in available_agents
        ]
        if invalid:
            raise ValueError(f"goal evaluation selected unavailable agents: {sorted(set(invalid))}")
        return evaluation


class StructuredHealthDecisionMaker:
    def __init__(
        self,
        runnable: Any,
        *,
        timeout_seconds: float = 12.0,
        trace_store: InMemoryTraceStore | None = None,
        task_id_getter: Any | None = None,
    ) -> None:
        self.runnable = runnable
        self.timeout_seconds = timeout_seconds
        self.trace_store = trace_store
        self.task_id_getter = task_id_getter

    async def __call__(self, context: dict[str, Any]) -> HealthAgentDecision:
        messages = [
            SystemMessage(content=prompt_system(
                profile_for("health_agent"),
                "Observe-Reason-Act contract: choose exactly one name from available_tools or "
                "finish. Use existing evidence before requesting more data. Check freshness "
                "before trusting a summary, inspect latest values when stale, and inspect trend "
                "when an abnormal value requires temporal evidence. Do not diagnose or recommend "
                "medication. A finish finding must be concise plain text, normally under 180 "
                "Chinese characters. Do not produce Markdown tables, normal-range comparisons, "
                "disease labels, or new severity judgments. Report only observed values, source "
                "risk labels, freshness, trend availability, and unresolved data gaps.",
            )),
            HumanMessage(content=context_message("health_loop_context", context)),
        ]
        result = await asyncio.wait_for(
            self.runnable.ainvoke(messages),
            timeout=self.timeout_seconds,
        )
        task_id = (
            str(self.task_id_getter(context))
            if callable(self.task_id_getter)
            else str(context.get("task_id", ""))
        )
        await _record_model(
            self.trace_store,
            task_id,
            "health_agent",
            "health_step_decision",
            result,
        )
        decision = HealthAgentDecision.model_validate(
            _tool_arguments(result, "decide_health_step")
        )
        if decision.tool_name and decision.tool_name not in context["available_tools"]:
            raise ValueError(f"health tool '{decision.tool_name}' is outside AgentSpec")
        return decision


def _tool_arguments(result: Any, expected_name: str) -> dict[str, Any]:
    calls = getattr(result, "tool_calls", None) or []
    if calls:
        call = calls[0]
        if call.get("name") != expected_name:
            raise ValueError(f"unexpected structured tool: {call.get('name')}")
        arguments = call.get("args", {})
        return json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
    raw = (getattr(result, "additional_kwargs", None) or {}).get("tool_calls", [])
    if not raw:
        raise ValueError(f"model returned no {expected_name} tool call")
    function = raw[0].get("function", {})
    if function.get("name") != expected_name:
        raise ValueError(f"unexpected structured tool: {function.get('name')}")
    arguments = function.get("arguments", "{}")
    return json.loads(arguments) if isinstance(arguments, str) else dict(arguments)


async def _record_model(
    store: InMemoryTraceStore | None,
    task_id: str,
    node: str,
    purpose: str,
    result: Any,
) -> None:
    if store is None or not task_id:
        return
    await store.record(
        task_id=task_id,
        type="model.request.completed",
        node=node,
        data={
            "purpose": purpose,
            "prompt_version": "goal-driven-v1",
            **extract_usage_with_cost(result),
        },
    )


__all__ = [
    "GOAL_EVALUATION_SCHEMA",
    "GOAL_PLAN_SCHEMA",
    "HEALTH_DECISION_SCHEMA",
    "StructuredGoalEvaluator",
    "StructuredGoalPlanner",
    "StructuredHealthDecisionMaker",
]
