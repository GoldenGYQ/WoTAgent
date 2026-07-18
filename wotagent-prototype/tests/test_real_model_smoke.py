"""Opt-in real-model smoke tests; never run without explicit acknowledgement."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest
from langchain_core.callbacks import UsageMetadataCallbackHandler

from wotagent.agents import AGENT_SPECS, build_run_supervisor_agent
from wotagent.domains.eldercare.models import SensorReading
from wotagent.orchestration import InMemoryRunStore, RunRecord
from wotagent.runtime import build_run_coordinator, create_runtime_container


ACK = "I_UNDERSTAND_THIS_USES_EXTERNAL_TOKENS"


class MeteredAgent:
    def __init__(
        self,
        agent: Any,
        callback: UsageMetadataCallbackHandler,
    ) -> None:
        self.agent = agent
        self.callback = callback

    async def ainvoke(self, value: Any) -> Any:
        return await self.agent.ainvoke(
            value,
            config={"callbacks": [self.callback]},
        )


@pytest.mark.real_model
@pytest.mark.skipif(
    os.getenv("WOTAGENT_REAL_MODEL_SMOKE_ACK") != ACK,
    reason="real-model smoke test requires explicit token-use acknowledgement",
)
def test_real_model_health_chain_and_resident_scope(
    monkeypatch,
    tmp_path,
) -> None:
    async def scenario():
        monkeypatch.setenv("BUTLER_MAX_ITERATIONS", "2")
        monkeypatch.setenv("HEALTH_AGENT_MAX_ITERATIONS", "4")
        monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "4")
        monkeypatch.setenv("AGENT_TOTAL_TIMEOUT_SECONDS", "20")
        monkeypatch.setenv("AGENT_LLM_TIMEOUT_SECONDS", "10")
        monkeypatch.setenv("WOTAGENT_MAX_OUTPUT_TOKENS", "900")
        monkeypatch.setenv(
            "WOTAGENT_CONVERSATION_DB",
            str(tmp_path / "conversations.db"),
        )
        monkeypatch.setenv(
            "ELDERCARE_DB",
            str(tmp_path / "eldercare.db"),
        )
        runtime = create_runtime_container(
            enable_thinking=False,
            enable_streaming=False,
            temperature=0,
        )
        for resident_id, heart_rate, spo2 in (
            ("elderly-001", 82, 97),
            ("elderly-002", 78, 98),
        ):
            runtime.eldercare_store.add_reading(SensorReading(
                resident_id=resident_id,
                sensor_type="heart_rate",
                value=heart_rate,
                unit="bpm",
                source="real-model-smoke-fixture",
            ))
            runtime.eldercare_store.add_reading(SensorReading(
                resident_id=resident_id,
                sensor_type="spo2",
                value=spo2,
                unit="%",
                source="real-model-smoke-fixture",
            ))
            runtime.eldercare_store.add_reading(SensorReading(
                resident_id=resident_id,
                sensor_type="sleep_quality",
                value="good",
                source="real-model-smoke-fixture",
            ))
        usage = UsageMetadataCallbackHandler()
        runtime.run_supervisor_agent = build_run_supervisor_agent(
            runtime.pipeline.response_model,
            {"health_agent": AGENT_SPECS["health_agent"]},
        )
        runtime.run_supervisor_agent = MeteredAgent(
            runtime.run_supervisor_agent,
            usage,
        )
        runtime.health_react_agent = MeteredAgent(
            runtime.health_react_agent,
            usage,
        )
        runtime.health_finding_repair_runnable = MeteredAgent(
            runtime.health_finding_repair_runnable,
            usage,
        )
        store = InMemoryRunStore()
        cases = [
            (
                "assigned-zhang",
                "JH-1",
                "查看张爷爷今天的健康状态",
                "elderly-001",
                False,
            ),
            (
                "assigned-li",
                "JH-2",
                "查看李奶奶最近的生命体征",
                "elderly-002",
                False,
            ),
            (
                "denied-cross-resident",
                "JH-2",
                "查看张爷爷今天的健康状态",
                "elderly-001",
                True,
            ),
        ]
        selected_case = os.getenv("WOTAGENT_REAL_MODEL_CASE", "").strip()
        if selected_case:
            cases = [
                item for item in cases
                if item[0] == selected_case
            ]
            assert cases, f"unknown real-model case: {selected_case}"
        results = []
        for case_id, user_id, message, target_id, denied in cases:
            run_id = f"run-real-{case_id}"
            conversation_id = f"conv-real-{case_id}"
            turn_id = f"turn-real-{case_id}"
            await store.create_run(RunRecord(
                run_id=run_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                root_goal=message,
                user_id=user_id,
                role="operator",
                status="accepted",
            ))
            coordinator = build_run_coordinator(
                runtime,
                store,
                {
                    "user_id": user_id,
                    "role": "operator",
                    "thinking": False,
                    "history": [],
                    "runtime": {},
                },
                {},
            )
            run = await coordinator.execute(
                run_id=run_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                message=message,
            )
            tasks = await store.tasks_for_run(run.run_id)
            health_tasks = [
                item for item in tasks
                if item.agent_id == "health_agent"
            ]
            assert health_tasks, f"{case_id}: Health Task was not created"
            first = health_tasks[0]
            assert first.target is not None
            assert first.target.id == target_id
            if denied:
                assert run.status == "failed"
                assert all(
                    item.status != "completed"
                    for item in health_tasks
                )
                assert first.failure is not None
                assert first.failure.code == "RESOURCE_ACCESS_DENIED"
            else:
                task_diagnostics = [
                    {
                        "agent_id": item.agent_id,
                        "objective": item.objective,
                        "status": item.status,
                        "failure": (
                            item.failure.model_dump(mode="json")
                            if item.failure else None
                        ),
                    }
                    for item in tasks
                ]
                assert run.status == "completed", json.dumps(
                    task_diagnostics,
                    ensure_ascii=False,
                    default=str,
                )
                assert first.effective_scope is not None
                assert first.finding is not None
                assert first.finding.agent == "health_agent"
                assert first.status == "completed", first.model_dump_json(
                    indent=2
                )
            results.append({
                "case": case_id,
                "run_status": run.status,
                "task_statuses": [
                    item.status for item in health_tasks
                ],
                "all_tasks": [
                    {
                        "agent_id": item.agent_id,
                        "status": item.status,
                        "failure_code": (
                            item.failure.code if item.failure else None
                        ),
                    }
                    for item in tasks
                ],
                "target_id": first.target.id,
                "failure_code": (
                    first.failure.code if first.failure else None
                ),
            })
        return results, usage.usage_metadata

    results, usage = asyncio.run(scenario())

    print(json.dumps(
        {"cases": results, "usage": usage},
        ensure_ascii=False,
        default=str,
    ))
    assert usage
