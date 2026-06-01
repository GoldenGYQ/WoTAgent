"""Dynamic prompt builders for the multi-agent pipeline."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable

from .. import prompts
from .models import AgentContext


def _extract_context(request: Runnable) -> AgentContext:
    """Pull AgentContext from a middleware request."""
    ctx: AgentContext | None = None
    runtime = getattr(request, "runtime", None)
    if runtime is not None:
        ctx = getattr(runtime, "context", None) or getattr(runtime, "args", None)
    if ctx is None:
        ctx = getattr(request, "context", None)
    return ctx or AgentContext()


def _format_tools(tools: list[Runnable]) -> str:
    """Format a list of tools as a human-readable bullet list."""
    lines: list[str] = []
    for t in tools:
        name = getattr(t, "name", str(t))
        desc = getattr(t, "description", "")
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines) if lines else "No tools available."


def _make_prompt_builder(template_name: str):
    """Create a dynamic prompt function for a specific template."""
    def builder(request: Runnable) -> str:
        ctx = _extract_context(request)
        tools: list[Runnable] = list(getattr(request, "tools", []))
        tools_desc = _format_tools(tools)

        extra = ""
        if ctx.user_role == "viewer":
            extra = "You have read-only access."
        elif ctx.user_role == "operator":
            extra = "You can control devices."

        plan_json = getattr(ctx, "plan_json", "{}") or "{}"
        exec_results = getattr(ctx, "execution_results", "") or ""
        perception_ctx = getattr(ctx, "perception_context", "") or ""

        prompt = prompts.get_template(template_name).partial(
            role=ctx.user_role,
            session_id=ctx.session_id,
            tools_description=tools_desc,
            plan_json=plan_json,
            execution_results=exec_results,
            extra_instructions=extra,
            perception_context=perception_ctx,
        )
        content = prompt.format_messages()[0].content
        return content if isinstance(content, str) else str(content)

    return builder
