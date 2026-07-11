"""Tests for execution-aware final response generation and fallback."""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage

from wotagent.agent_graph import LangChainResponseComposer
from wotagent.domain import (
    Condition,
    ExecutionResult,
    PlanStep,
    PolicyDecision,
    StepTrace,
    TaskPlan,
    TaskStatus,
    TaskTrace,
    VerificationResult,
)
from wotagent.telemetry import InMemoryTraceStore


class FakeResponder:
    def __init__(self, content: str = "客厅灯已打开，并完成状态验证。") -> None:
        self.content = content
        self.context = None
        self.prompt = ""

    async def ainvoke(self, inputs, context=None):
        self.context = context
        self.prompt = inputs["messages"][0].content
        return {"messages": [AIMessage(
            content=self.content,
            usage_metadata={"input_tokens": 80, "output_tokens": 12, "total_tokens": 92},
            response_metadata={"model_name": "fake-responder"},
        )]}


def success_state() -> dict:
    step = PlanStep(
        id="s1",
        kind="action",
        device_id="light-001",
        action="turnOn",
        postconditions=[Condition(property="on", value=True)],
    )
    plan = TaskPlan(intent="control", summary="打开客厅灯", steps=[step])
    trace = TaskTrace(plan=plan, role="operator", status=TaskStatus.SUCCEEDED, steps=[StepTrace(
        step=step,
        policy=PolicyDecision(effect="allow", reason_code="OK", reason="allowed"),
        execution=ExecutionResult(step_id="s1", status="accepted"),
        verification=VerificationResult(step_id="s1", passed=True, observed_state={"on": True}),
    )])
    return {
        "task_id": "task-response",
        "session_id": "sess-response",
        "role": "operator",
        "message": "打开客厅灯",
        "plan": plan,
        "trace": trace,
        "policy_decisions": [PolicyDecision(effect="allow", reason_code="OK", reason="allowed")],
    }


def test_response_agent_receives_verified_execution_facts_and_records_usage() -> None:
    async def run():
        store = InMemoryTraceStore()
        await store.create(task_id="task-response", session_id="sess-response", request_id="req")
        runnable = FakeResponder()
        response = await LangChainResponseComposer(runnable, trace_store=store)(success_state())
        return response, runnable, await store.get("task-response")

    response, runnable, record = asyncio.run(run())

    assert response == "客厅灯已打开，并完成状态验证。"
    assert '"passed": true' in runnable.prompt
    assert '"status": "succeeded"' in runnable.prompt
    assert runnable.context.execution_results
    assert record.usage.model_calls == 1
    assert record.usage.total_tokens == 92


def test_empty_response_is_rejected_for_graph_fallback() -> None:
    with asyncio.Runner() as runner:
        try:
            runner.run(LangChainResponseComposer(FakeResponder(""))(success_state()))
        except ValueError as exc:
            assert "empty content" in str(exc)
        else:
            raise AssertionError("empty response should fail")
