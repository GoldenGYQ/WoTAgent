from __future__ import annotations

import asyncio

from wotagent.agents import build_care_tools
from wotagent.domains.eldercare import EldercareStore, ScopedCareMCPClient
from wotagent.orchestration import ResourceScope


def test_care_gateway_rejects_resident_outside_effective_scope(tmp_path) -> None:
    async def scenario():
        store = EldercareStore(tmp_path / "eldercare.db")
        store.seed_demo()
        gateway = ScopedCareMCPClient(store, require_scope=True)
        with gateway.authorized_scope(ResourceScope(
            resource_ids=["resident:elderly-001"],
            capabilities=["care.coordinate"],
        )):
            return await gateway.call("notify_caregiver", {
                "resident_id": "elderly-002",
                "message": "Please review the alert.",
                "urgency": "high",
            })

    result = asyncio.run(scenario())

    assert result.success is False
    assert result.error_code == "RESOURCE_SCOPE_DENIED"


def test_care_tool_adapter_preserves_gateway_attestation() -> None:
    class Gateway:
        calls = []

        async def call(self, tool_name, arguments):
            from wotagent.agents import ToolExecutionResult

            self.calls.append((tool_name, arguments))
            return ToolExecutionResult(
                success=True,
                verified=True,
                tool_name=tool_name,
                arguments=arguments,
                result={"delivery": "simulated"},
                observed_state={"delivery": "simulated"},
            )

    gateway = Gateway()
    tools = {tool.name: tool for tool in build_care_tools(gateway)}
    result = asyncio.run(tools["notify_caregiver"].ainvoke({
        "resident_id": "elderly-001",
        "message": "Please review the alert.",
        "urgency": "high",
    }))

    assert gateway.calls[0][0] == "notify_caregiver"
    assert result["success"] is True
    assert result["verified"] is True
