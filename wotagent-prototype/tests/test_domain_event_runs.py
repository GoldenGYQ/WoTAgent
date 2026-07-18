from __future__ import annotations

import asyncio

from wotagent.application import DomainEventConsumer, RunService
from wotagent.agents import AgentFinding, Evidence
from wotagent.conversation import ConversationStore
from wotagent.domains.eldercare import (
    EldercareMonitorService,
    EldercareStore,
    Resident,
    SensorReading,
)
from wotagent.orchestration import (
    InMemoryRunStore,
    RunCoordinator,
    SupervisorDecision,
    TaskScheduler,
)


class EmptyGateway:
    async def query_state(self, _device_id):
        return {}


def test_monitor_outbox_creates_a_source_attested_run(tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
    ))
    monitor = EldercareMonitorService(store, EmptyGateway())
    asyncio.run(monitor.ingest(SensorReading(
        resident_id="elderly-001",
        sensor_type="fall_detected",
        value=True,
    )))

    conversations = ConversationStore(tmp_path / "conversations.db")
    runs = InMemoryRunStore()

    def coordinator_factory(_context):
        decisions = iter([
            SupervisorDecision(
                action="create_tasks",
                goal="处理跌倒事件",
                tasks=[{
                    "local_id": "care",
                    "agent_id": "care_agent",
                    "objective": "通知护理员复核跌倒事件",
                    "reason": "需要人工复核",
                    "success_criteria": ["护理任务已创建"],
                }],
                success_criteria=["护理任务已创建"],
                reason="委派照护 Agent",
            ),
            SupervisorDecision(
                action="finish",
                goal="处理跌倒事件",
                final_response="风险事件已进入人工处置流程。",
                reason="照护任务已经创建",
            ),
        ])

        async def supervisor(_state):
            return next(decisions)

        async def care_executor(task):
            return AgentFinding(
                agent="care_agent",
                objective=task.objective,
                status="completed",
                finding="护理任务已创建。",
                confidence=1,
                evidence=[Evidence(
                    source="care-task",
                    value={"status": "open"},
                    reliability=1,
                    description="护理任务记录",
                )],
            )

        return RunCoordinator(
            runs,
            TaskScheduler(available_agents={"care_agent"}),
            supervisor,
            {"care_agent": care_executor},
        )

    service = RunService(conversations, runs, coordinator_factory)
    consumer = DomainEventConsumer(store, service)

    results = asyncio.run(consumer.consume_once())

    assert len(results) == 1
    run = asyncio.run(runs.get_run(results[0]["run_id"]))
    assert run is not None
    assert run.source == "monitor"
    assert run.domain_id == "eldercare"
    assert run.user_id == "system:eldercare-monitor"
    assert run.source_context["target"]["id"] == "elderly-001"
    risk = store.list_risks("elderly-001")[0]
    assert risk.run_id == run.run_id
    assert store.list_outbox()[0]["status"] == "processed"
    source_event = asyncio.run(runs.events(run.run_id))[0]
    assert source_event.type == "run.source.received"
