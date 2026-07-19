from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from langgraph.types import Command

from wotagent.agents import (
    AgentFindingPayload,
    Evidence,
    ToolExecutionResult,
)
from wotagent.orchestration import (
    AgentTask,
    InMemoryRunStore,
    PendingConfirmation,
    ResourceScope,
    RunRecord,
    TaskTarget,
)
from wotagent.runtime.run_runtime import (
    _device_executor,
    _device_resumer,
    _health_executor,
)
from wotagent.telemetry import InMemoryTraceStore


def test_device_executor_surfaces_and_resumes_native_langgraph_interrupt() -> None:
    async def scenario():
        class TraceStore:
            record = None

            async def get(self, _task_id):
                return self.record

            async def create(self, **kwargs):
                self.record = kwargs

        class Graph:
            def __init__(self):
                self.calls = []

            async def ainvoke(self, value, config=None):
                self.calls.append((value, config))
                if isinstance(value, Command):
                    return {
                        "status": "succeeded",
                        "response": "客厅灯已打开并通过状态验证。",
                    }
                return {
                    "__interrupt__": [SimpleNamespace(value={
                        "type": "confirmation_required",
                        "summary": "打开客厅灯",
                    })],
                }

        graph = Graph()
        runtime = SimpleNamespace(
            settings=SimpleNamespace(total_timeout_seconds=2),
            trace_store=TraceStore(),
        )
        task = AgentTask(
            task_id="task-confirm",
            run_id="run-confirm",
            local_id="turn-on",
            agent_id="environment_agent",
            objective="打开客厅灯",
            reason="用户请求",
            success_criteria=["灯状态为开启"],
        )
        executor = _device_executor(
            runtime,
            graph,
            {
                "user_id": "local-user",
                "role": "operator",
                "thinking": False,
                "history": [],
                "runtime": {},
            },
        )

        pending = await executor(task)
        assert isinstance(pending, PendingConfirmation)
        finding = await _device_resumer(runtime, graph)(task, True)
        return pending, finding, graph.calls

    pending, finding, calls = asyncio.run(scenario())

    assert pending.payload["summary"] == "打开客厅灯"
    assert finding.status == "completed"
    assert isinstance(calls[1][0], Command)
    assert calls[0][1]["configurable"]["thread_id"] == "task-confirm"
    assert calls[1][1]["configurable"]["thread_id"] == "task-confirm"


def test_device_trace_events_are_projected_into_run_event_stream() -> None:
    async def scenario():
        trace_store = InMemoryTraceStore()
        run_store = InMemoryRunStore()
        await run_store.create_run(RunRecord(
            run_id="run-trace",
            conversation_id="conv-1",
            turn_id="turn-1",
            root_goal="读取客厅灯",
        ))

        class Graph:
            async def ainvoke(self, _value, config=None):
                await trace_store.record(
                    task_id="task-trace",
                    type="agent.tool.completed",
                    node="executor",
                    data={
                        "step_id": "step-1",
                        "tool": "light_001__read_state",
                        "input": {
                            "device_id": "light-001",
                            "action": "read_state",
                        },
                        "output": {
                            "success": True,
                            "raw_result": {"on": False},
                        },
                    },
                )
                return {
                    "status": "succeeded",
                    "response": "客厅灯当前关闭。",
                }

        runtime = SimpleNamespace(
            settings=SimpleNamespace(total_timeout_seconds=2),
            trace_store=trace_store,
        )
        task = AgentTask(
            task_id="task-trace",
            run_id="run-trace",
            local_id="inspect",
            agent_id="environment_agent",
            objective="读取客厅灯",
            reason="用户请求",
            success_criteria=["获得客厅灯状态"],
        )
        await _device_executor(
            runtime,
            Graph(),
            {
                "user_id": "local-user",
                "role": "operator",
                "thinking": False,
                "history": [],
                "runtime": {},
            },
            run_store,
        )(task)
        return await run_store.events("run-trace")

    events = asyncio.run(scenario())

    assert [event.type for event in events] == ["agent.tool.completed"]
    assert events[0].task_id == "task-trace"
    assert events[0].tool_call_id == "step-1"
    assert events[0].data["input"] == {
        "device_id": "light-001",
        "action": "read_state",
    }
    assert events[0].data["output"]["raw_result"] == {"on": False}


