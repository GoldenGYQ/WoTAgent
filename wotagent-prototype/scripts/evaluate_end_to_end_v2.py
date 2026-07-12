"""Small real-model, real-MCP benchmark with user-visible latency metrics."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from wotagent.agent_graph import (
    AgentGraphDependencies,
    LangChainResponseComposer,
    LangChainTaskPlanner,
    build_agent_graph,
)
from wotagent.core import create_wot_pipeline
from wotagent.gateway import MCPDeviceGateway
from wotagent.orchestration import TaskOrchestrator
from wotagent.policy import PolicyEngine
from wotagent.telemetry import InMemoryTraceStore


CASES = [
    {
        "id": "light_brightness",
        "instruction": "把客厅灯打开并把亮度调到60%",
        "device_id": "light-001",
        "goal": {"on": True, "brightness": 60},
    },
    {
        "id": "light_off",
        "instruction": "关闭客厅灯",
        "device_id": "light-001",
        "goal": {"on": False},
    },
    {
        "id": "ac_temperature",
        "instruction": "把客厅空调打开并设置为25度",
        "device_id": "ac-001",
        "goal": {"on": True, "targetTemp": 25},
    },
    {
        "id": "fan_speed",
        "instruction": "打开书房风扇并把速度设置为2",
        "device_id": "fan-001",
        "goal": {"on": True, "speed": 2},
    },
    {
        "id": "state_query",
        "instruction": "查询客厅灯现在的状态",
        "device_id": "light-001",
        "goal": None,
    },
]


async def main() -> None:
    load_dotenv(".env")
    pipeline = create_wot_pipeline(enable_thinking=False, enable_streaming=True, temperature=0)
    gateway = MCPDeviceGateway()
    rows = []

    for index, case in enumerate(CASES):
        task_id = f"e2e_eval_{index}_{case['id']}"
        store = InMemoryTraceStore()
        await store.create(task_id=task_id, session_id=task_id, request_id=task_id)
        policy = PolicyEngine()
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
        started = time.perf_counter()
        result = await graph.ainvoke(
            {
                "task_id": task_id,
                "request_id": task_id,
                "session_id": task_id,
                "user_id": "benchmark",
                "role": "operator",
                "message": case["instruction"],
            },
            config={"configurable": {"thread_id": task_id}},
        )
        total_ms = (time.perf_counter() - started) * 1000
        observed = await gateway.query_state(case["device_id"])
        goal = case["goal"]
        goal_passed = True if goal is None else all(observed.get(key) == value for key, value in goal.items())
        events = await store.events(task_id)
        model_events = [event for event in events if event.type == "model.request.completed"]
        planner_event = next((event for event in model_events if event.node == "planner"), None)
        responder_event = next((event for event in model_events if event.node == "responder"), None)
        usage = (await store.get(task_id)).usage
        rows.append({
            "case": case,
            "status": result.get("trace").status.value if result.get("trace") else result.get("phase"),
            "goal_passed": goal_passed,
            "observed_state": observed,
            "response": result.get("response"),
            "total_duration_ms": total_ms,
            "time_to_plan_ms": planner_event.duration_ms if planner_event else None,
            "response_latency_ms": responder_event.duration_ms if responder_event else None,
            "response_ttft_ms": responder_event.data.get("ttft_ms") if responder_event else None,
            "usage": usage.model_dump(mode="json"),
            "loop_count": usage.tool_calls and (await store.get(task_id)).loop_count,
            "event_count": len(events),
        })

    def values(key):
        return [row[key] for row in rows if row[key] is not None]

    report = {
        "benchmark": "WoTAgent v2 real DeepSeek + MCP end-to-end",
        "evaluated_cases": len(rows),
        "metrics": {
            "goal_state_success_rate": statistics.mean(row["goal_passed"] for row in rows),
            "tokens_per_turn": statistics.mean(row["usage"]["total_tokens"] for row in rows),
            "input_tokens_per_turn": statistics.mean(row["usage"]["input_tokens"] for row in rows),
            "output_tokens_per_turn": statistics.mean(row["usage"]["output_tokens"] for row in rows),
            "mean_total_duration_ms": statistics.mean(values("total_duration_ms")),
            "mean_time_to_plan_ms": statistics.mean(values("time_to_plan_ms")),
            "mean_response_latency_ms": statistics.mean(values("response_latency_ms")),
            "mean_response_ttft_ms": statistics.mean(values("response_ttft_ms")),
            "mean_tool_calls": statistics.mean(row["usage"]["tool_calls"] for row in rows),
            "mean_model_calls": statistics.mean(row["usage"]["model_calls"] for row in rows),
        },
        "rows": rows,
    }
    output = Path("data/evaluation/end_to_end_v2_5.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
