"""Official LangChain agent factories for supervisors and specialists."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import BaseTool, StructuredTool

from .contracts import (
    AgentFinding,
    AgentFindingPayload,
    AgentSpec,
    DelegationRequest,
)


def build_specialist_agent(
    model: Any,
    spec: AgentSpec,
    tools: Sequence[BaseTool],
    *,
    checkpointer: Any = None,
):
    """Create a bounded ReAct specialist with a business-only output contract."""
    scoped_tools = _scope_tools(spec, tools)
    prompt = (
        f"{spec.system_prompt}\n\n"
        f"职责：{spec.responsibility}\n"
        "你只能调用当前暴露的工具。工具返回成功不等于目标完成；涉及状态改变时，"
        "必须检查 verified。最终只返回符合 AgentFindingPayload 的业务结果："
        "status、finding、confidence、evidence、recommended_actions、"
        "unresolved_questions 和 tool_calls。不要生成 agent、task_id 或 "
        "agent_run_id；这些身份字段由运行时注入。finding 中的结论必须能由 "
        "evidence 支撑，不得声称未验证的动作已经成功。"
    )
    return create_agent(
        model=model,
        tools=scoped_tools,
        system_prompt=prompt,
        response_format=AgentFindingPayload,
        checkpointer=checkpointer,
        name=spec.agent_id,
    )


def build_delegation_tool(
    spec: AgentSpec,
    specialist_agent: Any,
) -> StructuredTool:
    """Expose one context-isolated specialist to the supervisor as a tool."""

    async def delegate(
        objective: str,
        expected_evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        request = DelegationRequest(
            objective=objective,
            expected_evidence=expected_evidence or [],
        )
        result = await specialist_agent.ainvoke({
            "messages": [{
                "role": "user",
                "content": (
                    f"目标：{request.objective}\n"
                    f"期望证据：{request.expected_evidence}"
                ),
            }],
        })
        payload = AgentFindingPayload.model_validate(
            result.get("structured_response")
        )
        finding = AgentFinding(
            agent=spec.agent_id,
            objective=request.objective,
            **payload.model_dump(),
        )
        return finding.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=delegate,
        name=f"delegate_{spec.agent_id}",
        description=(
            f"委派给 {spec.agent_id}。职责：{spec.responsibility}。"
            "输入目标和期望证据，返回结构化 AgentFinding。"
        ),
    )


def build_supervisor_agent(
    model: Any,
    delegation_tools: Sequence[BaseTool],
    *,
    checkpointer: Any = None,
):
    """Create the main Butler agent; all professional work is delegated."""
    return create_agent(
        model=model,
        tools=list(delegation_tools),
        system_prompt=(
            "你是 WoTAgent Butler Supervisor，是面向用户的主智能体。先建立目标和"
            "可验证成功条件，再选择一个或多个专业 Agent 调查或执行。你不能直接"
            "调用设备工具，也不能自行扩大专业 Agent 权限。收到 AgentFinding 后"
            "必须检查证据、未解决问题和 verified 状态；证据不足时可以再次委派，"
            "目标满足或达到边界时再向用户回答。不得把专业 Agent 的建议误写成已执行。"
        ),
        checkpointer=checkpointer,
        name="butler_supervisor",
    )


def _scope_tools(spec: AgentSpec, tools: Sequence[BaseTool]) -> list[BaseTool]:
    by_name: dict[str, BaseTool] = {}
    for tool in tools:
        if tool.name in by_name:
            raise ValueError(f"duplicate tool name: {tool.name}")
        by_name[tool.name] = tool
    if not spec.allowed_tools:
        return list(by_name.values())
    return [
        by_name[name]
        for name in sorted(spec.allowed_tools)
        if name in by_name
    ]


__all__ = [
    "build_delegation_tool",
    "build_specialist_agent",
    "build_supervisor_agent",
]
