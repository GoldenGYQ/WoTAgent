"""Execution-aware LLM response composer for the final graph node."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, message_chunk_to_message
from ..telemetry import InMemoryTraceStore, extract_usage_with_cost


@dataclass
class LangChainResponseComposer:
    runnable: Any
    trace_store: InMemoryTraceStore | None = None
    thinking_runnable: Any | None = None

    async def __call__(self, state: dict[str, Any]) -> str:
        thinking = bool(state.get("thinking", False))
        if thinking and self.thinking_runnable is None:
            raise ValueError("thinking mode is not available on this server")
        runnable = self.thinking_runnable if thinking else self.runnable
        facts = _response_facts(state)
        system = (
            "你是 WoTAgent 的最终 Response Agent。请根据下面经过验证的事实，"
            "使用与用户相同的语言给出简洁自然的最终回复。\n"
            "严格规则：\n"
            "1. 不得调用工具、生成新计划或改变任务状态。\n"
            "2. 只有 status=succeeded 时才能声称全部完成。\n"
            "3. partial_success 必须分别说明成功和失败步骤。\n"
            "4. failed/denied 时不得使用‘已完成’等成功措辞。\n"
            "5. 只使用提供的事实，不推测设备状态或当前角色。\n"
            "6. ACTION_NOT_DECLARED 只表示当前 TD 未声明该动作，不能说物理设备本身不支持。\n"
            "7. 结构化执行事实优先于历史对话中的旧状态。"
        )
        prompt = f"执行事实：{json.dumps(facts, ensure_ascii=False)}"
        task_id = state.get("task_id", "")
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="responder",
                data={
                    "purpose": "final_response",
                    "prompt_version": "responder-v2.0",
                    "thinking": thinking,
                },
            )
        started = time.perf_counter()
        messages = [SystemMessage(content=system)]
        for item in state.get("conversation_history", []):
            content = str(item.get("content", ""))
            if item.get("role") == "assistant":
                messages.append(AIMessage(content=content))
            elif item.get("role") == "user":
                messages.append(HumanMessage(content=content))
        messages.append(HumanMessage(content=prompt))
        ttft_ms: float | None = None
        if hasattr(runnable, "astream"):
            aggregate = None
            async for chunk in runnable.astream(messages):
                if ttft_ms is None and getattr(chunk, "content", ""):
                    ttft_ms = (time.perf_counter() - started) * 1000
                aggregate = chunk if aggregate is None else aggregate + chunk
            if aggregate is None:
                raise ValueError("response agent returned no stream chunks")
            message = message_chunk_to_message(aggregate)
        else:
            result = await runnable.ainvoke(messages)
            message = _last_message(result)
        response = str(getattr(message, "content", "") or "").strip()
        if not response:
            raise ValueError("response agent returned empty content")
        if self.trace_store is not None and task_id:
            usage = extract_usage_with_cost(message)
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.completed",
                node="responder",
                duration_ms=(time.perf_counter() - started) * 1000,
                data={
                    "purpose": "final_response",
                    "prompt_version": "responder-v2.0",
                    "model": _model_name(message),
                    "ttft_ms": ttft_ms,
                    "ttft_available": ttft_ms is not None,
                    "thinking": thinking,
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
        "current_role": state.get("role", "viewer"),
        "runtime_context": state.get("context", {}).get("runtime", {}),
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


def _model_name(message: Any) -> str:
    metadata = getattr(message, "response_metadata", None) or {}
    return str(metadata.get("model_name") or metadata.get("model") or "unknown")
