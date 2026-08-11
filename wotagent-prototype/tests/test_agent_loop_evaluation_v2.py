"""Executable Agent Loop contracts plus forward-looking loop datasets."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from wotagent.agent_graph import AgentGraphDependencies, build_agent_graph
from wotagent.domain import PlanStep, TaskPlan
from wotagent.evaluation import load_loop_cases, score_loop_trace
from wotagent.orchestration import TaskOrchestrator
from wotagent.policy import PolicyEngine
from wotagent.telemetry import InMemoryTraceStore

DATASET = Path(__file__).parents[1] / "data" / "evaluation" / "agent_loop_v2.json"


def loop_cases():
    return load_loop_cases(DATASET)


def test_loop_dataset_is_unique_bounded_and_requires_multiple_iterations() -> None:
    cases = loop_cases()

    assert len(cases) == 6
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.expected.min_iterations >= 2 for case in cases)
    assert all(case.expected.max_iterations <= 3 for case in cases)
    assert {case.support for case in cases} == {"implemented", "target"}


def test_loop_scorer_rejects_a_single_pass_trace() -> None:
    case = next(case for case in loop_cases() if case.case_id == "verification_miss_then_recover")

    score = score_loop_trace(
        case,
        [{"type": "agent.loop.completed", "iteration": 1}],
        final_status="succeeded",
    )

    assert score["passed"] is False
    assert score["checks"]["min_iterations"] is False
    assert "agent.recovery.completed" in score["missing_events"]


class FaultGateway:
    name = "loop-fault-gateway"

    def __init__(self, fault_type: str) -> None:
        self.fault_type = fault_type
        self.control_calls = 0
        self.state = {"on": False}

    async def query_state(self, device_id: str) -> dict[str, Any]:
        return dict(self.state)

    async def control_device(
        self,
        device_id: str,
        action: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.control_calls += 1
        if self.fault_type == "tool_failure" and self.control_calls == 1:
            raise ConnectionError("injected first-call failure")
        if self.fault_type == "persistent_failure":
            raise ConnectionError("injected persistent failure")
        if not (self.fault_type == "verification_miss" and self.control_calls == 1):
            self.state["on"] = True
        return {"success": True, "idempotency_key": idempotency_key}


@pytest.mark.parametrize(
    "case_id",
    [
        "verification_miss_then_recover",
        "tool_failure_then_recover",
        "persistent_failure_stops_at_bound",
    ],
)
def test_implemented_recovery_cases_execute_two_real_graph_iterations(case_id: str) -> None:
    case = next(case for case in loop_cases() if case.case_id == case_id)

    async def scenario():
        gateway = FaultGateway(case.fault.type)
        plan = TaskPlan(intent="control", summary="打开客厅灯", steps=[PlanStep(
            id="turn-on",
            kind="action",
            device_id="light-001",
            action="turnOn",
        )])

        async def planner(_state):
            return plan

        async def recovery_planner(_state):
            return plan.model_copy(deep=True)

        store = InMemoryTraceStore()
        task_id = f"loop-{case_id}"
        await store.create(task_id=task_id, session_id="loop-eval", request_id=task_id)
        policy = PolicyEngine()
        graph = build_agent_graph(AgentGraphDependencies(
            planner=planner,
            recovery_planner=recovery_planner,
            max_recovery_attempts=1,
            policy=policy,
            orchestrator=TaskOrchestrator(gateway, policy),
            trace_store=store,
        ))
        result = await graph.ainvoke({
            "task_id": task_id,
            "message": case.turns[0].message,
            "role": case.turns[0].role,
        })
        events = await store.events(task_id)
        record = await store.get(task_id)
        return gateway, result, events, record

    gateway, result, events, record = asyncio.run(scenario())
    status = result["trace"].status.value
    score = score_loop_trace(case, events, final_status=status)

    assert score["passed"] is True
    assert record.loop_count == 2
    assert record.recovery_count == 1
    assert gateway.control_calls == 2


def test_unimplemented_loop_contracts_are_not_misreported_as_supported() -> None:
    targets = {case.case_id: case for case in loop_cases() if case.support == "target"}

    assert set(targets) == {
        "partial_batch_replans_failed_subset",
        "successful_task_correction_executes_diff",
        "clarification_resumes_same_task",
    }
    assert "plan.diff.generated" in targets[
        "successful_task_correction_executes_diff"
    ].expected.required_events
    assert "task.clarification.resumed" in targets[
        "clarification_resumes_same_task"
    ].expected.required_events
