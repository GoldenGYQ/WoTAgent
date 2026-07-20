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
    DependencyFinding,
    InMemoryRunStore,
    PendingConfirmation,
    ResourceScope,
    RunCoordinator,
    RunRecord,
    SupervisorDecision,
    TaskScheduler,
    TaskTarget,
)
from wotagent.runtime.run_runtime import (
    _care_executor,
    _device_executor,
    _device_resumer,
    _health_executor,
    _normalize_supervisor_user_request,
    _supervisor_decider,
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

    assert [event.type for event in events] == [
        "task.execution.context",
        "agent.tool.completed",
    ]
    assert events[1].task_id == "task-trace"
    assert events[1].tool_call_id == "step-1"
    assert events[1].data["input"] == {
        "device_id": "light-001",
        "action": "read_state",
    }
    assert events[1].data["output"]["raw_result"] == {"on": False}


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


def test_supervisor_decider_defers_conditional_care_task_until_health_finishes() -> None:
    async def scenario():
        store = InMemoryRunStore()
        await store.create_run(RunRecord(
            run_id="run-supervisor-guard",
            conversation_id="conv-1",
            turn_id="turn-1",
            root_goal="查看张爷爷健康状态，如异常通知护理员",
        ))

        class SupervisorAgent:
            async def ainvoke(self, _value):
                return {
                    "structured_response": {
                        "action": "create_tasks",
                        "goal": "查看张爷爷健康状态，如异常通知护理员",
                        "reason": "先查健康，再在异常时通知护理员",
                        "tasks": [
                            {
                                "local_id": "health",
                                "agent_id": "health_agent",
                                "objective": "查询张爷爷健康状态",
                                "reason": "先确认是否存在异常",
                                "success_criteria": ["获得当前健康结论"],
                                "depends_on": [],
                            },
                            {
                                "local_id": "care",
                                "agent_id": "care_agent",
                                "objective": "如果异常则通知护理员，否则不操作",
                                "reason": "仅在健康结果异常时才通知",
                                "success_criteria": ["如果异常则完成通知，否则不操作"],
                                "depends_on": ["health"],
                            },
                        ],
                        "success_criteria": ["正确回答健康状态并仅在必要时通知"],
                    },
                    "messages": [],
                }

        runtime = SimpleNamespace(
            run_supervisor_agent=SupervisorAgent(),
            settings=SimpleNamespace(llm_timeout_seconds=2),
        )
        decider = _supervisor_decider(
            runtime,
            {
                "history": [],
                "user_id": "JH-1",
                "role": "operator",
                "source": {},
                "deployment": {},
                "runtime": {},
            },
            store,
        )
        return await decider({
            "run": {"run_id": "run-supervisor-guard"},
            "message": "查看张爷爷健康状态，如异常通知护理员",
            "findings": [],
            "supervisor_iteration": 1,
        })

    decision = asyncio.run(scenario())

    assert decision.action == "create_tasks"
    assert [task.agent_id for task in decision.tasks] == ["health_agent"]


def test_supervisor_normalizes_room_answer_as_follow_up_to_prior_goal() -> None:
    request = _normalize_supervisor_user_request(
        "A-101 啊！",
        {
            "history": [
                {
                    "role": "user",
                    "content": "把张爷爷房间温度调到 24 度，并确认是否生效",
                },
                {
                    "role": "assistant",
                    "content": "请问张爷爷住在哪个房间？是 A-101 还是 A-102？",
                },
            ],
        },
    )
    assert request == (
        "把张爷爷房间温度调到 24 度，并确认是否生效\n"
        "补充信息：目标房间是 A-101。"
    )


def test_supervisor_keeps_plain_room_answer_unchanged_without_room_clarification() -> None:
    request = _normalize_supervisor_user_request(
        "A-101",
        {
            "history": [
                {"role": "user", "content": "今天状态怎么样"},
                {"role": "assistant", "content": "今天生命体征整体稳定。"},
            ],
        },
    )
    assert request == "A-101"


def test_care_executor_skips_conditional_noop_when_health_dependency_is_normal() -> None:
    async def scenario():
        class LegacyCareAgent:
            calls = 0

            async def run(self, **_kwargs):
                self.calls += 1
                raise AssertionError("conditional noop should skip legacy care execution")

        runtime = SimpleNamespace(
            eldercare_chat_agent=LegacyCareAgent(),
            settings=SimpleNamespace(total_timeout_seconds=2),
        )
        task = AgentTask(
            task_id="task-care-skip",
            run_id="run-care-skip",
            local_id="care",
            agent_id="care_agent",
            objective="如果健康异常则通知护理员，否则不操作",
            reason="仅在健康异常时才需要通知",
            success_criteria=["如果异常则完成通知，否则保持空操作"],
            dependency_findings=[
                DependencyFinding(
                    task_id="task-health",
                    agent_id="health_agent",
                    objective="查询张爷爷健康状态",
                    status="completed",
                    finding="未发现需要升级处置的明确异常。",
                    recommended_actions=[],
                )
            ],
        )
        result = await _care_executor(
            runtime,
            {"history": [], "role": "operator", "thinking": False},
        )(task)
        return result, runtime.eldercare_chat_agent.calls

    result, calls = asyncio.run(scenario())

    assert result.status == "completed"
    assert "安全跳过" in result.finding
    assert calls == 0


def test_health_executor_downgrades_completed_answer_when_critical_data_is_stale() -> None:
    async def scenario():
        class Gateway:
            @contextmanager
            def authorized_scope(self, _scope):
                yield

        class HealthAgent:
            async def ainvoke(self, _value):
                return {
                    "structured_response": AgentFindingPayload(
                        status="completed",
                        finding="最近一次生命体征未见明确异常。",
                        confidence=0.92,
                        tool_calls=[
                            ToolExecutionResult(
                                success=True,
                                verified=True,
                                tool_name="check_data_quality",
                                arguments={"resident_id": "elderly-001"},
                                result={
                                    "sensors": {
                                        "heart_rate": {"fresh": False},
                                        "spo2": {"fresh": False},
                                        "fall_detected": {"fresh": False},
                                    }
                                },
                            )
                        ],
                    ).model_dump(mode="json"),
                    "messages": [],
                }

        runtime = SimpleNamespace(
            health_gateway=Gateway(),
            health_react_agent=HealthAgent(),
            health_finding_repair_runnable=None,
            settings=SimpleNamespace(
                total_timeout_seconds=2,
                llm_timeout_seconds=2,
            ),
        )
        task = AgentTask(
            task_id="task-health-stale",
            run_id="run-health-stale",
            local_id="health",
            agent_id="health_agent",
            objective="查看张爷爷当前健康状态",
            source_request="查看张爷爷今天健康状态",
            reason="用户请求",
            success_criteria=["给出健康状态结论"],
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
        return await _health_executor(runtime)(task)

    result = asyncio.run(scenario())

    assert result.status == "needs_more_information"
    assert "关键健康数据已过期" in result.finding
    assert result.confidence <= 0.45


def test_run_coordinator_filters_first_round_conditional_care_and_finishes_next_round() -> None:
    async def scenario():
        class SupervisorAgent:
            def __init__(self) -> None:
                self.calls = 0

            async def ainvoke(self, _value):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "structured_response": {
                            "action": "create_tasks",
                            "goal": "查看张爷爷健康状态，如异常通知护理员",
                            "reason": "需要先确认健康状态，再决定是否通知",
                            "tasks": [
                                {
                                    "local_id": "health",
                                    "agent_id": "health_agent",
                                    "objective": "查询张爷爷健康状态",
                                    "reason": "先核实是否存在异常",
                                    "success_criteria": ["获得健康状态结论"],
                                    "depends_on": [],
                                },
                                {
                                    "local_id": "care",
                                    "agent_id": "care_agent",
                                    "objective": "如果异常则通知护理员，否则不操作",
                                    "reason": "只有异常时才需要通知",
                                    "success_criteria": ["如果异常则通知，否则不操作"],
                                    "depends_on": ["health"],
                                },
                            ],
                            "success_criteria": ["给出健康判断，并仅在必要时通知"],
                        },
                        "messages": [],
                    }
                return {
                    "structured_response": {
                        "action": "finish",
                        "goal": "查看张爷爷健康状态，如异常通知护理员",
                        "final_response": "最近一次可用健康数据未见明确异常，无需通知护理员。",
                        "reason": "健康结果未触发通知条件",
                    },
                    "messages": [],
                }

        store = InMemoryRunStore()
        await store.create_run(RunRecord(
            run_id="run-conditional-care",
            conversation_id="conv-1",
            turn_id="turn-1",
            root_goal="查看张爷爷健康状态，如异常通知护理员",
        ))
        supervisor_runtime = SimpleNamespace(
            run_supervisor_agent=SupervisorAgent(),
            settings=SimpleNamespace(llm_timeout_seconds=2),
        )
        care_calls = 0

        async def execute_health(_task):
            return AgentFindingPayload(
                status="completed",
                finding="最近一次生命体征未见明确异常。",
                confidence=0.8,
            )

        async def execute_care(_task):
            nonlocal care_calls
            care_calls += 1
            raise AssertionError("conditional care task should be filtered before execution")

        coordinator = RunCoordinator(
            store,
            TaskScheduler(
                available_agents={"health_agent", "care_agent"},
                task_id_factory=lambda: "task-health",
            ),
            _supervisor_decider(
                supervisor_runtime,
                {
                    "history": [],
                    "user_id": "JH-1",
                    "role": "operator",
                    "source": {},
                    "deployment": {},
                    "runtime": {},
                },
                store,
            ),
            {
                "health_agent": execute_health,
                "care_agent": execute_care,
            },
            max_supervisor_iterations=2,
            run_id_factory=lambda: "run-conditional-care",
        )
        run = await coordinator.execute(
            conversation_id="conv-1",
            turn_id="turn-1",
            message="查看张爷爷健康状态，如异常通知护理员",
            run_id="run-conditional-care",
        )
        return (
            run,
            care_calls,
            await store.tasks_for_run(run.run_id),
            await store.events(run.run_id),
        )

    run, care_calls, tasks, events = asyncio.run(scenario())

    assert run.status == "completed"
    assert run.final_response == "最近一次可用健康数据未见明确异常，无需通知护理员。"
    assert care_calls == 0
    assert len(tasks) == 1
    assert tasks[0].agent_id == "health_agent"
    assert all(task.agent_id != "care_agent" for task in tasks)
    assert [event.type for event in events].count("task.created") == 1
    assert [event.type for event in events].count("supervisor.decision.completed") == 2
