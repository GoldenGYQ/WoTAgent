"""LLM provider and runnable assembly for WoTAgent agents."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from ..domain import TaskPlan

PLAN_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate_plan",
        "description": "Classify user intent and output a structured plan",
        "parameters": TaskPlan.model_json_schema(),
    },
}


def _build_llm(
    model_name: str | None = None,
    temperature: float = 0.7,
    reasoning_effort: str = "high",
    enable_thinking: bool = True,
    enable_streaming: bool = False,
) -> ChatOpenAI:
    max_output_tokens = max(
        256,
        min(int(os.getenv("WOTAGENT_MAX_OUTPUT_TOKENS", "2048")), 8192),
    )
    kwargs: dict[str, Any] = {
        "model": model_name or os.getenv("LLM_MODEL", "deepseek-chat"),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
        "temperature": temperature,
        "max_tokens": max_output_tokens,
        "streaming": enable_streaming,
        "extra_body": {
            "thinking": {"type": "enabled" if enable_thinking else "disabled"}
        },
    }
    if enable_thinking:
        kwargs["reasoning_effort"] = reasoning_effort
    if enable_streaming:
        kwargs["stream_usage"] = True
    return ChatOpenAI(**kwargs)


@dataclass
class LLMRuntime:
    """Named model runnables; this object does not orchestrate Agents."""

    planner: Runnable
    response_model: Runnable
    thinking_planner: Runnable | None = None
    thinking_response_model: Runnable | None = None


def create_llm_runtime(
    *,
    model_name: str | None = None,
    temperature: float = 0.7,
    reasoning_effort: str = "high",
    enable_thinking: bool = True,
    enable_streaming: bool = False,
) -> LLMRuntime:
    direct_planner_model = _build_llm(
        model_name=model_name,
        temperature=0,
        enable_thinking=False,
    )
    planner = direct_planner_model.bind(
        tools=[PLAN_SCHEMA],
        tool_choice={"type": "function", "function": {"name": "generate_plan"}},
    )
    response_model = _build_llm(
        model_name=model_name,
        temperature=temperature,
        enable_thinking=False,
        enable_streaming=enable_streaming,
    )
    thinking_planner = None
    thinking_response_model = None
    if enable_thinking:
        thinking_planner = _build_llm(
            model_name=model_name,
            temperature=0,
            reasoning_effort=reasoning_effort,
            enable_thinking=True,
        )
        thinking_response_model = _build_llm(
            model_name=model_name,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            enable_thinking=True,
            enable_streaming=enable_streaming,
        )
    return LLMRuntime(
        planner=planner,
        response_model=response_model,
        thinking_planner=thinking_planner,
        thinking_response_model=thinking_response_model,
    )


__all__ = ["LLMRuntime", "PLAN_SCHEMA", "_build_llm", "create_llm_runtime"]
