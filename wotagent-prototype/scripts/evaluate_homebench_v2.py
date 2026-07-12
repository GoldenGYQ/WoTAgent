"""Run the adapted HomeBench subset against the real structured Planner."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path

from dotenv import load_dotenv

from wotagent.agent_graph import LangChainTaskPlanner, capability_context
from wotagent.core import create_wot_pipeline
from wotagent.evaluation import HomeBenchAdapter, score_plan
from wotagent.policy import PolicyEngine
from wotagent.telemetry import InMemoryTraceStore


async def run(args) -> dict:
    load_dotenv(".env")
    cases = HomeBenchAdapter().load(args.dataset)
    selected = cases[args.offset:args.offset + args.limit]
    pipeline = create_wot_pipeline(enable_thinking=False, enable_streaming=False, temperature=0)
    store = InMemoryTraceStore()
    planner = LangChainTaskPlanner(pipeline.planner, trace_store=store)
    policy = PolicyEngine()
    rows = []

    for index, case in enumerate(selected):
        task_id = f"eval_{index:04d}_{case.case_id}"
        await store.create(task_id=task_id, session_id=task_id, request_id=task_id)
        try:
            state = {"task_id": task_id, "message": case.instruction}
            if args.context_mode == "all":
                state["context"] = {"devices": capability_context()}
            plan = await planner(state)
            score = score_plan(case, plan)
            executable = all(policy.evaluate("operator", step).effect == "allow" for step in plan.steps)
            error = None
        except Exception as exc:
            plan = None
            score = {"intent_correct": False, "exact_match": False, "precision": 0, "recall": 0, "f1": 0}
            executable = False
            error = str(exc)
        record = await store.get(task_id)
        events = await store.events(task_id)
        model_event = next((event for event in events if event.type == "model.request.completed"), None)
        rows.append({
            "case": case.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json") if plan else None,
            "score": score,
            "executable": executable,
            "error": error,
            "usage": record.usage.model_dump(mode="json"),
            "time_to_plan_ms": model_event.duration_ms if model_event else None,
            "planner_ttft_ms": None,
            "planner_ttft_reason": "forced function call currently uses non-streaming completion",
        })

    def mean(key):
        values = [float(row["score"][key]) for row in rows]
        return statistics.mean(values) if values else 0.0

    report = {
        "benchmark": "HomeBench-adapted Planner-only",
        "context_mode": args.context_mode,
        "available_aligned_cases": len(cases),
        "evaluated_cases": len(rows),
        "metrics": {
            "intent_accuracy": mean("intent_correct"),
            "exact_match": mean("exact_match"),
            "precision": mean("precision"),
            "recall": mean("recall"),
            "f1": mean("f1"),
            "executable_plan_rate": statistics.mean(row["executable"] for row in rows) if rows else 0.0,
            "total_input_tokens": sum(row["usage"]["input_tokens"] for row in rows),
            "total_output_tokens": sum(row["usage"]["output_tokens"] for row in rows),
            "tokens_per_case": statistics.mean(row["usage"]["total_tokens"] for row in rows) if rows else 0.0,
            "mean_time_to_plan_ms": statistics.mean(row["time_to_plan_ms"] for row in rows if row["time_to_plan_ms"] is not None) if rows else 0.0,
        },
        "rows": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--output", default="data/evaluation/homebench_v2.json")
    parser.add_argument("--context-mode", choices=("relevant", "all"), default="relevant")
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
