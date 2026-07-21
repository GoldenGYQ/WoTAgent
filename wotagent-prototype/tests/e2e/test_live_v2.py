"""Opt-in live DeepSeek + MCP + simulator acceptance test."""

from __future__ import annotations

import asyncio
import os

import pytest
from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from wotagent.agent_graph import (
    AgentGraphDependencies,
    LangChainResponseComposer,
    LangChainTaskPlanner,
    build_agent_graph,
)
from wotagent.core import create_llm_runtime
from wotagent.gateway import MCPDeviceGateway
from wotagent.orchestration import TaskOrchestrator
from wotagent.policy import PolicyEngine
from wotagent.telemetry import InMemoryTraceStore

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1",
    reason="set RUN_LIVE_LLM_TESTS=1 with the simulator running",
)


def test_chinese_two_step_control_reaches_verified_device_state() -> None:
    async def run():
        load_dotenv(".env")
        task_id = "task_live_acceptance"
        session_id = "sess_live_acceptance"
        store = InMemoryTraceStore()
        await store.create(task_id=task_id, session_id=session_id, request_id="req_live_acceptance")
        pipeline = create_llm_runtime(enable_thinking=False, enable_streaming=False, temperature=0)
        policy = PolicyEngine()
        gateway = MCPDeviceGateway()
        graph = build_agent_graph(
            AgentGraphDependencies(
                planner=LangChainTaskPlanner(pipeline.planner, trace_store=store),
                policy=policy,
                orchestrator=TaskOrchestrator(gateway, policy),
                responder=LangChainResponseComposer(pipeline.response_model, trace_store=store),
                trace_store=store,
            ),
            checkpointer=InMemorySaver(),
        )
        result = await graph.ainvoke(
            {
                "task_id": task_id,
                "request_id": "req_live_acceptance",
                "session_id": session_id,
                "user_id": "acceptance-test",
                "role": "operator",
                "message": "把客厅灯打开并把亮度调到60%",
            },
            config={"configurable": {"thread_id": session_id}},
        )
        state = await gateway.query_state("light-001")
        usage = (await store.get(task_id)).usage
        return result, state, usage

    result, state, usage = asyncio.run(run())
    assert result["plan"].intent == "control"
    assert len(result["plan"].steps) == 2
    assert result["trace"].status.value == "succeeded"
    assert all(item.verification and item.verification.passed for item in result["trace"].steps)
    assert state["on"] is True
    assert state["brightness"] == 60
    assert result["response"]
    assert usage.model_calls == 2
    assert usage.tool_calls == 2
