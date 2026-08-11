from __future__ import annotations

import asyncio
import os
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from wotagent.agents import AgentFindingPayload, Evidence, ToolExecutionResult
from wotagent.domains.eldercare.health_gateway import ScopedHealthMCPClient
from wotagent.domains.eldercare.models import Resident, SensorReading
from wotagent.domains.eldercare.store import EldercareStore
from wotagent.gateway.mcp import MCPDeviceGateway
from wotagent.orchestration import (
    InMemoryRunStore,
    ResourceScope,
    RunCoordinator,
    SupervisorDecision,
    TaskScheduler,
)
from wotagent.tools.mcp_client import reset_mcp_client

pytestmark = pytest.mark.simulator


def _simulator_available() -> bool:
    try:
        with urlopen("http://127.0.0.1:18080/api/devices", timeout=2) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def test_run_coordinator_reads_health_and_simulator_in_parallel(
    tmp_path,
) -> None:
    if os.getenv("RUN_SIMULATOR_AGENT_TESTS") != "1":
        pytest.skip("set RUN_SIMULATOR_AGENT_TESTS=1 to run simulator smoke")
    if not _simulator_available():
        pytest.skip("wot-device-simulator is not running on port 18080")

    async def scenario():
        eldercare_store = EldercareStore(tmp_path / "eldercare.db")
        eldercare_store.upsert_resident(Resident(
            resident_id="elderly-001",
            name="Zhang",
            room="A-101",
            caregiver="JH-1",
            environment_device_id="ac-002",
            wristband_device_id="wristband-001",
            mattress_device_id="mattress-001",
            environment_sensor_device_id="environment-sensor-001",
        ))
        eldercare_store.add_reading(SensorReading(
            resident_id="elderly-001",
            sensor_type="heart_rate",
            value=78,
            unit="bpm",
            source="simulator-smoke",
        ))
        health_client = ScopedHealthMCPClient(
            eldercare_store,
            require_scope=True,
        )
        health_scope = ResourceScope(
            resource_ids=["resident:elderly-001"],
            capabilities=["health.summary.read"],
        )
        device_gateway = MCPDeviceGateway()

        active = 0
        maximum = 0
        lock = asyncio.Lock()

        async def enter() -> None:
            nonlocal active, maximum
            async with lock:
                active += 1
                maximum = max(maximum, active)

        async def leave() -> None:
            nonlocal active
            async with lock:
                active -= 1

        async def health_executor(_task):
            await enter()
            try:
                with health_client.authorized_scope(health_scope):
                    result = await health_client.call(
                        "read_health_summary",
                        {"resident_id": "elderly-001"},
                    )
                return AgentFindingPayload(
                    status="completed" if result.success else "failed",
                    finding="health summary read through scoped MCP resource",
                    confidence=1,
                    evidence=[Evidence(
                        source="eldercare-mcp",
                        value=result.result,
                        reliability=1,
                        description="scoped resident health summary",
                    )],
                    tool_calls=[ToolExecutionResult.model_validate(
                        result.model_dump(mode="json")
                    )],
                )
            finally:
                await leave()

        async def environment_executor(_task):
            await enter()
            try:
                state = await device_gateway.query_state(
                    "environment-sensor-001"
                )
                return AgentFindingPayload(
                    status="completed",
                    finding="environment state read through TD-derived MCP tool",
                    confidence=1,
                    evidence=[Evidence(
                        source="wot-device-simulator",
                        value=state,
                        reliability=1,
                        description="A-101 environment sensor state",
                    )],
                    tool_calls=[ToolExecutionResult(
                        success=True,
                        verified=True,
                        tool_name=str(state.get("_mcp_tool", "")),
                        arguments={
                            "device_id": "environment-sensor-001",
                        },
                        result=state,
                        observed_state=state,
                    )],
                )
            finally:
                await leave()

        decisions = iter([
            SupervisorDecision(
                action="create_tasks",
                goal="inspect resident health and room environment",
                execution_strategy="parallel_when_safe",
                tasks=[
                    {
                        "local_id": "health",
                        "agent_id": "health_agent",
                        "objective": "read resident health summary",
                        "reason": "health evidence is required",
                        "success_criteria": ["health summary is available"],
                        "resource_claims": [{
                            "key": "resident:elderly-001",
                            "mode": "read",
                        }],
                    },
                    {
                        "local_id": "environment",
                        "agent_id": "environment_agent",
                        "objective": "read A-101 environment state",
                        "reason": "environment evidence is required",
                        "success_criteria": ["environment state is available"],
                        "resource_claims": [{
                            "key": "device:environment-sensor-001",
                            "mode": "read",
                        }],
                    },
                ],
                success_criteria=[
                    "health and environment evidence are both available"
                ],
                reason="two independent specialists can investigate in parallel",
            ),
            SupervisorDecision(
                action="finish",
                goal_completed=True,
                goal="inspect resident health and room environment",
                final_response="health and environment checks completed",
                reason="both specialist findings contain verified evidence",
            ),
        ])
        store = InMemoryRunStore()
        task_ids = iter(["task-health", "task-environment"])
        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={"health_agent", "environment_agent"},
                task_id_factory=lambda: next(task_ids),
            ),
            lambda _state: next(decisions),
            {
                "health_agent": health_executor,
                "environment_agent": environment_executor,
            },
            run_id_factory=lambda: "run-simulator-multi-agent",
        )
        run = await coordinator.execute(
            conversation_id="conv-simulator",
            turn_id="turn-simulator",
            message="inspect Zhang's health and A-101 environment",
        )
        return (
            run,
            await store.tasks_for_run(run.run_id),
            await store.events(run.run_id),
            maximum,
        )

    reset_mcp_client()
    try:
        run, tasks, events, maximum = asyncio.run(scenario())
    finally:
        reset_mcp_client()

    assert run.status == "completed"
    assert maximum == 2
    assert {task.agent_id for task in tasks} == {
        "health_agent",
        "environment_agent",
    }
    assert all(task.status == "completed" for task in tasks)
    assert all(
        task.finding is not None and task.finding.tool_calls
        for task in tasks
    )
    environment_task = next(
        task for task in tasks if task.agent_id == "environment_agent"
    )
    observed = environment_task.finding.evidence[0].value
    assert isinstance(observed, dict)
    assert observed["_mcp_tool"].endswith("__read_state")
    batch = next(
        event
        for event in events
        if event.type == "scheduler.parallel_batch.completed"
    )
    assert batch.data["parallel"] is True
