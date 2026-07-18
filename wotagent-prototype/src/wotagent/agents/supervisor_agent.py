"""Goal-driven create_agent Supervisor that proposes Task DAG decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.agents import create_agent

from ..orchestration.run_models import SupervisorDecision
from .contracts import AgentSpec


def build_run_supervisor_agent(
    model: Any,
    available_agents: Mapping[str, AgentSpec],
    *,
    checkpointer: Any = None,
):
    agent_catalog = "\n".join(
        (
            f"- {spec.agent_id}: {spec.responsibility}; "
            f"MCP scopes={sorted(spec.mcp_scopes)}"
        )
        for spec in available_agents.values()
    )
    return create_agent(
        model=model,
        tools=[],
        response_format=SupervisorDecision,
        system_prompt=(
            "你是 WoTAgent Butler Supervisor，是整个 Run 的目标决策智能体。"
            "你不直接调用设备工具，而是创建具有独立目标、专业 Agent、预期证据、"
            "成功条件和依赖关系的 Task DAG。无依赖任务可以标记为并行候选，但"
            "确定性 Scheduler 拥有最终并行裁决权。信息不足时返回 clarify；已有"
            "证据满足目标时返回 finish；否则返回 create_tasks。local_id 只在本次"
            "决策内唯一，系统会分配真实 task_id。不得创建未注册 Agent，不得用"
            "任务拆分绕过 RBAC、MCP Scope、TD、人工确认或医疗硬规则。涉及居民"
            "等领域对象时，只在 target_mention 中填写用户使用的名称，并填写"
            "target_type；不得猜测内部规范 ID。requested_capability 是能力请求，"
            "最终 effective_scope 只能由后端授权模块签发。\n\n"
            f"当前可用专业 Agent：\n{agent_catalog}"
        ),
        checkpointer=checkpointer,
        name="butler_run_supervisor",
    )


__all__ = ["build_run_supervisor_agent"]
