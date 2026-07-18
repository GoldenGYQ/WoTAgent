"""Health specialist assembly over scoped MCP resources."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..domains.eldercare.health_gateway import HEALTH_RESOURCE_URIS, ScopedHealthMCPClient
from .factory import build_specialist_agent
from .specs import get_agent_spec


class ResidentResourceInput(BaseModel):
    resident_id: str = Field(
        min_length=1,
        description="Canonical resident identifier from the resident context.",
    )


def build_health_tools(gateway: ScopedHealthMCPClient) -> list[BaseTool]:
    """Adapt scoped MCP Resources to tools without weakening the MCP boundary."""
    tools: list[BaseTool] = []
    descriptions = {
        "read_health_summary": "读取居民健康摘要及其生成时间。",
        "read_latest_vitals": "读取居民最新生命体征及观测时间。",
        "read_vital_trend": "读取居民近期生命体征趋势。",
        "check_data_quality": "检查居民健康数据的新鲜度和缺失情况。",
        "read_resident_context": "读取非诊断性的居民基础资料。",
    }
    for tool_name in sorted(HEALTH_RESOURCE_URIS):
        async def call_resource(
            resident_id: str,
            *,
            _tool_name: str = tool_name,
        ) -> dict[str, Any]:
            result = await gateway.call(
                _tool_name,
                {"resident_id": resident_id},
            )
            return result.model_dump(mode="json")

        tools.append(StructuredTool.from_function(
            coroutine=call_resource,
            name=tool_name,
            description=descriptions[tool_name],
            args_schema=ResidentResourceInput,
        ))
    return tools


def build_health_react_agent(
    model: Any,
    gateway: ScopedHealthMCPClient,
    *,
    checkpointer: Any = None,
    max_iterations: int | None = None,
    max_tool_calls: int | None = None,
):
    return build_specialist_agent(
        model,
        get_agent_spec("health_agent"),
        build_health_tools(gateway),
        checkpointer=checkpointer,
        max_model_calls=(
            max_iterations + 1
            if max_iterations is not None
            else None
        ),
        max_tool_calls=max_tool_calls,
    )


__all__ = [
    "ResidentResourceInput",
    "build_health_react_agent",
    "build_health_tools",
]
