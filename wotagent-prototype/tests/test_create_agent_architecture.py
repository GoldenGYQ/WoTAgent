from __future__ import annotations

import asyncio
from typing import Any, Sequence

import pytest
from langchain.agents.middleware.model_call_limit import (
    ModelCallLimitExceededError,
)
from langchain.agents.middleware.tool_call_limit import (
    ToolCallLimitExceededError,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from pydantic import Field

from wotagent.agents import (
    AgentFinding,
    AgentFindingPayload,
    Evidence,
    ToolExecutionResult,
    build_delegation_tool,
    build_health_tools,
    build_run_supervisor_agent,
    build_specialist_agent,
    build_supervisor_agent,
    get_agent_spec,
)
from wotagent.orchestration import SupervisorDecision


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    index: int = 0
    bound_tool_names: list[list[str]] = Field(default_factory=list)
    captured_messages: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-chat-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> "ScriptedChatModel":
        names = []
        for item in tools:
            if isinstance(item, dict):
                names.append(str(item.get("function", item).get("name", "")))
            else:
                names.append(str(getattr(item, "name", getattr(item, "__name__", ""))))
        self.bound_tool_names.append(names)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.captured_messages.append(list(messages))
        if self.index >= len(self.responses):
            raise AssertionError("scripted model ran out of responses")
        response = self.responses[self.index]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


@tool
def read_health_summary(resident_id: str) -> dict[str, Any]:
    """Read a resident health summary."""
    return {
        "resident_id": resident_id,
        "generated_at": "2026-07-17T08:00:00Z",
        "fresh": False,
    }


@tool
def read_latest_vitals(resident_id: str) -> dict[str, Any]:
    """Read latest resident vital signs."""
    return {
        "resident_id": resident_id,
        "heart_rate": 104,
        "observed_at": "2026-07-17T09:00:00Z",
    }


@tool
def unlock_medicine_cabinet(resident_id: str) -> dict[str, Any]:
    """An intentionally unauthorized health tool."""
    return {"resident_id": resident_id, "unlocked": True}


def _finding_payload() -> dict[str, Any]:
    return AgentFindingPayload(
        status="completed",
        finding="摘要已过期，最新心率为 104 bpm，需要护理人员复核。",
        confidence=0.9,
        evidence=[
            Evidence(
                source="read_health_summary",
                value={"fresh": False},
                timestamp="2026-07-17T08:00:00Z",
                reliability=1,
                description="摘要时间早于最新数据",
            ),
            Evidence(
                source="read_latest_vitals",
                value={"heart_rate": 104},
                timestamp="2026-07-17T09:00:00Z",
                reliability=1,
                description="最新生命体征",
            ),
        ],
        recommended_actions=["请求护理人员复核"],
        unresolved_questions=[],
        tool_calls=[
            ToolExecutionResult(
                success=True,
                verified=True,
                tool_name="read_health_summary",
                arguments={"resident_id": "elderly-001"},
                result={"fresh": False},
            ),
            ToolExecutionResult(
                success=True,
                verified=True,
                tool_name="read_latest_vitals",
                arguments={"resident_id": "elderly-001"},
                result={"heart_rate": 104},
            ),
        ],
    ).model_dump(mode="json")


def test_specialist_create_agent_runs_multi_step_react_and_returns_finding() -> None:
    model = ScriptedChatModel(responses=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "read_health_summary",
                "args": {"resident_id": "elderly-001"},
                "id": "summary-call",
                "type": "tool_call",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "read_latest_vitals",
                "args": {"resident_id": "elderly-001"},
                "id": "vitals-call",
                "type": "tool_call",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "AgentFindingPayload",
                "args": _finding_payload(),
                "id": "finding-call",
                "type": "tool_call",
            }],
        ),
    ])
    agent = build_specialist_agent(
        model,
        get_agent_spec("health_agent"),
        [
            read_health_summary,
            read_latest_vitals,
            unlock_medicine_cabinet,
        ],
    )

    result = agent.invoke({
        "messages": [{"role": "user", "content": "检查张爷爷当前健康状态"}],
    })

    finding = AgentFindingPayload.model_validate(result["structured_response"])
    assert finding.status == "completed"
    assert [item.source for item in finding.evidence] == [
        "read_health_summary",
        "read_latest_vitals",
    ]
    assert model.index == 3
    assert all(
        "unlock_medicine_cabinet" not in names
        for names in model.bound_tool_names
    )