def test_health_executor_repairs_missing_structured_response_once() -> None:
    async def scenario():
        class Gateway:
            @contextmanager
            def authorized_scope(self, _scope):
                yield

        class HealthAgent:
            calls = 0

            async def ainvoke(self, _value):
                self.calls += 1
                return {
                    "structured_response": None,
                    "messages": [
                        {
                            "role": "tool",
                            "name": "read_latest_vitals",
                            "content": '{"heart_rate":82}',
                        },
                        {
                            "role": "assistant",
                            "content": "最新心率为 82 bpm。",
                        },
                    ],
                }

        class RepairRunnable:
            calls = 0
            input = None

            async def ainvoke(self, value):
                self.calls += 1
                self.input = value
                return AgentFindingPayload(
                    status="completed",
                    finding="最新心率为 82 bpm。",
                    confidence=0.95,
                    evidence=[Evidence(
                        source="read_latest_vitals",
                        value={"heart_rate": 82},
                        reliability=1,
                        description="最新生命体征",
                    )],
                )

        agent = HealthAgent()
        repair = RepairRunnable()
        runtime = SimpleNamespace(
            health_gateway=Gateway(),
            health_react_agent=agent,
            health_finding_repair_runnable=repair,
            settings=SimpleNamespace(
                total_timeout_seconds=2,
                llm_timeout_seconds=2,
            ),
        )
        task = AgentTask(
            task_id="task-health",
            run_id="run-health",
            local_id="health",
            agent_id="health_agent",
            objective="获取最新生命体征",
            source_request="查看李奶奶最近的生命体征",
            reason="用户请求",
            success_criteria=["获得最新生命体征"],
            target=TaskTarget(
                type="resident",
                id="elderly-002",
                display_name="李奶奶",
            ),
            effective_scope=ResourceScope(
                resource_ids=["resident:elderly-002"],
                capabilities=["health.status.read"],
            ),
        )

        result = await _health_executor(runtime)(task)
        return result, agent, repair

    result, agent, repair = asyncio.run(scenario())

    assert result.status == "completed"
    assert agent.calls == 1
    assert repair.calls == 1
    assert "read_latest_vitals" in repair.input[1]["content"]


def test_health_executor_attests_verified_tool_transcript_without_repair() -> None:
    async def scenario():
        class Gateway:
            @contextmanager
            def authorized_scope(self, _scope):
                yield

        tool_result = ToolExecutionResult(
            success=True,
            verified=True,
            tool_name="read_latest_vitals",
            arguments={"resident_id": "elderly-001"},
            result={"spo2": 89, "heart_rate": 78},
            observed_state={"spo2": 89, "heart_rate": 78},
        )

        class HealthAgent:
            async def ainvoke(self, _value):
                return {
                    "structured_response": None,
                    "messages": [
                        {
                            "role": "tool",
                            "name": "read_latest_vitals",
                            "content": tool_result.model_dump_json(),
                        },
                        {
                            "role": "assistant",
                            "content": "当前血氧为 89%，需要护理人员复核。",
                        },
                    ],
                }

        class RepairRunnable:
            calls = 0

            async def ainvoke(self, _value):
                self.calls += 1
                raise AssertionError("verified transcript should not be repaired")

        repair = RepairRunnable()
        runtime = SimpleNamespace(
            health_gateway=Gateway(),
            health_react_agent=HealthAgent(),
            health_finding_repair_runnable=repair,
            settings=SimpleNamespace(
                total_timeout_seconds=2,
                llm_timeout_seconds=2,
            ),
        )
        task = AgentTask(
            task_id="task-health-transcript",
            run_id="run-health-transcript",
            local_id="health",
            agent_id="health_agent",
            objective="核查张爷爷当前血氧",
            source_request="处理血氧过低事件",
            reason="Monitor 风险事件",
            target=TaskTarget(
                type="resident",
                id="elderly-001",
                display_name="张爷爷",
            ),
            effective_scope=ResourceScope(
                resource_ids=["resident:elderly-001"],
                capabilities=["health.status.read"],
            ),
        )
        return await _health_executor(runtime)(task), repair

    result, repair = asyncio.run(scenario())

    assert result.status == "completed"
    assert result.finding == "当前血氧为 89%，需要护理人员复核。"
    assert result.evidence[0].value == {"spo2": 89, "heart_rate": 78}
    assert result.tool_calls[0].verified is True
    assert repair.calls == 0
