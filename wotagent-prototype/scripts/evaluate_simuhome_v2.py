"""Run WoTAgent 2.0 on the official SimuHome qt3 feasible episodes."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", encoding="utf-8")

from wotagent.agent_graph import (
    AgentGraphDependencies,
    LangChainResponseComposer,
    LangChainTaskPlanner,
    build_agent_graph,
)
from wotagent.core import create_wot_pipeline
from wotagent.evaluation import SimuHomeMCPHarness
from wotagent.evaluation.simuhome import evaluate_qt3_runtime
from wotagent.orchestration import TaskOrchestrator
from wotagent.policy import PolicyEngine
from wotagent.telemetry import InMemoryTraceStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:18081/api")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--seeds", default="")
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--role", default="admin")
    return parser.parse_args()


def _selected_episodes(root: Path, seeds: str, limit: int) -> list[Path]:
    requested = {int(item.strip()) for item in seeds.split(",") if item.strip()}
    paths = sorted(
        root.glob("qt3_feasible_seed_*.json"),
        key=lambda path: int(path.stem.rsplit("_", 1)[1]),
    )
    if requested:
        paths = [path for path in paths if int(path.stem.rsplit("_", 1)[1]) in requested]
    return paths[:limit] if limit > 0 else paths


async def run(args: argparse.Namespace) -> dict[str, Any]:
    paths = _selected_episodes(args.benchmark_root, args.seeds, args.limit)
    if not paths:
        raise FileNotFoundError("no qt3 feasible episodes matched the selection")
    run_id = datetime.now(timezone.utc).strftime("simuhome_qt3_%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or Path("data/evaluation/runs") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved = {
        "run_id": run_id,
        "benchmark_root": str(args.benchmark_root.resolve()),
        "base_url": args.base_url,
        "episodes": [path.name for path in paths],
        "thinking": args.thinking,
        "role": args.role,
        "model": os.getenv("LLM_MODEL", "deepseek-chat"),
        "agent_gateway": "mcp",
        "capability_source": "dynamic-td",
        "runtime_binding": "simuhome-rest-matter",
    }
    (output_dir / "config.resolved.json").write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    pipeline = create_wot_pipeline(
        enable_thinking=args.thinking,
        enable_streaming=False,
    )
    store = InMemoryTraceStore()

    rows: list[dict[str, Any]] = []
    predictions_path = output_dir / "episode_results.jsonl"
    traces_path = output_dir / "trace_events.jsonl"
    tds_path = output_dir / "dynamic_tds.jsonl"
    predictions_path.write_text("", encoding="utf-8")
    traces_path.write_text("", encoding="utf-8")
    tds_path.write_text("", encoding="utf-8")
    for path in paths:
        episode = json.loads(path.read_text(encoding="utf-8"))
        harness = await SimuHomeMCPHarness.create(
            episode,
            episode["query"],
            base_url=args.base_url,
        )
        policy = PolicyEngine()
        graph = build_agent_graph(
            AgentGraphDependencies(
                planner=LangChainTaskPlanner(
                    pipeline.planner,
                    trace_store=store,
                    capability_provider=harness.gateway.capabilities,
                    thinking_runnable=pipeline.thinking_planner,
                ),
                policy=policy,
                orchestrator=TaskOrchestrator(harness.gateway, policy),
                responder=LangChainResponseComposer(
                    pipeline.response_model,
                    trace_store=store,
                    thinking_runnable=pipeline.thinking_response_model,
                ),
                trace_store=store,
            ),
            checkpointer=InMemorySaver(),
        )
        try:
            with tds_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "episode": path.name,
                    "seed": episode["meta"]["seed"],
                    "tds": [td.raw for td in harness.tds],
                }, ensure_ascii=False) + "\n")
            task_id = f"simuhome_{episode['meta']['seed']}_{uuid.uuid4().hex[:8]}"
            await store.create(task_id=task_id, session_id=run_id, request_id=task_id)
            started = time.perf_counter()
            error = ""
            result: dict[str, Any] = {}
            try:
                result = await graph.ainvoke({
                    "request_id": task_id,
                    "task_id": task_id,
                    "session_id": run_id,
                    "user_id": "simuhome-benchmark",
                    "role": args.role,
                    "message": episode["query"],
                    "thinking": args.thinking,
                    "conversation_history": [],
                    "context": {"runtime": {"current_role": args.role, "pending_goal": None}},
                }, config={"configurable": {"thread_id": task_id}})
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            duration_ms = (time.perf_counter() - started) * 1000
            await asyncio.sleep(0.2)
            final_state = await harness.binding.home_state()
            evaluation = evaluate_qt3_runtime(
                episode, final_state, harness.binding.tool_invocations
            )
            record = await store.get(task_id)
            events = await store.events(task_id)
            trace = result.get("trace")
            plan = result.get("plan")
            row = {
                "episode": path.name,
                "seed": episode["meta"]["seed"],
                "query": episode["query"],
                "score": evaluation["score"],
                "goal_success": evaluation["goal_success"],
                "required_action_success": evaluation["required_action_success"],
                "assertions_passed": evaluation["assertions_passed"],
                "assertions_total": evaluation["assertions_total"],
                "agent_status": trace.status.value if trace is not None else result.get("phase", "failed"),
                "plan": plan.model_dump(mode="json") if plan is not None else None,
                "response": result.get("response", ""),
                "duration_ms": duration_ms,
                "loop_count": record.loop_count if record else 0,
                "recovery_count": record.recovery_count if record else 0,
                "usage": record.usage.model_dump(mode="json") if record else {},
                "evaluation": evaluation,
                "tools_invoked": list(harness.binding.tool_invocations),
                "dynamic_td_count": len(harness.tds),
                "error": error or result.get("error", ""),
            }
            rows.append(row)
            with predictions_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            with traces_path.open("a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps({
                        "episode": path.name,
                        "seed": episode["meta"]["seed"],
                        **event.model_dump(mode="json"),
                    }, ensure_ascii=False) + "\n")
        finally:
            await harness.close()

    summary = summarize(rows, resolved)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_summary_csv(output_dir / "summary.csv", rows)
    (output_dir / "analysis.md").write_text(render_analysis(summary, rows), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir.resolve()), **summary["metrics"]}, ensure_ascii=False))
    return summary


def summarize(rows: list[dict[str, Any]], resolved: dict[str, Any]) -> dict[str, Any]:
    count = len(rows)
    assertion_total = sum(row["assertions_total"] for row in rows)
    assertion_passed = sum(row["assertions_passed"] for row in rows)
    usage_keys = (
        "model_calls", "tool_calls", "input_tokens", "output_tokens", "reasoning_tokens",
        "total_tokens", "cache_hit_tokens", "cache_miss_tokens", "estimated_cost",
        "model_latency_ms", "ttft_ms_total", "ttft_measurements",
    )
    usage = {key: sum(float(row["usage"].get(key, 0)) for row in rows) for key in usage_keys}
    return {
        "config": resolved,
        "metrics": {
            "episodes": count,
            "episode_success_rate": sum(row["score"] for row in rows) / count,
            "goal_success_rate": sum(row["goal_success"] for row in rows) / count,
            "required_action_success_rate": sum(row["required_action_success"] for row in rows) / count,
            "assertion_accuracy": assertion_passed / assertion_total if assertion_total else 0.0,
            "agent_success_rate": sum(row["agent_status"] == "succeeded" for row in rows) / count,
            "mean_duration_ms": sum(row["duration_ms"] for row in rows) / count,
            "mean_tokens_per_episode": usage["total_tokens"] / count,
            "mean_cost_per_episode": usage["estimated_cost"] / count,
            "mean_ttft_ms": (
                usage["ttft_ms_total"] / usage["ttft_measurements"]
                if usage["ttft_measurements"] else None
            ),
            "total_usage": usage,
        },
    }


def write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "episode", "seed", "score", "goal_success", "required_action_success",
        "assertions_passed", "assertions_total", "agent_status", "duration_ms", "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fields} for row in rows)


def render_analysis(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    metrics = summary["metrics"]
    lines = [
        "# WoTAgent 2.0 SimuHome qt3 Evaluation",
        "",
        "This is a runtime-connected adapted evaluation over the official, unmodified qt3 feasible episodes.",
        "The score follows SimuHome qt3 semantics: required discovery actions and all target assertions must pass.",
        "",
        "## Metrics",
        "",
        f"- Episodes: {metrics['episodes']}",
        f"- Episode success rate: {metrics['episode_success_rate']:.2%}",
        f"- Goal success rate: {metrics['goal_success_rate']:.2%}",
        f"- Matter assertion accuracy: {metrics['assertion_accuracy']:.2%}",
        f"- Agent execution success rate: {metrics['agent_success_rate']:.2%}",
        f"- Mean latency: {metrics['mean_duration_ms']:.1f} ms",
        f"- Mean TTFT: {metrics['mean_ttft_ms']:.1f} ms" if metrics["mean_ttft_ms"] is not None else "- Mean TTFT: unavailable",
        f"- Mean tokens per episode: {metrics['mean_tokens_per_episode']:.1f}",
        f"- Total tokens: {metrics['total_usage']['total_tokens']:.0f}",
        f"- Estimated cost: {metrics['total_usage']['estimated_cost']:.6f}",
        "",
        "## Episodes",
        "",
        "| Episode | Score | Assertions | Agent status | Error |",
        "|---|---:|---:|---|---|",
    ]
    for row in rows:
        error = str(row["error"]).replace("|", "\\|")
        lines.append(
            f"| {row['episode']} | {row['score']} | {row['assertions_passed']}/{row['assertions_total']} "
            f"| {row['agent_status']} | {error} |"
        )
    lines.extend(["", "## Scope", "", "Only qt3 feasible immediate-control episodes are included. Temporal and environment-aware qt1/qt2/qt4 remain future work."])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