def test_specialist_enforces_model_call_budget() -> None:
    model = ScriptedChatModel(responses=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "read_health_summary",
                "args": {"resident_id": "elderly-001"},
                "id": "summary-call",
                "type": "tool_call",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "read_latest_vitals",
                "args": {"resident_id": "elderly-001"},
                "id": "vitals-call",
                "type": "tool_call",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "AgentFindingPayload",
                "args": _finding_payload(),
                "id": "finding-call",
                "type": "tool_call",
            }],
        ),
    ])
    agent = build_specialist_agent(
        model,
        get_agent_spec("health_agent"),
        [read_health_summary, read_latest_vitals],
        max_model_calls=2,
        max_tool_calls=8,
    )

    with pytest.raises(ModelCallLimitExceededError):
        agent.invoke({
            "messages": [{"role": "user", "content": "检查健康状态"}],
        })

    assert model.index == 2


def test_specialist_enforces_tool_call_budget() -> None:
    model = ScriptedChatModel(responses=[
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_health_summary",
                    "args": {"resident_id": "elderly-001"},
                    "id": "summary-call",
                    "type": "tool_call",
                },
                {
                    "name": "read_latest_vitals",
                    "args": {"resident_id": "elderly-001"},
                    "id": "vitals-call",
                    "type": "tool_call",
                },
            ],
        ),
    ])
    agent = build_specialist_agent(
        model,
        get_agent_spec("health_agent"),
        [read_health_summary, read_latest_vitals],
        max_model_calls=3,
        max_tool_calls=1,
    )

    with pytest.raises(ToolCallLimitExceededError):
        agent.invoke({
            "messages": [{"role": "user", "content": "检查健康状态"}],
        })


def test_supervisor_create_agent_delegates_to_specialist() -> None:
    child_model = ScriptedChatModel(responses=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "AgentFindingPayload",
                "args": _finding_payload(),
                "id": "finding-call",
                "type": "tool_call",
            }],
        ),
    ])
    health_agent = build_specialist_agent(
        child_model,
        get_agent_spec("health_agent"),
        [read_health_summary, read_latest_vitals],
    )
    delegation_tool = build_delegation_tool(
        get_agent_spec("health_agent"),
        health_agent,
    )
    supervisor_model = ScriptedChatModel(responses=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "delegate_health_agent",
                "args": {
                    "objective": "检查张爷爷当前健康状态",
                    "expected_evidence": ["最新生命体征", "数据时间"],
                },
                "id": "delegate-call",
                "type": "tool_call",
            }],
        ),
        AIMessage(content="最新心率为 104 bpm，建议护理人员复核。"),
    ])
    supervisor = build_supervisor_agent(
        supervisor_model,
        [delegation_tool],
    )

    result = asyncio.run(supervisor.ainvoke({
        "messages": [{"role": "user", "content": "查看张爷爷现在怎么样"}],
    }))

    assert result["messages"][-1].content == "最新心率为 104 bpm，建议护理人员复核。"
    assert supervisor_model.index == 2
    assert child_model.index == 1
    delegation_message = next(
        item for item in result["messages"] if item.type == "tool"
    )
    assert '"agent": "health_agent"' in delegation_message.content
    assert '"verified": true' in delegation_message.content


