"""Agent pipeline container and factory functions."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    dynamic_prompt,
)
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from ..domain import TaskPlan
from ..tools import get_all_tools
from .models import AgentContext
from .prompt import _make_prompt_builder

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Planner tool-calling schema — guarantees structured output
# ---------------------------------------------------------------------------

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
    """Build an OpenAI-compatible LLM with optional DeepSeek thinking mode."""
    kwargs: dict[str, Any] = {
        "model": model_name or os.getenv("LLM_MODEL", "deepseek-chat"),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
        "temperature": temperature,
        "streaming": enable_streaming,
    }
    if enable_thinking:
        kwargs["reasoning_effort"] = reasoning_effort
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    else:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    if enable_streaming:
        kwargs["stream_usage"] = True
    return ChatOpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Pipeline container with typed roles (planner is a tool-calling LLM,
# executor/responder are ReAct agents; observer is reserved).
# ---------------------------------------------------------------------------


@dataclass
class AgentPipeline:
    """Container for pipeline roles.

    The active runtime path:
      planner (tool-calling LLM, classify+plan)
        → intent=chat → responder
        → intent=control|query → executor (ReAct with tools)

    ``observer`` is reserved for future execution-verification use.
    """
    planner: Runnable
    executor: Runnable
    responder: Runnable
    observer: Runnable | None = None
    response_model: Runnable | None = None
    thinking_planner: Runnable | None = None
    thinking_response_model: Runnable | None = None


def create_wot_pipeline(
    *,
    model_name: str | None = None,
    temperature: float = 0.7,
    reasoning_effort: str = "high",
    enable_thinking: bool = True,
    enable_streaming: bool = False,
    max_tool_calls_per_plan: int = 20,
) -> AgentPipeline:
    """Create the multi-agent pipeline.

    Returns an ``AgentPipeline`` with typed ``.planner``, ``.executor``,
    ``.observer``, and ``.responder`` attributes.
    """
    llm = _build_llm(
        model_name=model_name,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        enable_thinking=enable_thinking,
        enable_streaming=enable_streaming,
    )
    # Planner is a tool-calling LLM (temperature=0, no thinking) for reliable
    # structured classification — not a ReAct agent with tool access.
    plan_llm = _build_llm(
        model_name=model_name,
        temperature=0,
        enable_thinking=False,
    )
    response_llm = _build_llm(
        model_name=model_name,
        temperature=temperature,
        enable_thinking=False,
        enable_streaming=enable_streaming,
    )
    planner = plan_llm.bind(
        tools=[PLAN_SCHEMA],
        tool_choice={"type": "function", "function": {"name": "generate_plan"}},
    )
    thinking_planner = None
    thinking_response_llm = None
    if enable_thinking:
        thinking_plan_llm = _build_llm(
            model_name=model_name,
            temperature=0,
            reasoning_effort=reasoning_effort,
            enable_thinking=True,
        )
        # Thinking models on OpenAI-compatible providers such as DeepSeek do
        # not support forced tool_choice. Use this model for analysis only;
        # the direct planner below remains responsible for the forced schema.
        thinking_planner = thinking_plan_llm
        thinking_response_llm = _build_llm(
            model_name=model_name,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            enable_thinking=True,
            enable_streaming=enable_streaming,
        )

    tools = get_all_tools(include_mcp=False)

    executor_mw = [
        dynamic_prompt(_make_prompt_builder("executor")),
        ToolCallLimitMiddleware(thread_limit=max_tool_calls_per_plan),
        ModelRetryMiddleware(max_retries=2, retry_on=(Exception,)),
    ]
    executor = create_agent(
        model=llm,
        tools=tools,
        system_prompt="",
        middleware=executor_mw,
        context_schema=AgentContext,
        name="executor",
    )

    observer_mw = [
        dynamic_prompt(_make_prompt_builder("observer")),
    ]
    observer = create_agent(
        model=llm,
        tools=[],
        system_prompt="",
        middleware=observer_mw,
        context_schema=AgentContext,
        name="observer",
    )

    responder_mw = [
        dynamic_prompt(_make_prompt_builder("responder")),
    ]
    responder = create_agent(
        model=llm,
        tools=[],
        system_prompt="",
        middleware=responder_mw,
        context_schema=AgentContext,
        name="responder",
    )

    logger.info("Pipeline created | model=%s tools=%d", llm.model, len(tools))
    return AgentPipeline(
        planner=planner,
        executor=executor,
        responder=responder,
        observer=observer,
        response_model=response_llm,
        thinking_planner=thinking_planner,
        thinking_response_model=thinking_response_llm,
    )


def create_wot_agent(
    *,
    model_name: str | None = None,
    temperature: float = 0.7,
    reasoning_effort: str = "high",
    enable_thinking: bool = True,
    enable_streaming: bool = False,
    max_tool_calls_per_plan: int = 20,
    system_prompt: str | None = None,
    name: str = "wot_agent",
) -> Runnable:
    """Legacy single ReAct agent.  Prefer ``create_wot_pipeline``."""
    llm = _build_llm(
        model_name=model_name,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        enable_thinking=enable_thinking,
        enable_streaming=enable_streaming,
    )
    tools = get_all_tools(include_mcp=False)
    middleware = [
        dynamic_prompt(_make_prompt_builder("default")),
        ToolCallLimitMiddleware(thread_limit=max_tool_calls_per_plan),
        ModelRetryMiddleware(max_retries=2, retry_on=(Exception,)),
    ]
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
        context_schema=AgentContext,
        name=name,
    )
    logger.info("Legacy agent created | model=%s tools=%d", llm.model, len(tools))
    return agent
