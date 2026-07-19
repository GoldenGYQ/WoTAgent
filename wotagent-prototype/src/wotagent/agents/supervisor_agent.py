"""Goal-driven create_agent Supervisor that proposes Task DAG decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.agents import create_agent

from ..orchestration.run_models import SupervisorDecision
from .contracts import AgentSpec


SUPERVISOR_TASK_DECOMPOSITION_RULES = (
    "条件工作流的 Task 边界规则：如果“观察或查询 → 计算、比较或条件判断 → "
    "选择动作 → 执行动作 → 验证结果”的完整链路都属于同一个专业 Agent 的能力"
    "范围，应优先创建一个包含完整条件目标的 Task，让该专业 Agent 在自己的有限"
    "循环内根据真实观察决定后续动作。不要仅为了展示步骤而把同一专业 Agent 的"
    "条件链拆成多个 Task。只有涉及不同专业 Agent、可独立并行的目标、明确的权限"
    "边界或确实需要跨专业交接时才拆分 Task。拆分后的 depends_on 表示执行依赖，"
    "系统会把上游结构化 Finding 传给下游，不要要求下游重新查询已经取得且仍然"
    "有效的事实。resource_claims 仍应根据每个 Task 可能访问或改变的资源，由你"
    "结合目标合理生成；Scheduler 只负责校验冲突和决定是否并行。\n\n"
)


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
            "target_type；不得猜测内部规范 ID。requested_capability 是自然语言"
            "能力提议，不要求你猜内部 capability ID；后端会根据 AgentSpec、任务"
            "读写模式和场景将其规范化。最终 effective_scope 只能由后端授权模块"
            "签发。\n\n"
            "遵循最小必要任务原则：不得把用户的一般查询自动扩展为该专业 Agent "
            "能够调查的全部项目。用户只问“今天健康状态”时，不要自动追加睡眠、"
            "长期趋势等未明确要求的成功条件；仅创建回答当前问题所需的最小证据"
            "集合，后续是否补查由专业 Agent 根据实际观察决定。\n\n"
            "每次决策必须遵守输入中的 deployment 上下文，只能选择 "
            "enabled_agents 中的 Agent，不得引用当前场景之外的空间、居民或设备。\n\n"
            f"{SUPERVISOR_TASK_DECOMPOSITION_RULES}"
            f"当前可用专业 Agent：\n{agent_catalog}"
        ),
        checkpointer=checkpointer,
        name="butler_run_supervisor",
    )


__all__ = [
    "SUPERVISOR_TASK_DECOMPOSITION_RULES",
    "build_run_supervisor_agent",
]