def test_health_tool_adapter_preserves_scoped_gateway_result() -> None:
    class FakeScopedGateway:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def call(
            self,
            tool_name: str,
            arguments: dict[str, Any],
        ) -> ToolExecutionResult:
            self.calls.append((tool_name, arguments))
            return ToolExecutionResult(
                success=True,
                verified=True,
                tool_name=tool_name,
                arguments=arguments,
                result={"heart_rate": 82},
                observed_state={"heart_rate": 82},
            )

    gateway = FakeScopedGateway()
    tools = {item.name: item for item in build_health_tools(gateway)}

    result = asyncio.run(tools["read_latest_vitals"].ainvoke({
        "resident_id": "elderly-001",
    }))

    assert gateway.calls == [(
        "read_latest_vitals",
        {"resident_id": "elderly-001"},
    )]
    assert result["success"] is True
    assert result["verified"] is True
    assert result["observed_state"] == {"heart_rate": 82}


def test_run_supervisor_creates_structured_parallel_task_dag() -> None:
    decision = {
        "action": "create_tasks",
        "goal": "同时调查张爷爷健康和房间环境",
        "context_summary": "用户要求综合状态",
        "execution_strategy": "parallel_when_safe",
        "tasks": [
            {
                "local_id": "health",
                "agent_id": "health_agent",
                "objective": "调查最新健康状态",
                "reason": "需要健康证据",
                "expected_evidence": ["最新生命体征"],
                "success_criteria": ["获得最新健康数据"],
                "depends_on": [],
                "resource_claims": [{
                    "key": "resident:elderly-001",
                    "mode": "read",
                }],
            },
            {
                "local_id": "environment",
                "agent_id": "environment_agent",
                "objective": "调查房间环境",
                "reason": "需要环境证据",
                "expected_evidence": ["温度", "湿度"],
                "success_criteria": ["获得房间环境状态"],
                "depends_on": [],
                "resource_claims": [{
                    "key": "room:A-101",
                    "mode": "read",
                }],
            },
        ],
        "success_criteria": ["健康与环境均有证据"],
        "constraints": ["不得进行医疗诊断"],
        "reason": "两个调查互不依赖，可建议并行",
    }
    model = ScriptedChatModel(responses=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "SupervisorDecision",
                "args": decision,
                "id": "supervisor-decision",
                "type": "tool_call",
            }],
        ),
    ])
    supervisor = build_run_supervisor_agent(
        model,
        {
            "health_agent": get_agent_spec("health_agent"),
            "environment_agent": get_agent_spec("environment_agent"),
        },
    )

    result = supervisor.invoke({
        "messages": [{
            "role": "user",
            "content": "查看张爷爷现在怎么样，也检查房间环境",
        }],
    })

    parsed = SupervisorDecision.model_validate(result["structured_response"])
    assert parsed.action == "create_tasks"
    assert parsed.execution_strategy == "parallel_when_safe"
    assert [item.agent_id for item in parsed.tasks] == [
        "health_agent",
        "environment_agent",
    ]
    assert parsed.task_plan().tasks[0].local_id == "health"
    assert "最小必要任务原则" in model.captured_messages[0][0].content
    assert (
        "同一个专业 Agent 的能力范围，应优先创建一个"
        in model.captured_messages[0][0].content
    )
    assert (
        "resource_claims 仍应根据每个 Task 可能访问或改变的资源，由你"
        in model.captured_messages[0][0].content
    )
    assert "最小必要证据原则" in get_agent_spec(
        "health_agent"
    ).system_prompt


def test_run_supervisor_can_be_reused_across_independent_runs() -> None:
    def response(goal: str) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[{
                "name": "SupervisorDecision",
                    "args": {
                        "action": "finish",
                        "goal": goal,
                        "goal_completed": True,
                        "final_response": f"{goal}已处理",
                        "reason": "无需创建专业任务",
                    },
                "id": f"decision-{goal}",
                "type": "tool_call",
            }],
        )

    model = ScriptedChatModel(responses=[
        response("第一轮"),
        response("第二轮"),
    ])
    supervisor = build_run_supervisor_agent(
        model,
        {"environment_agent": get_agent_spec("environment_agent")},
    )

    first = supervisor.invoke({
        "messages": [{"role": "user", "content": "第一轮"}],
    })
    second = supervisor.invoke({
        "messages": [{"role": "user", "content": "第二轮"}],
    })

    assert first["structured_response"].goal == "第一轮"
    assert second["structured_response"].goal == "第二轮"
    assert model.index == 2
