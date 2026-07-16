"""Operational actions shared by eldercare subagents and MCP tools."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .store import EldercareStore


class EldercareActionService:
    def __init__(self, store: EldercareStore, environment_gateway: Any | None = None) -> None:
        self.store = store
        self.environment_gateway = environment_gateway

    async def adjust_temperature(
        self,
        resident_id: str,
        target_temperature: float,
        reason: str,
    ) -> dict[str, Any]:
        resident = self._resident(resident_id)
        if self.environment_gateway is not None and resident.environment_device_id:
            device_id = resident.environment_device_id
            raw = await self.environment_gateway.control_device(
                device_id,
                "setTemperature",
                {"temperature": target_temperature},
                idempotency_key=f"eldercare:{resident_id}:{uuid4().hex[:10]}",
            )
            state = await self.environment_gateway.query_state(device_id)
            actual = state.get("targetTemp")
            verified = isinstance(actual, (int, float)) and abs(float(actual) - target_temperature) < 0.1
            result = {
                "success": bool(raw.get("success", raw.get("accepted", True))) and verified,
                "execution_mode": "wot_mcp",
                "device_id": device_id,
                "room": resident.room,
                "target_temperature": target_temperature,
                "verified_state": state,
                "reason": reason,
            }
        else:
            result = {
                "success": True,
                "execution_mode": "simulated",
                "room": resident.room,
                "target_temperature": target_temperature,
                "reason": reason,
            }
        return self.store.record_action(
            resident_id=resident_id,
            agent_id="environment_agent",
            action="adjust_temperature",
            arguments={"target_temperature": target_temperature, "reason": reason},
            result=result,
        )

    async def notify_caregiver(
        self,
        resident_id: str,
        message: str,
        urgency: str,
    ) -> dict[str, Any]:
        resident = self._resident(resident_id)
        result = {
            "success": True,
            "recipient": resident.caregiver,
            "message": message,
            "urgency": urgency,
            "delivery": "simulated",
        }
        return self.store.record_action(
            resident_id=resident_id,
            agent_id="care_agent",
            action="notify_caregiver",
            arguments={"message": message, "urgency": urgency},
            result=result,
        )

    async def create_care_task(
        self,
        resident_id: str,
        title: str,
        priority: str,
    ) -> dict[str, Any]:
        self._resident(resident_id)
        result = {
            "success": True,
            "title": title,
            "priority": priority,
            "status": "open",
        }
        return self.store.record_action(
            resident_id=resident_id,
            agent_id="care_agent",
            action="create_care_task",
            arguments={"title": title, "priority": priority},
            result=result,
        )

    def _resident(self, resident_id: str):
        resident = self.store.get_resident(resident_id)
        if resident is None:
            raise KeyError(f"resident '{resident_id}' not found")
        return resident
