from __future__ import annotations

import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException

from wotagent.application import RunService
from wotagent.agents import DEFAULT_AGENT_REGISTRY
from wotagent.conversation import ConversationStore
from wotagent.domain import DeploymentProfileRegistry
from wotagent.gateway import MCPDeviceGateway, deployment_device_scope
from wotagent.orchestration import InMemoryRunStore


class FakeMCPClient:
    async def refresh_td_catalog(self) -> int:
        return 2

    async def td_capabilities(self):
        return [
            {
                "device_id": "light-001",
                "title": "Living room light",
                "location": "living_room",
                "actions": [],
                "properties": ["state"],
                "read_scopes": ["wot.device_ops"],
            },
            {
                "device_id": "wristband-001",
                "title": "Resident wristband",
                "location": "A-101",
                "actions": [],
                "properties": ["state"],
                "read_scopes": ["wot.device_ops"],
            },
        ]


def test_profiles_define_disjoint_device_worlds() -> None:
    registry = DeploymentProfileRegistry.load()
    eldercare = registry.require("eldercare")
    villa = registry.require("villa")

    assert eldercare.domain_id == "eldercare"
    assert villa.domain_id == "smart_home"
    villa_agents = DEFAULT_AGENT_REGISTRY.select(villa.enabled_agents)
    assert "security_agent" in villa_agents
    assert "health_agent" not in villa_agents
    assert set(eldercare.device_ids).isdisjoint(villa.device_ids)
    assert eldercare.spaces == ["A-101", "A-102"]
    assert "living_room" in villa.spaces


def test_gateway_filters_and_rejects_cross_profile_devices() -> None:
    async def scenario() -> None:
        gateway = MCPDeviceGateway(
            client=FakeMCPClient(),
            allowed_scopes={"wot.device_ops"},
        )
        with deployment_device_scope({"light-001"}):
            capabilities = await gateway.capabilities()
            assert [item["device_id"] for item in capabilities] == [
                "light-001"
            ]
            with pytest.raises(
                PermissionError,
                match="outside active deployment profile",
            ):
                await gateway.query_state("wristband-001")

    asyncio.run(scenario())


def test_run_persists_profile_and_rejects_cross_profile_conversation(
    tmp_path,
) -> None:
    async def scenario() -> None:
        conversations = ConversationStore(tmp_path / "conversations.db")
        runs = InMemoryRunStore()
        service = RunService(
            conversations,
            runs,
            lambda _context: None,  # type: ignore[arg-type]
        )
        background = BackgroundTasks()
        first = await service.create(
            message="有几个房间",
            conversation_id="conv-profile",
            role="operator",
            user_id="JH-1",
            thinking=False,
            profile_id="villa",
            background_tasks=background,
        )
        assert first.profile_id == "villa"
        assert first.domain_id == "smart_home"
        assert first.site_id == "villa-demo"

        with pytest.raises(HTTPException) as error:
            await service.create(
                message="查看张爷爷健康状态",
                conversation_id="conv-profile",
                role="operator",
                user_id="JH-1",
                thinking=False,
                profile_id="eldercare",
                background_tasks=BackgroundTasks(),
            )
        assert error.value.status_code == 409

    asyncio.run(scenario())
