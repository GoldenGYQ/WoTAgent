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

from ..tools import get_all_tools
from .models import AgentContext
from .prompt import _make_prompt_builder

logger = logging.getLogger(__name__)


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
    return ChatOpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Pipeline container with typed roles
# ---------------------------------------------------------------------------


@dataclass
class AgentPipeline:
    """Container for pipeline roles.

    ``planner`` is reserved for future direct-planning extension;
    the active runtime path is intent-classification-first.
    """
    planner: Runnable
    executor: Runnable
    observer: Runnable
    responder: Runnable


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
    tools = get_all_tools(include_mcp=False)

    planner_mw = [
        dynamic_prompt(_make_prompt_builder("planner")),
        ModelRetryMiddleware(max_retries=1, retry_on=(Exception,)),
    ]
    planner = create_agent(
        model=llm,
        tools=tools,
        system_prompt="",
        middleware=planner_mw,
        context_schema=AgentContext,
        name="planner",
    )

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
        observer=observer,
        responder=responder,
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
