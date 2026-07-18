"""LLM model assembly for the WoTAgent 2.0 LangGraph runtime."""

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
    """Build an OpenAI-compatible chat model."""
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
class AgentPipeline:
    """Models used by the typed Planner and Response Agent nodes."""

    planner: Runnable
    response_model: Runnable
    thinking_planner: Runnable | None = None
    thinking_response_model: Runnable | None = None


def create_wot_pipeline(
    *,
    model_name: str | None = None,
    temperature: float = 0.7,
    reasoning_effort: str = "high",
    enable_thinking: bool = True,
    enable_streaming: bool = False,
) -> AgentPipeline:
    """Create only the models consumed by the WoTAgent 2.0 graph.

    Device execution is deliberately absent here. The graph delegates plans to
    the deterministic orchestrator and MCP gateway instead of a second ReAct
    agent.
    """
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
        # Providers such as DeepSeek reject forced tool_choice in thinking mode.
        # The thinking model analyzes; the direct planner still emits the schema.
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

    return AgentPipeline(
        planner=planner,
        response_model=response_model,
        thinking_planner=thinking_planner,
        thinking_response_model=thinking_response_model,
    )
