"""MCP Resources for health context and Tools for eldercare actions."""

from __future__ import annotations

import argparse
import json

from fastmcp import FastMCP

from .actions import EldercareActionService
from .store import EldercareStore


def build_eldercare_mcp(store: EldercareStore | None = None) -> FastMCP:
    eldercare_store = store or EldercareStore()
    eldercare_store.seed_demo()
    actions = EldercareActionService(eldercare_store)
    mcp = FastMCP("eldercare-health")

    @mcp.resource("elderly://{resident_id}/vitals/latest", mime_type="application/json")
    def latest_vitals(resident_id: str) -> str:
        latest = eldercare_store.latest(resident_id)
        vitals = {
            key: value for key, value in latest.items()
            if key in {"heart_rate", "spo2", "fall_detected"}
        }
        return json.dumps(vitals, ensure_ascii=False)

    @mcp.resource("elderly://{resident_id}/sleep/today", mime_type="application/json")
    def sleep_today(resident_id: str) -> str:
        return json.dumps(
            eldercare_store.latest(resident_id).get("sleep_quality", {}),
            ensure_ascii=False,
        )

    @mcp.resource("elderly://{resident_id}/health/summary", mime_type="application/json")
    def health_summary(resident_id: str) -> str:
        return eldercare_store.summary(resident_id).model_dump_json()

    @mcp.tool(tags={"eldercare-environment"})
    async def adjust_temperature(
        resident_id: str,
        target_temperature: float,
        reason: str,
    ) -> dict:
        """Adjust a resident room to a target temperature."""
        return await actions.adjust_temperature(resident_id, target_temperature, reason)

    @mcp.tool(tags={"eldercare-care"})
    async def notify_caregiver(resident_id: str, message: str, urgency: str) -> dict:
        """Notify the caregiver assigned to a resident."""
        return await actions.notify_caregiver(resident_id, message, urgency)

    @mcp.tool(tags={"eldercare-care"})
    async def create_care_task(resident_id: str, title: str, priority: str) -> dict:
        """Create a tracked care task for a resident."""
        return await actions.create_care_task(resident_id, title, priority)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the eldercare MCP service")
    parser.add_argument("--transport", choices=("stdio", "http"), default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18082)
    args = parser.parse_args()
    server = build_eldercare_mcp()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
