"""Inspect one SimuHome episode's dynamic TD/MCP conversion without an LLM call."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from wotagent.evaluation import SimuHomeMCPHarness
from wotagent.evaluation.simuhome import evaluate_qt3_runtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18081/api")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--device-id")
    parser.add_argument("--action")
    parser.add_argument("--arguments", default="{}", help="JSON object passed to the MCP action tool")
    args = parser.parse_args()
    if bool(args.device_id) != bool(args.action):
        parser.error("--device-id and --action must be provided together")
    try:
        args.arguments = json.loads(args.arguments)
    except json.JSONDecodeError as exc:
        parser.error(f"--arguments must be valid JSON: {exc}")
    if not isinstance(args.arguments, dict):
        parser.error("--arguments must decode to a JSON object")
    return args


async def inspect(args: argparse.Namespace) -> dict[str, Any]:
    episode = json.loads(args.episode.read_text(encoding="utf-8"))
    harness = await SimuHomeMCPHarness.create(
        episode,
        str(episode.get("query", "")),
        base_url=args.base_url,
    )
    try:
        capabilities = await harness.gateway.capabilities()
        tools = [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("input_schema", {}),
                "wot": (tool.get("meta") or {}).get("wot", {}),
            }
            for tool in await harness.client.list_tools()
            if (tool.get("meta") or {}).get("wot")
        ]
        manual_execution = None
        if args.device_id and args.action:
            result = await harness.gateway.control_device(
                args.device_id,
                args.action,
                args.arguments,
                idempotency_key="manual-simuhome-inspection",
            )
            observed = await harness.gateway.query_state(args.device_id)
            manual_execution = {
                "device_id": args.device_id,
                "action": args.action,
                "arguments": args.arguments,
                "result": result,
                "observed_state": observed,
            }

        final_state = await harness.binding.home_state()
        evaluation = evaluate_qt3_runtime(
            episode,
            final_state,
            harness.binding.tool_invocations,
        )
        report = {
            "episode": str(args.episode.resolve()),
            "seed": (episode.get("meta") or {}).get("seed"),
            "query": episode.get("query", ""),
            "llm_called": False,
            "token_cost": 0,
            "dynamic_td_count": len(harness.tds),
            "mcp_tool_count": len(tools),
            "tds": [td.raw for td in harness.tds],
            "capabilities_from_mcp": capabilities,
            "mcp_tools": tools,
            "binding_invocations": list(harness.binding.tool_invocations),
            "manual_execution": manual_execution,
            "evaluation": evaluation,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(json.dumps({
            "seed": report["seed"],
            "query": report["query"],
            "llm_called": False,
            "token_cost": 0,
            "dynamic_td_count": report["dynamic_td_count"],
            "mcp_tool_count": report["mcp_tool_count"],
            "manual_execution": manual_execution,
            "evaluation": evaluation,
            "output": str(args.output.resolve()) if args.output else None,
        }, ensure_ascii=False, indent=2))
        return report
    finally:
        await harness.close()


if __name__ == "__main__":
    asyncio.run(inspect(parse_args()))
