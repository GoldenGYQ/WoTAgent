"""Execution-aware LLM response composer for the final graph node."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from ..core.models import AgentContext
from ..telemetry import InMemoryTraceStore


@dataclass
class LangChainResponseComposer:
    runnable: Any
    trace_store: InMemoryTraceStore | None = None

    async def __call__(self, state: dict[str, Any]) -> str:
        facts = _response_facts(state)
        prompt = (
            "你是 WoTAgent 的最终 Response Agent。请根据下面经过验证的事实，"
            "使用与用户相同的语言给出简洁自然的最终回复。\n"
            "严格规则：\n"
            "1. 不得调用工具、生成新计划或改变任务状态。\n"
            "2. 只有 status=succeeded 时才能声称全部完成。\n"
            "3. partial_success 必须分别说明成功和失败步骤。\n"
            "4. failed/denied 时不得使用‘已完成’等成功措辞。\n"
            "5. 只使用提供的事实，不推测设备状态。\n\n"
            f"执行事实：{json.dumps(facts, ensure_ascii=False)}"
        )
        task_id = state.get("task_id", "")
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="responder",
                data={"purpose": "final_response", "prompt_version": "responder-v2.0"},
            )
        started = time.perf_counter()
        context = AgentContext(
            session_id=state.get("session_id", ""),
            user_role=state.get("role", "operator"),
            user_message=state.get("message", ""),
            plan_json=json.dumps(facts.get("plan", {}), ensure_ascii=False),
            execution_results=json.dumps(facts, ensure_ascii=False),
        )
        result = await self.runnable.ainvoke(
            {"messages": [HumanMessage(content=prompt)]},
            context=context,
        )
        message = _last_message(result)
        response = str(getattr(message, "content", "") or "").strip()
        if not response:
            raise ValueError("response agent returned empty content")
        if self.trace_store is not None and task_id:
            usage = _usage(message)
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.completed",
                node="responder",
                duration_ms=(time.perf_counter() - started) * 1000,
                data={
                    "purpose": "final_response",
                    "prompt_version": "responder-v2.0",
                    "model": _model_name(message),
                    **usage,
                },
            )
        return response


def _response_facts(state: dict[str, Any]) -> dict[str, Any]:
    plan = state.get("plan")
    trace = state.get("trace")
    decisions = state.get("policy_decisions", [])
    return {
        "user_message": state.get("message", ""),
        "plan": plan.model_dump(mode="json") if plan is not None else None,
        "policy_decisions": [item.model_dump(mode="json") for item in decisions],
        "status": trace.status.value if trace is not None else state.get("phase", "unknown"),
        "steps": [item.model_dump(mode="json") for item in trace.steps] if trace is not None else [],
        "error": state.get("error", ""),
        "confirmation_approved": state.get("confirmation_approved"),
    }


def _last_message(result: Any) -> Any:
    if isinstance(result, dict):
        messages = result.get("messages", [])
        if messages:
            return messages[-1]
    if hasattr(result, "content"):
        return result
    raise ValueError("response agent returned no message")


def _usage(message: Any) -> dict[str, int]:
    usage = getattr(message, "usage_metadata", None) or {}
    response = getattr(message, "response_metadata", None) or {}
    raw = response.get("token_usage") or response.get("usage") or {}
    input_tokens = int(usage.get("input_tokens", raw.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", raw.get("completion_tokens", 0)) or 0)
    details = usage.get("output_token_details") or {}
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": int(details.get("reasoning", raw.get("reasoning_tokens", 0)) or 0),
        "total_tokens": int(usage.get("total_tokens", raw.get("total_tokens", input_tokens + output_tokens)) or 0),
    }


def _model_name(message: Any) -> str:
    metadata = getattr(message, "response_metadata", None) or {}
    return str(metadata.get("model_name") or metadata.get("model") or "unknown")
