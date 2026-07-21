"""LangChain model adapter for the strongly typed LangGraph planner node."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jsonschema import ValidationError as JSONSchemaValidationError, validate
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from ..domain import TaskPlan
from ..prompting import (
    DEVICE_PLANNING_CONTRACT,
    AgentPromptProfile,
    compact_history,
    context_message,
    profile_for,
    prompt_system,
)
from ..telemetry import InMemoryTraceStore, extract_usage_with_cost
from ..wot import load_tds


_LOCATION_TERMS: dict[str, tuple[str, ...]] = {
    "living_room": ("客厅", "起居室", "living room", "parlor"),
    "bedroom1": ("主卧", "卧室1", "bedroom1", "master bedroom", "A-101", "A101"),
    "bedroom2": ("次卧", "卧室2", "bedroom2", "guest bedroom", "A-102", "A102"),
    "kitchen": ("厨房", "kitchen"),
    "study": ("书房", "study"),
    "bathroom1": ("主卫", "卫生间1", "bathroom1"),
    "bathroom2": ("客卫", "卫生间2", "bathroom2"),
}

_DEVICE_TERMS: dict[str, tuple[str, ...]] = {
    "light": ("灯", "照明", "light"),
    "ac": ("空调", "ac", "air conditioner"),
    "fan": ("风扇", "fan"),
    "exhaust": ("排风", "换气", "exhaust"),
    "humidifier": ("加湿器", "humidifier"),
    "purifier": ("净化器", "purifier"),
    "tv": ("电视", "tv"),
    "gas_sensor": ("燃气", "gas"),
    "health": ("手环", "床垫", "健康", "心率", "血氧", "wristband", "mattress"),
    "environment": ("环境传感器", "温湿度", "environment sensor"),
    "window_covering_controller": ("百叶窗", "窗帘", "blind", "blinds", "shade", "curtain"),
    "dimmable_light": ("调光灯", "dimmer", "dimmable light"),
    "laundry_dryer": ("烘干机", "dryer"),
    "laundry_washer": ("洗衣机", "washer"),
    "dishwasher": ("洗碗机", "dishwasher"),
    "refrigerator": ("冰箱", "refrigerator", "fridge"),
    "freezer": ("冷冻柜", "freezer"),
    "heat_pump": ("热泵", "heat pump"),
    "security": ("门锁", "门禁", "摄像头", "烟雾", "安防", "security"),
    "energy": ("电表", "能耗", "充电桩", "用电", "energy"),
    "housekeeping": ("扫地机器人", "清洁", "家务", "vacuum"),
    "device_ops": ("网关", "设备运维", "设备离线", "gateway"),
}


class ConditionalPlanReview(BaseModel):
    approved: bool
    observed_facts: list[str] = Field(default_factory=list)
    selected_branch: str = ""
    violations: list[str] = Field(default_factory=list)


CONDITIONAL_PLAN_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "review_conditional_plan",
        "description": "Check whether a candidate device action plan follows observed conditions",
        "parameters": ConditionalPlanReview.model_json_schema(),
    },
}


@dataclass
class StructuredConditionalPlanReviewer:
    runnable: Any
    trace_store: InMemoryTraceStore | None = None

    async def review(
        self,
        *,
        state: dict[str, Any],
        plan: TaskPlan,
        loop_context: dict[str, Any],
    ) -> ConditionalPlanReview:
        task_id = state.get("task_id", "")
        messages = [
            (
                "system",
                "You are a strict conditional-plan reviewer. Check the candidate action plan "
                "against the original objective and authoritative observations. Recompute counts, "
                "parity, comparisons, arithmetic, and branch activation. Reject any action from "
                "an inactive branch, any action unsupported by observations, or any plan that "
                "performs an unrequested parent action merely to activate a downstream condition. "
                "The candidate is only the next stage of a bounded Observe-Act loop, not the full "
                "remaining workflow. Do not reject a correct next-stage action merely because a "
                "downstream condition is intentionally deferred until that action is verified. "
                "A parent action explicitly required by the active branch is valid. For count "
                "arithmetic, a verified boolean device state contributes 1 when on and 0 when off "
                "if the user's expression combines that device with another count. Do not "
                "review RBAC or TD capability; those are separate deterministic layers. Return "
                "only the review_conditional_plan tool call.",
            ),
            (
                "human",
                context_message("conditional_plan_review", {
                    "original_objective": loop_context.get("original_objective", ""),
                    "observations": loop_context.get("observations", []),
                    "candidate_plan": plan.model_dump(mode="json"),
                }),
            ),
        ]
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="plan_reviewer",
                data={
                    "purpose": "conditional_plan_review",
                    "prompt_version": "conditional-review-v1",
                },
            )
        started = time.perf_counter()
        result = await self.runnable.ainvoke(messages)
        review = ConditionalPlanReview.model_validate(
            _extract_named_tool_arguments(result, "review_conditional_plan")
        )
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.completed",
                node="plan_reviewer",
                duration_ms=(time.perf_counter() - started) * 1000,
                data={
                    "purpose": "conditional_plan_review",
                    "prompt_version": "conditional-review-v1",
                    "approved": review.approved,
                    "selected_branch": review.selected_branch,
                    "violations": review.violations,
                    "model": _extract_model_name(result),
                    **extract_usage_with_cost(result),
                },
            )
        return review


def capability_context(message: str = "", max_devices: int = 8) -> list[dict[str, Any]]:
    """Return a compact, model-safe capability view instead of complete TDs."""
    all_devices = [
        {
            "device_id": td.device_id,
            "title": td.title,
            "location": td.location,
            "location_aliases": list(_LOCATION_TERMS.get(td.location, ())),
            "actions": [
                {"name": action.name, "input_schema": action.input_schema}
                for action in td.actions
            ],
            "properties": [prop.name for prop in td.properties],
        }
        for td in load_tds()
    ]
    return select_capability_context(all_devices, message, max_devices=max_devices)


def select_capability_context(
    all_devices: list[dict[str, Any]],
    message: str = "",
    *,
    max_devices: int = 8,
) -> list[dict[str, Any]]:
    """Select relevant capabilities regardless of whether TDs came from disk or MCP."""
    all_devices = [
        {
            **device,
            "location_aliases": sorted(
                set(device.get("location_aliases", []))
                | set(_LOCATION_TERMS.get(str(device.get("location", "")), ()))
            ),
        }
        for device in all_devices
    ]
    text = message.lower().strip()
    if not text:
        return all_devices
    locations = {
        location for location, terms in _LOCATION_TERMS.items()
        if any(term in text for term in terms)
    }
    device_types = {
        device_type for device_type, terms in _DEVICE_TERMS.items()
        if any(term in text for term in terms)
    }
    for device in all_devices:
        location = str(device.get("location", ""))
        location_text = location.replace("_", " ").lower()
        if location_text and location_text in text:
            locations.add(location)
        device_type = str(device.get("device_type", "")).lower()
        type_text = device_type.replace("_", " ")
        distinctive = type_text.rsplit(" ", 1)[-1]
        if type_text and (type_text in text or distinctive in text):
            device_types.add(device_type)
    if not locations and not device_types:
        return all_devices

    def matches(device: dict[str, Any]) -> bool:
        title = device["title"].lower()
        location_match = not locations or device["location"] in locations
        actual_type = str(device.get("device_type", "")).lower()
        type_match = not device_types or any(
            device_type in title
            or device_type == actual_type
            or (device_type == "ac" and actual_type == "air_conditioner")
            for device_type in device_types
        )
        return location_match and type_match

    selected = [device for device in all_devices if matches(device)]
    selected.sort(
        key=lambda device: max(
            (len(alias) for alias in device.get("aliases", []) if alias.lower() in text),
            default=0,
        ),
        reverse=True,
    )
    return selected[:max_devices] if selected else all_devices


@dataclass
class LangChainTaskPlanner:
    """Convert one forced Function Calling result into a validated TaskPlan."""

    runnable: Any
    trace_store: InMemoryTraceStore | None = None
    capability_provider: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None
    thinking_runnable: Any | None = None
    agent_profile: AgentPromptProfile | None = None
    plan_reviewer: StructuredConditionalPlanReviewer | None = None

    async def __call__(self, state: dict[str, Any]) -> TaskPlan:
        thinking = bool(state.get("thinking", False))
        if thinking and self.thinking_runnable is None:
            raise ValueError("thinking mode is not available on this server")
        message = state.get("message", "")
        provided = state.get("context", {}).get("devices")
        if provided:
            all_devices = list(provided)
        elif self.capability_provider is not None:
            all_devices = await self.capability_provider()
        else:
            all_devices = capability_context("")
        selection_text = _capability_selection_text(
            message,
            state.get("conversation_history", []),
        )
        loop_context = state.get("context", {}).get("agent_loop")
        devices = (
            list(all_devices)
            if provided and loop_context
            else select_capability_context(
                all_devices,
                selection_text,
                max_devices=24 if loop_context else 8,
            )
        )
        runtime_context = dict(state.get("context", {}).get("runtime", {}))
        if loop_context:
            runtime_context["agent_loop"] = loop_context

        profile = self.agent_profile or profile_for(
            state.get("context", {}).get("delegated_agent")
        )
        system = prompt_system(profile, DEVICE_PLANNING_CONTRACT)
        runtime_message = context_message("runtime_context", runtime_context)
        capability_message = context_message("capability_context", {"devices": devices})
        history = compact_history(state.get("conversation_history", []))
        task_id = state.get("task_id", "")
        messages: list[Any] = [
            ("system", system),
            ("human", runtime_message),
            ("human", capability_message),
        ]
        for item in history:
            content = str(item.get("content", ""))
            if item.get("role") == "assistant":
                messages.append(("assistant", content))
            elif item.get("role") == "user":
                messages.append(("human", content))

        analysis = ""
        if thinking:
            analysis = await self._analyze(state, messages, devices)
        user_prompt = state.get("message", "")
        if analysis:
            user_prompt += (
                "\n\nPrivate planning analysis (advisory only; verify it against runtime facts "
                f"and TD capabilities):\n{analysis}"
            )
        messages.append(("human", user_prompt))

        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="planner",
                data={
                    "purpose": "task_planning",
                    "prompt_version": "planner-v3",
                    "device_count": len(devices),
                    "all_device_count": len(all_devices),
                    "selected_device_ids": [
                        str(device.get("device_id", "")) for device in devices
                    ],
                    "all_device_ids": [
                        str(device.get("device_id", "")) for device in all_devices
                    ],
                    "message_length": len(state.get("message", "")),
                    "attempt": 1,
                    "thinking": thinking,
                    "model_thinking": False,
                    "analysis_available": bool(analysis),
                    "agent_id": profile.agent_id,
                    "context_pack_version": "context-pack-v1",
                    "system_characters": len(system),
                    "runtime_context_characters": len(runtime_message),
                    "capability_context_characters": len(capability_message),
                    "history_messages": len(history),
                    "history_characters": sum(len(item["content"]) for item in history),
                    "prompt_snapshot": _planner_prompt_snapshot(
                        system=system,
                        runtime_context=runtime_context,
                        devices=devices,
                        history=history,
                        user_prompt=user_prompt,
                    ),
                },
            )
        started = time.perf_counter()
        result = await self.runnable.ainvoke(messages)
        arguments = _extract_tool_arguments(result)
        await self._record_model_completion(
            state,
            result,
            started,
            attempt=1,
            raw_plan=arguments,
        )
        arguments, intent_normalized = _canonicalize_plan_arguments(arguments)
        if intent_normalized is not None and self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="plan.normalized",
                node="planner",
                data={
                    "reason": "planner intent canonicalized from executable steps",
                    "from_intent": intent_normalized["from_intent"],
                    "to_intent": intent_normalized["to_intent"],
                    "raw_plan": _trace_value(arguments),
                },
            )
        normalized = _normalize_empty_executable_plan(arguments, state)
        if normalized is not None:
            if self.trace_store is not None and task_id:
                await self.trace_store.record(
                    task_id=task_id,
                    type="plan.normalized",
                    node="planner",
                    data={
                        "reason": "planner declared no remaining executable steps after prior observations",
                        "from_intent": arguments.get("intent"),
                        "to_intent": "chat",
                        "raw_plan": _trace_value(arguments),
                    },
                )
            return normalized
        schema_repaired = False
        repair_used = False
        try:
            plan = TaskPlan.model_validate(arguments)
        except PydanticValidationError as exc:
            schema_repaired = True
            repair_used = True
            if self.trace_store is not None and task_id:
                await self.trace_store.record(
                    task_id=task_id,
                    type="plan.validation.failed",
                    node="planner",
                    data={
                        "attempt": 1,
                        "violations": [str(exc)],
                        "raw_plan": _trace_value(arguments),
                    },
                )
                await self.trace_store.record(
                    task_id=task_id,
                    type="model.request.started",
                    node="planner",
                    data={
                        "purpose": "task_planning_repair",
                        "prompt_version": "planner-v3",
                        "attempt": 2,
                        "thinking": thinking,
                        "model_thinking": False,
                        "repair_reason": _trace_text(str(exc), 1200),
                        "prompt_snapshot": _planner_prompt_snapshot(
                            system=system,
                            runtime_context=runtime_context,
                            devices=devices,
                            history=history,
                            user_prompt=(
                                f"{state.get('message', '')}\n\nPlanner correction: "
                                "The generated plan violates the structured TaskPlan contract."
                            ),
                            repair_prompt=(
                                "The generated plan violates the structured TaskPlan contract: "
                                f"{exc}. Regenerate the complete plan. query/control intents "
                                "require at least one executable step. If the original objective "
                                "is already complete and no further tool call is needed, use "
                                "intent=chat with steps=[]. Never return query/control with "
                                "steps=[]. clarify also has no executable steps."
                            ),
                        ),
                    },
                )
            repair_prompt = (
                "The generated plan violates the structured TaskPlan contract: "
                f"{exc}. Regenerate the complete plan. query/control intents require at least "
                "one executable step. If the original objective is already complete and no "
                "further tool call is needed, use intent=chat with steps=[]. Never return "
                "query/control with steps=[]. clarify also has no executable steps."
            )
            repair_messages = [
                *messages[:-1],
                ("human", f"{state.get('message', '')}\n\nPlanner correction: {repair_prompt}"),
            ]
            repair_started = time.perf_counter()
            result = await self.runnable.ainvoke(repair_messages)
            arguments = _extract_tool_arguments(result)
            await self._record_model_completion(
                state,
                result,
                repair_started,
                attempt=2,
                raw_plan=arguments,
            )
            arguments, intent_normalized = _canonicalize_plan_arguments(arguments)
            if intent_normalized is not None and self.trace_store is not None and task_id:
                await self.trace_store.record(
                    task_id=task_id,
                    type="plan.normalized",
                    node="planner",
                    data={
                        "reason": "repaired planner intent canonicalized from executable steps",
                        "from_intent": intent_normalized["from_intent"],
                        "to_intent": intent_normalized["to_intent"],
                        "raw_plan": _trace_value(arguments),
                    },
                )
            normalized = _normalize_empty_executable_plan(arguments, state)
            if normalized is not None:
                if self.trace_store is not None and task_id:
                    await self.trace_store.record(
                        task_id=task_id,
                        type="plan.normalized",
                        node="planner",
                        data={
                            "reason": "repaired plan declared no remaining executable steps after prior observations",
                            "from_intent": arguments.get("intent"),
                            "to_intent": "chat",
                            "raw_plan": _trace_value(arguments),
                        },
                    )
                return normalized
            try:
                plan = TaskPlan.model_validate(arguments)
            except PydanticValidationError:
                if not _terminal_noop_after_execution(arguments, state):
                    raise
                plan = TaskPlan(
                    intent="chat",
                    summary=str(arguments.get("summary", "") or "目标已完成。"),
                    assumptions=list(arguments.get("assumptions", [])),
                )
                if self.trace_store is not None and task_id:
                    await self.trace_store.record(
                        task_id=task_id,
                        type="plan.normalized",
                        node="planner",
                        data={
                            "reason": "repaired plan declared no remaining executable steps",
                            "from_intent": arguments.get("intent"),
                            "to_intent": "chat",
                            "raw_plan": _trace_value(arguments),
                        },
                    )
        violations = _capability_violations(plan, devices, message)
        if violations:
            if schema_repaired:
                raise ValueError(
                    "planner returned a TD-invalid plan after one schema repair: "
                    + "; ".join(violations)
                )
            if self.trace_store is not None and task_id:
                await self.trace_store.record(
                    task_id=task_id,
                    type="plan.validation.failed",
                    node="planner",
                    data={
                        "attempt": 1,
                        "violations": violations,
                        "raw_plan": _trace_value(plan.model_dump(mode="json")),
                    },
                )
                await self.trace_store.record(
                    task_id=task_id,
                    type="model.request.started",
                    node="planner",
                    data={
                        "purpose": "task_planning_repair",
                        "prompt_version": "planner-v3",
                        "attempt": 2,
                        "thinking": thinking,
                        "model_thinking": False,
                        "repair_reason": _trace_text("; ".join(violations), 1200),
                        "prompt_snapshot": _planner_prompt_snapshot(
                            system=system,
                            runtime_context=runtime_context,
                            devices=devices,
                            history=history,
                            user_prompt=(
                                f"{state.get('message', '')}\n\nPlanner correction: "
                                "The generated plan violates the current TD capability contract."
                            ),
                            repair_prompt=(
                                "The generated plan violates the current TD capability contract: "
                                + "; ".join(violations)
                                + ". Regenerate the complete plan using only the listed "
                                "capabilities. Preserve the user's requested action direction; "
                                "never replace turnOff with turnOn or turnOn with turnOff to "
                                "make a plan valid. Clarify instead when needed."
                            ),
                        ),
                    },
                )
            repair_prompt = (
                "The generated plan violates the current TD capability contract: "
                + "; ".join(violations)
                + ". Regenerate the complete plan using only the listed capabilities. "
                "Preserve the user's requested action direction; never replace turnOff with turnOn "
                "or turnOn with turnOff to make a plan valid. Clarify instead when needed."
            )
            # Start a fresh constrained attempt. Replaying an assistant tool call
            # without a matching ToolMessage violates the chat protocol used by
            # OpenAI-compatible providers.
            repair_messages = [
                *messages[:-1],
                ("human", f"{state.get('message', '')}\n\nPlanner correction: {repair_prompt}"),
            ]
            repair_started = time.perf_counter()
            result = await self.runnable.ainvoke(repair_messages)
            arguments = _extract_tool_arguments(result)
            await self._record_model_completion(
                state,
                result,
                repair_started,
                attempt=2,
                raw_plan=arguments,
            )
            arguments, intent_normalized = _canonicalize_plan_arguments(arguments)
            if intent_normalized is not None and self.trace_store is not None and task_id:
                await self.trace_store.record(
                    task_id=task_id,
                    type="plan.normalized",
                    node="planner",
                    data={
                        "reason": "repaired planner intent canonicalized from executable steps",
                        "from_intent": intent_normalized["from_intent"],
                        "to_intent": intent_normalized["to_intent"],
                        "raw_plan": _trace_value(arguments),
                    },
                )
            plan = TaskPlan.model_validate(arguments)
            repair_used = True
            violations = _capability_violations(plan, devices, message)
            if violations:
                raise ValueError(
                    "planner returned a TD-invalid plan after one repair: "
                    + "; ".join(violations)
                )
        if loop_context and _plan_has_action_steps(plan) and self.plan_reviewer is not None:
            review = await self.plan_reviewer.review(
                state=state,
                plan=plan,
                loop_context=loop_context,
            )
            if not review.approved:
                if repair_used:
                    raise ValueError(
                        "conditional plan review rejected the repaired plan: "
                        + "; ".join(review.violations or ["branch consistency failure"])
                    )
                if self.trace_store is not None and task_id:
                    await self.trace_store.record(
                        task_id=task_id,
                        type="plan.validation.failed",
                        node="plan_reviewer",
                        data={
                            "attempt": 1,
                            "violations": review.violations,
                            "selected_branch": review.selected_branch,
                        },
                    )
                    await self.trace_store.record(
                        task_id=task_id,
                        type="model.request.started",
                        node="planner",
                        data={
                            "purpose": "task_planning_repair",
                            "prompt_version": "planner-v3",
                            "attempt": 2,
                            "thinking": thinking,
                            "model_thinking": False,
                        },
                    )
                repair_prompt = (
                    "An independent conditional-plan review rejected the candidate plan. "
                    f"Observed branch: {review.selected_branch or 'undetermined'}. "
                    f"Violations: {'; '.join(review.violations)}. Regenerate the complete next "
                    "plan using only the active branch supported by observations. If the goal is "
                    "already complete, return intent=chat with no steps."
                )
                repair_messages = [
                    *messages[:-1],
                    ("human", f"{state.get('message', '')}\n\nPlanner correction: {repair_prompt}"),
                ]
                repair_started = time.perf_counter()
                result = await self.runnable.ainvoke(repair_messages)
                arguments = _extract_tool_arguments(result)
                await self._record_model_completion(
                    state,
                    result,
                    repair_started,
                    attempt=2,
                    raw_plan=arguments,
                )
                arguments, intent_normalized = _canonicalize_plan_arguments(arguments)
                if intent_normalized is not None and self.trace_store is not None and task_id:
                    await self.trace_store.record(
                        task_id=task_id,
                        type="plan.normalized",
                        node="planner",
                        data={
                            "reason": "repaired planner intent canonicalized from executable steps",
                            "from_intent": intent_normalized["from_intent"],
                            "to_intent": intent_normalized["to_intent"],
                            "raw_plan": _trace_value(arguments),
                        },
                    )
                plan = TaskPlan.model_validate(arguments)
                violations = _capability_violations(plan, devices, message)
                if violations:
                    raise ValueError(
                        "conditional plan repair violated TD capabilities: "
                        + "; ".join(violations)
                    )
                if _plan_has_action_steps(plan):
                    repaired_review = await self.plan_reviewer.review(
                        state=state,
                        plan=plan,
                        loop_context=loop_context,
                    )
                    if not repaired_review.approved:
                        raise ValueError(
                            "conditional plan review rejected the repaired plan: "
                            + "; ".join(
                                repaired_review.violations or ["branch consistency failure"]
                            )
                        )
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.reasoning.summary",
                node="planner",
                data={
                    "summary": plan.summary,
                    "intent": plan.intent,
                    "step_count": len(plan.steps),
                },
            )
        return plan

    async def _analyze(
        self,
        state: dict[str, Any],
        planner_messages: list[Any],
        devices: list[dict[str, Any]],
    ) -> str:
        task_id = state.get("task_id", "")
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="planner",
                data={
                    "purpose": "task_analysis",
                    "prompt_version": "planner-analysis-v2",
                    "device_count": len(devices),
                    "thinking": True,
                    "model_thinking": True,
                },
            )
        profile = self.agent_profile or profile_for(
            state.get("context", {}).get("delegated_agent")
        )
        analysis_messages = [
            (
                "system",
                prompt_system(profile, (
                    "Private analysis contract: analyze the user's "
                "intent, references, target scope, dependencies, and ambiguities using only the "
                "provided runtime facts and TD capabilities. Do not call tools and do not claim "
                "that any action was executed. Return concise planning guidance for a separate "
                    "structured planner."
                )),
            ),
            ("human", context_message(
                "runtime_context",
                state.get("context", {}).get("runtime", {}),
            )),
            ("human", context_message("capability_context", {"devices": devices})),
            *planner_messages[3:],
            ("human", state.get("message", "")),
        ]
        started = time.perf_counter()
        result = await self.thinking_runnable.ainvoke(analysis_messages)
        analysis = _extract_content(result).strip()
        if not analysis:
            raise ValueError("thinking planner returned empty analysis")
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.completed",
                node="planner",
                duration_ms=(time.perf_counter() - started) * 1000,
                data={
                    "purpose": "task_analysis",
                    "prompt_version": "planner-analysis-v2",
                    "model": _extract_model_name(result),
                    "thinking": True,
                    "model_thinking": True,
                    "analysis_characters": len(analysis),
                    **extract_usage_with_cost(result),
                },
            )
        return analysis

    async def _record_model_completion(
        self,
        state: dict[str, Any],
        result: Any,
        started: float,
        *,
        attempt: int,
        raw_plan: dict[str, Any] | None = None,
    ) -> None:
        task_id = state.get("task_id", "")
        if self.trace_store is None or not task_id:
            return
        planner_result = _trace_value(raw_plan) if raw_plan is not None else _extract_result_plan(result)
        await self.trace_store.record(
            task_id=task_id,
            type="model.request.completed",
            node="planner",
            duration_ms=(time.perf_counter() - started) * 1000,
            data={
                "purpose": "task_planning" if attempt == 1 else "task_planning_repair",
                "prompt_version": "planner-v3",
                "attempt": attempt,
                "model": _extract_model_name(result),
                "thinking": bool(state.get("thinking", False)),
                "model_thinking": False,
                "raw_plan": planner_result,
                **extract_usage_with_cost(result),
            },
        )


def _extract_tool_arguments(result: Any) -> dict[str, Any]:
    tool_calls = getattr(result, "tool_calls", None) or []
    if tool_calls:
        call = tool_calls[0]
        if call.get("name") != "generate_plan":
            raise ValueError(f"unexpected planner tool: {call.get('name')}")
        arguments = call.get("args", {})
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("planner tool arguments must be an object")
        return arguments

    raw_calls = getattr(result, "additional_kwargs", {}).get("tool_calls", [])
    if not raw_calls:
        raise ValueError("planner returned no generate_plan tool call")
    function = raw_calls[0].get("function", {})
    if function.get("name") != "generate_plan":
        raise ValueError(f"unexpected planner tool: {function.get('name')}")
    arguments = function.get("arguments", {})
    return json.loads(arguments) if isinstance(arguments, str) else arguments


def _extract_named_tool_arguments(result: Any, expected_name: str) -> dict[str, Any]:
    tool_calls = getattr(result, "tool_calls", None) or []
    if tool_calls:
        call = tool_calls[0]
        if call.get("name") != expected_name:
            raise ValueError(f"unexpected tool: {call.get('name')}")
        arguments = call.get("args", {})
        return json.loads(arguments) if isinstance(arguments, str) else arguments
    raw_calls = getattr(result, "additional_kwargs", {}).get("tool_calls", [])
    if not raw_calls:
        raise ValueError(f"model returned no {expected_name} tool call")
    function = raw_calls[0].get("function", {})
    if function.get("name") != expected_name:
        raise ValueError(f"unexpected tool: {function.get('name')}")
    arguments = function.get("arguments", {})
    return json.loads(arguments) if isinstance(arguments, str) else arguments


def _extract_content(result: Any) -> str:
    message = result
    if isinstance(result, dict):
        messages = result.get("messages", [])
        if messages:
            message = messages[-1]
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")


def _planner_prompt_snapshot(
    *,
    system: str,
    runtime_context: dict[str, Any],
    devices: list[dict[str, Any]],
    history: list[dict[str, str]],
    user_prompt: str,
    repair_prompt: str = "",
) -> dict[str, Any]:
    snapshot = {
        "system": _trace_text(system, 1200),
        "user_prompt": _trace_text(user_prompt, 1200),
        "history": _trace_value(history),
        "runtime_context": _trace_value(runtime_context),
        "capabilities": _trace_value(devices),
    }
    if repair_prompt:
        snapshot["repair_prompt"] = _trace_text(repair_prompt, 1200)
    return snapshot


def _extract_result_plan(result: Any) -> dict[str, Any] | str:
    try:
        return _trace_value(_extract_tool_arguments(result))
    except Exception as exc:  # pragma: no cover - defensive telemetry path
        return f"[unavailable] {exc}"


def _trace_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else f"{text[:limit]}...[truncated]"


def _trace_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, str):
        return _trace_text(value, 800)
    if isinstance(value, dict):
        items = list(value.items())[:24]
        clipped = {
            _trace_text(key, 120): _trace_value(item, depth=depth + 1)
            for key, item in items
        }
        if len(value) > len(items):
            clipped["__truncated__"] = f"{len(value) - len(items)} more fields"
        return clipped
    if isinstance(value, (list, tuple)):
        items = [_trace_value(item, depth=depth + 1) for item in list(value)[:16]]
        if len(value) > len(items):
            items.append(f"...[truncated {len(value) - len(items)} items]")
        return items
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _trace_text(value, 300)


def _terminal_noop_after_execution(
    arguments: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    return (
        _has_prior_observations(state)
        and arguments.get("intent") in {"query", "control"}
        and arguments.get("steps") == []
    )


def _normalize_empty_executable_plan(
    arguments: dict[str, Any],
    state: dict[str, Any],
) -> TaskPlan | None:
    if not _terminal_noop_after_execution(arguments, state):
        return None
    return TaskPlan(
        intent="chat",
        summary=str(arguments.get("summary", "") or "目标已完成。"),
        assumptions=list(arguments.get("assumptions", [])),
    )


def _canonicalize_plan_arguments(
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str] | None]:
    steps = arguments.get("steps")
    if not isinstance(steps, list) or not steps:
        return arguments, None
    target_intent = "control" if any(
        isinstance(step, dict) and step.get("kind") == "action" for step in steps
    ) else "query"
    original_intent = str(arguments.get("intent", ""))
    if original_intent == target_intent:
        return arguments, None
    normalized = dict(arguments)
    normalized["intent"] = target_intent
    return normalized, {
        "from_intent": original_intent,
        "to_intent": target_intent,
    }


def _has_prior_observations(state: dict[str, Any]) -> bool:
    if state.get("execution_history"):
        return True
    loop_context = state.get("context", {}).get("agent_loop") or {}
    return bool(loop_context.get("observations"))


def _plan_has_action_steps(plan: TaskPlan) -> bool:
    return any(step.kind == "action" for step in plan.steps)

def _extract_model_name(result: Any) -> str:
    metadata = getattr(result, "response_metadata", None) or {}
    return str(metadata.get("model_name") or metadata.get("model") or "unknown")


def _capability_violations(
    plan: TaskPlan,
    devices: list[dict[str, Any]],
    message: str = "",
) -> list[str]:
    by_id = {str(device.get("device_id")): device for device in devices}
    violations: list[str] = []
    for step in plan.steps:
        device = by_id.get(step.device_id)
        if device is None:
            violations.append(f"step {step.id}: device '{step.device_id}' is not registered")
            continue
        if step.kind != "action":
            continue
        action = next(
            (item for item in device.get("actions", []) if item.get("name") == step.action),
            None,
        )
        if action is None:
            violations.append(
                f"step {step.id}: action '{step.action}' is not declared by {step.device_id}"
            )
            continue
        try:
            validate(instance=step.input, schema=action.get("input_schema") or {})
        except JSONSchemaValidationError as exc:
            violations.append(f"step {step.id}: invalid '{step.action}' input: {exc.message}")
    violations.extend(_power_direction_violations(plan, by_id, message))
    return violations


def _power_direction_violations(
    plan: TaskPlan,
    by_id: dict[str, dict[str, Any]],
    message: str,
) -> list[str]:
    if not _power_direction_guard_enabled():
        return []
    normalized_message = message.lower()
    requested_off = _contains_any(normalized_message, _POWER_OFF_MARKERS)
    requested_on = _contains_any(normalized_message, _POWER_ON_MARKERS)
    if not requested_off and not requested_on:
        return []

    mixed_intent = requested_off and requested_on
    clauses = _power_direction_clauses(normalized_message) if mixed_intent else []
    violations: list[str] = []
    for step in plan.steps:
        if step.kind != "action" or step.action not in {"turnOn", "turnOff"}:
            continue
        if mixed_intent:
            device = by_id.get(step.device_id, {})
            relevant_clauses = [
                clause for clause in clauses if _clause_matches_step(clause, step, device)
            ]
            if not relevant_clauses:
                continue
            if step.action == "turnOn" and any(clause["intent"] == "off" for clause in relevant_clauses):
                violations.append("plan reverses the requested power-off intent with turnOn")
            if step.action == "turnOff" and any(clause["intent"] == "on" for clause in relevant_clauses):
                violations.append("plan reverses the requested power-on intent with turnOff")
            continue
        if requested_off and step.action == "turnOn":
            violations.append("plan reverses the requested power-off intent with turnOn")
        if requested_on and step.action == "turnOff":
            violations.append("plan reverses the requested power-on intent with turnOff")
    return violations


def _power_direction_guard_enabled() -> bool:
    value = os.getenv("WOTAGENT_ENABLE_POWER_DIRECTION_GUARD", "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def _power_direction_clauses(message: str) -> list[dict[str, str]]:
    clauses: list[dict[str, str]] = []
    for raw_clause in re.split(r"[，,。；;]|(?:然后|并且|并|且|同时)", message):
        clause = raw_clause.strip()
        if not clause:
            continue
        off = _contains_any(clause, _POWER_OFF_MARKERS)
        on = _contains_any(clause, _POWER_ON_MARKERS)
        if off == on:
            continue
        clauses.append({
            "intent": "off" if off else "on",
            "text": clause,
        })
    return clauses


def _clause_matches_step(
    clause: dict[str, str],
    step: Any,
    device: dict[str, Any],
) -> bool:
    text = clause["text"]
    specific_terms, location_terms = _device_reference_terms(step.device_id, device)
    if specific_terms:
        return any(term and term in text for term in specific_terms)
    return any(term and term in text for term in location_terms)


def _device_reference_terms(device_id: str, device: dict[str, Any]) -> tuple[set[str], set[str]]:
    specific_terms = {
        str(device_id).lower(),
        str(device.get("title", "")).lower(),
        str(device.get("device_type", "")).lower(),
    }
    location_terms = {
        str(device.get("location", "")).lower(),
    }
    location = str(device.get("location", ""))
    location_terms.update(term.lower() for term in _LOCATION_TERMS.get(location, ()))
    device_type = str(device.get("device_type", "")).lower()
    specific_terms.update(term.lower() for term in _DEVICE_TERMS.get(device_type, ()))
    normalized_specific: set[str] = set()
    normalized_location: set[str] = set()
    for term in specific_terms:
        cleaned = term.strip()
        if cleaned:
            normalized_specific.add(cleaned)
            normalized_specific.add(cleaned.replace("_", " "))
    for term in location_terms:
        cleaned = term.strip()
        if cleaned:
            normalized_location.add(cleaned)
            normalized_location.add(cleaned.replace("_", " "))
    return normalized_specific, normalized_location


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


_POWER_OFF_MARKERS = ("关掉", "关闭", "关灯", "turn off", "switch off", "shut off")
_POWER_ON_MARKERS = ("打开", "开启", "开灯", "turn on", "switch on")


def _capability_selection_text(
    message: str,
    history: list[dict[str, str]],
) -> str:
    if _mentions_broad_catalog(message):
        return message
    if _mentions_device_scope(message):
        return message
    for item in reversed(history):
        if item.get("role") == "user" and _mentions_device_scope(item.get("content", "")):
            return f"{message} {item['content']}"
    return message


def _mentions_broad_catalog(message: str) -> bool:
    text = message.lower()
    return any(
        term in text
        for term in (
            "房间",
            "空间",
            "区域",
            "所有设备",
            "全部设备",
            "多少设备",
            "几个设备",
            "有哪些设备",
            "how many rooms",
            "all rooms",
            "all devices",
        )
    )


def _mentions_device_scope(message: str) -> bool:
    text = message.lower()
    return any(
        term in text
        for terms in (*_LOCATION_TERMS.values(), *_DEVICE_TERMS.values())
        for term in terms
    )
