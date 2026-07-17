from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langgraph.types import Command

from wotagent.orchestration import AgentTask, PendingConfirmation
from wotagent.runtime.run_runtime import _device_executor


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
        finding = await pending.resume(True)
        return pending, finding, graph.calls

    pending, finding, calls = asyncio.run(scenario())

    assert pending.payload["summary"] == "打开客厅灯"
    assert finding.status == "completed"
    assert isinstance(calls[1][0], Command)
    assert calls[0][1]["configurable"]["thread_id"] == "task-confirm"
    assert calls[1][1]["configurable"]["thread_id"] == "task-confirm"
