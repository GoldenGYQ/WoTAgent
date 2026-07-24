from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks

from wotagent.application import DomainEventConsumer, RunService
from wotagent.agents import AgentFinding, Evidence
from wotagent.conversation import ConversationStore
from wotagent.domain import DomainEventEnvelope, InMemoryDomainEventStore
from wotagent.domains.eldercare import (
    EldercareDomainEventSource,
    EldercareMonitorService,
    EldercareStore,
    Resident,
    SensorReading,
)
from wotagent.orchestration import (
    InMemoryRunStore,
    RunCoordinator,
    RunRecord,
    SupervisorDecision,
    TaskScheduler,
)


def test_domain_scenario_creates_an_accepted_platform_run(tmp_path) -> None:
    conversations = ConversationStore(tmp_path / "conversations.db")
    runs = InMemoryRunStore()
    background_tasks = BackgroundTasks()
    service = RunService(
        conversations,
        runs,
        lambda _context: None,
    )

    run = asyncio.run(service.create_domain_scenario_run(
        message="对整栋别墅执行一次只读运行体检。",
        domain_id="smart_home",
        scenario="whole_house_check",
        source_context={
            "subject": {"type": "site", "id": "villa-demo"},
            "preferred_agents": [
                "environment_agent",
                "security_agent",
                "energy_agent",
                "device_ops_agent",
            ],
        },
        user_id="JH-1",
        background_tasks=background_tasks,
    ))

    assert run.status == "accepted"
    assert run.source == "system"
    assert run.domain_id == "smart_home"
    assert run.profile_id == "villa"
    assert run.source_context["type"] == "domain_scenario"
    assert run.source_context["scenario"] == "whole_house_check"
    assert len(background_tasks.tasks) == 1
    events = asyncio.run(runs.events(run.run_id))
    assert events[0].type == "run.source.received"


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
                goal_completed=True,
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
    consumer = DomainEventConsumer(
        [EldercareDomainEventSource(store)],
        service,
    )

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


def test_failed_event_retry_does_not_create_a_second_run(tmp_path) -> None:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
    ))
    monitor = EldercareMonitorService(store, EmptyGateway())
    asyncio.run(monitor.ingest(SensorReading(
        resident_id="elderly-001",
        sensor_type="spo2",
        value=89,
    )))
    runs = InMemoryRunStore()

    class FailingRunService:
        calls = 0

        async def create_event_run(self, **kwargs):
            self.calls += 1
            run = RunRecord(
                run_id="run-event-failed",
                conversation_id="conv-event-failed",
                turn_id="turn-event-failed",
                root_goal=kwargs["message"],
                source="monitor",
                source_event_id=kwargs["source_event_id"],
                status="failed",
                final_response="health finding contract failed",
            )
            await runs.create_run(run)
            kwargs["on_created"](run)
            return run

        async def require_run(self, run_id):
            return await runs.get_run(run_id)

    service = FailingRunService()
    consumer = DomainEventConsumer(
        [EldercareDomainEventSource(store)],
        service,
    )

    first = asyncio.run(consumer.consume_once())
    outbox = store.list_outbox()[0]
    store.retry_event(
        outbox["outbox_id"],
        outbox["last_error"],
        delay_seconds=0,
    )
    second = asyncio.run(consumer.consume_once())

    assert first == []
    assert second == []
    assert service.calls == 1
    assert store.list_risks("elderly-001")[0].run_id == "run-event-failed"
    assert store.list_outbox()[0]["attempts"] == 2


def test_villa_event_uses_the_same_consumer_and_run_lifecycle(tmp_path) -> None:
    source = InMemoryDomainEventStore()
    source.publish(DomainEventEnvelope(
        event_id="villa-smoke-1",
        domain_id="smart_home",
        subject_id="villa-demo",
        event_type="smoke_alarm",
        severity="high",
        message="大厅烟雾浓度达到 75%。",
        source="smoke-sensor-001",
        run_goal="核查并处置别墅烟雾告警。",
        source_context={
            "target": {"type": "site", "id": "villa-demo"},
            "event": {"event_type": "smoke_alarm", "severity": "high"},
        },
        principal_id="system:villa-monitor",
    ))
    conversations = ConversationStore(tmp_path / "villa-conversations.db")
    runs = InMemoryRunStore()
    decisions = iter([
        SupervisorDecision(
            action="create_tasks",
            goal="处置烟雾告警",
            tasks=[{
                "local_id": "security-check",
                "agent_id": "security_agent",
                "objective": "核查烟雾传感器和燃气状态",
                "reason": "需要安防证据",
                "success_criteria": ["获得现场安全状态"],
            }],
            success_criteria=["完成安全核查"],
            reason="委派安防 Agent",
        ),
        SupervisorDecision(
            action="finish",
            goal_completed=True,
            goal="处置烟雾告警",
            final_response="烟雾告警已核查。",
            satisfied_criteria=["完成安全核查"],
            reason="已取得安防证据",
        ),
    ])

    async def execute(task):
        return AgentFinding(
            agent=task.agent_id,
            objective=task.objective,
            status="completed",
            finding="烟雾传感器告警，已完成现场核查。",
            confidence=1,
            evidence=[Evidence(
                source="smoke-sensor-001",
                value={"alarm": True},
                reliability=1,
                description="烟雾设备状态",
            )],
        )

    def coordinator_factory(_context):
        return RunCoordinator(
            runs,
            TaskScheduler(available_agents={"security_agent"}),
            lambda _state: next(decisions),
            {"security_agent": execute},
        )

    service = RunService(conversations, runs, coordinator_factory)
    consumer = DomainEventConsumer([source], service)

    results = asyncio.run(consumer.consume_once())

    assert results[0]["domain_id"] == "smart_home"
    run = asyncio.run(runs.get_run(results[0]["run_id"]))
    assert run is not None
    assert run.status == "completed"
    assert run.user_id == "system:villa-monitor"
    assert source.get_event("villa-smoke-1").run_id == run.run_id
    assert source.claim_events() == []
