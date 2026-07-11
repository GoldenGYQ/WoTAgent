"""Stable device boundary used by the deterministic orchestrator."""

from __future__ import annotations

from typing import Any, Protocol


class DeviceGateway(Protocol):
    name: str

    async def list_devices(self, location: str | None = None) -> list[dict[str, Any]]: ...

    async def query_state(self, device_id: str) -> dict[str, Any]: ...

    async def control_device(
        self,
        device_id: str,
        action: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]: ...
