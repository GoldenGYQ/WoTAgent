"""Execution-aware LLM response composer for the final graph node."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, message_chunk_to_message
from ..prompting import RESPONSE_CONTRACT, context_message
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
        system = RESPONSE_CONTRACT
        prompt = context_message("response_facts", facts)
        task_id = state.get("task_id", "")
        if self.trace_store is not None and task_id:
            await self.trace_store.record(
                task_id=task_id,
                type="model.request.started",
                node="responder",
                data={
                    "purpose": "final_response",
                    "prompt_version": "responder-v3",
                    "thinking": thinking,
                },
            )
        started = time.perf_counter()
        messages = [
            SystemMessage(content=system),
            HumanMessage(content=prompt),
        ]
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
                    "prompt_version": "responder-v3",
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
    execution_history = state.get("execution_history", [])
    return {
        "user_message": state.get("message", ""),
        "current_role": state.get("role", "viewer"),
        "runtime_context": state.get("context", {}).get("runtime", {}),
        "plan": plan.model_dump(mode="json") if plan is not None else None,
        "policy_decisions": [item.model_dump(mode="json") for item in decisions],
        "status": (
            "failed"
            if state.get("error")
            else trace.status.value if trace is not None else state.get("phase", "unknown")
        ),
        "steps": [item.model_dump(mode="json") for item in trace.steps] if trace is not None else [],
        "execution_history": [
            {
                "iteration": index,
                "status": item.status.value,
                "plan": item.plan.model_dump(mode="json"),
                "steps": [step.model_dump(mode="json") for step in item.steps],
            }
            for index, item in enumerate(execution_history, start=1)
        ],
        "agent_iterations": state.get("agent_iterations", 0),
        "tool_call_count": state.get("tool_call_count", 0),
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
