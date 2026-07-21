"""Care specialist assembly over scoped MCP tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..domains.eldercare.care_gateway import ScopedCareMCPClient
from .factory import build_specialist_agent
from .specs import get_agent_spec


class CareNotificationInput(BaseModel):
    resident_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    urgency: str = Field(min_length=1)


class CareTaskInput(BaseModel):
    resident_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    priority: str = Field(min_length=1)


def build_care_tools(gateway: ScopedCareMCPClient) -> list[BaseTool]:
    async def notify_caregiver(
        resident_id: str,
        message: str,
        urgency: str,
    ) -> dict[str, Any]:
        result = await gateway.call(
            "notify_caregiver",
            {"resident_id": resident_id, "message": message, "urgency": urgency},
        )
        return result.model_dump(mode="json")

    async def notify_duty_caregiver(
        resident_id: str,
        message: str,
        urgency: str,
    ) -> dict[str, Any]:
        result = await gateway.call(
            "notify_duty_caregiver",
            {"resident_id": resident_id, "message": message, "urgency": urgency},
        )
        return result.model_dump(mode="json")

    async def create_care_task(
        resident_id: str,
        title: str,
        priority: str,
    ) -> dict[str, Any]:
        result = await gateway.call(
            "create_care_task",
            {"resident_id": resident_id, "title": title, "priority": priority},
        )
        return result.model_dump(mode="json")

    return [
        StructuredTool.from_function(
            coroutine=notify_caregiver,
            name="notify_caregiver",
            description="Notify the caregiver assigned to the resident.",
            args_schema=CareNotificationInput,
        ),
        StructuredTool.from_function(
            coroutine=notify_duty_caregiver,
            name="notify_duty_caregiver",
            description="Notify the duty caregiver as a bounded fallback.",
            args_schema=CareNotificationInput,
        ),
        StructuredTool.from_function(
            coroutine=create_care_task,
            name="create_care_task",
            description="Create a tracked care coordination task.",
            args_schema=CareTaskInput,
        ),
    ]


def build_care_react_agent(
    model: Any,
    gateway: ScopedCareMCPClient,
    *,
    checkpointer: Any = None,
    max_iterations: int | None = None,
    max_tool_calls: int | None = None,
):
    return build_specialist_agent(
        model,
        get_agent_spec("care_agent"),
        build_care_tools(gateway),
        checkpointer=checkpointer,
        max_model_calls=(max_iterations + 1 if max_iterations is not None else None),
        max_tool_calls=max_tool_calls,
    )


__all__ = ["build_care_react_agent", "build_care_tools"]
