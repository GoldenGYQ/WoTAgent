"""Agent capability registry independent from any deployment domain."""

from __future__ import annotations

from .contracts import AgentSpec


AGENT_SPECS: dict[str, AgentSpec] = {
    "health_agent": AgentSpec(
        agent_id="health_agent",
        display_name="健康信息分析 Agent",
        capability_category="health_analysis",
        delegation_capabilities=frozenset({"health.status.read"}),
        default_capability="health.status.read",
        domain="health",
        responsibility="调查生命体征、睡眠、趋势和健康数据质量",
        system_prompt=(
            "你是 Health Agent。根据目标主动读取必要的健康资源并形成证据链。"
            "不得诊断疾病、调整药物或替代医护人员。证据不足时明确返回"
            " needs_more_information。遵循最小必要证据原则：一般健康状态先读取"
            "健康摘要；摘要过期或缺少当前状态时再读取最新生命体征和数据质量。"
            "只有用户明确询问趋势或摘要提示需要比较时才读取趋势；只有目标明确"
            "涉及居民基础资料时才读取 resident context。一次任务总工具调用不得"
            "超过运行时预算，不得为了展示能力而并行读取全部资源。"
        ),
        runtime="eldercare_subgraph",
        allowed_tools=frozenset({
            "read_health_summary",
            "read_latest_vitals",
            "read_vital_trend",
            "check_data_quality",
            "read_resident_context",
        }),
        mcp_scopes=frozenset({"wot.health", "eldercare-health"}),
    ),
    "care_agent": AgentSpec(
        agent_id="care_agent",
        display_name="照护协同 Agent",
        capability_category="care_coordination",
        delegation_capabilities=frozenset({"care.coordinate"}),
        default_capability="care.coordinate",
        domain="care",
        responsibility="通知护理员并创建照护任务",
        system_prompt=(
            "你是 Care Agent。只执行护理协同，不做医疗诊断。失败时报告结构化"
            "错误，并在授权工具中选择至多一次替代方案。"
        ),
        runtime="eldercare_subgraph",
        allowed_tools=frozenset({
            "notify_caregiver",
            "notify_duty_caregiver",
            "create_care_task",
        }),
        mcp_scopes=frozenset({"eldercare-care"}),
    ),
    "environment_agent": AgentSpec(
        agent_id="environment_agent",
        display_name="环境调节 Agent",
        capability_category="environment_control",
        delegation_capabilities=frozenset({
            "environment.use",
            "environment.control",
        }),
        default_capability="environment.use",
        domain="environment",
        responsibility="调查并控制灯光、空调、空气、窗帘等环境设备",
        system_prompt=(
            "你是 Environment Agent。先观察设备状态，再按目标调用授权工具；"
            "状态改变后必须依据验证结果判断是否完成。"
        ),
        runtime="langgraph_subgraph",
        mcp_scopes=frozenset({"wot.environment"}),
    ),
    "security_agent": AgentSpec(
        agent_id="security_agent",
        display_name="安防处置 Agent",
        capability_category="security_response",
        delegation_capabilities=frozenset({
            "security.observe",
            "security.use",
        }),
        default_capability="security.use",
        domain="security",
        responsibility="处理门禁、燃气、烟雾、摄像头和应急联动",
        system_prompt=(
            "你是 Security Agent。敏感动作必须经过策略和人工确认，不能降低"
            "或忽略硬安全告警。"
        ),
        runtime="langgraph_subgraph",
        mcp_scopes=frozenset({"wot.security"}),
    ),
    "energy_agent": AgentSpec(
        agent_id="energy_agent",
        display_name="能耗管理 Agent",
        capability_category="energy_management",
        delegation_capabilities=frozenset({"energy.use"}),
        default_capability="energy.use",
        domain="energy",
        responsibility="读取能耗并执行受限负载调度",
        system_prompt="你是 Energy Agent。根据可验证能耗数据执行授权范围内的调度。",
        runtime="langgraph_subgraph",
        mcp_scopes=frozenset({"wot.energy"}),
    ),
    "housekeeping_agent": AgentSpec(
        agent_id="housekeeping_agent",
        display_name="家务执行 Agent",
        capability_category="housekeeping",
        delegation_capabilities=frozenset({"housekeeping.use"}),
        default_capability="housekeeping.use",
        domain="housekeeping",
        responsibility="控制清洁和家务设备",
        system_prompt="你是 Housekeeping Agent。使用授权设备能力完成清洁目标。",
        runtime="langgraph_subgraph",
        mcp_scopes=frozenset({"wot.housekeeping"}),
    ),
    "device_ops_agent": AgentSpec(
        agent_id="device_ops_agent",
        display_name="设备运维 Agent",
        capability_category="device_operations",
        delegation_capabilities=frozenset({"device_ops.use"}),
        default_capability="device_ops.use",
        domain="device_ops",
        responsibility="发现 TD、查询设备状态并诊断接入异常",
        system_prompt=(
            "你是 Device Ops Agent。只处理设备接入、TD、MCP、离线和能力声明问题，"
            "不代替环境 Agent 执行普通家居自动化。"
        ),
        runtime="langgraph_subgraph",
        mcp_scopes=frozenset({"wot.device_ops"}),
    ),
}


def get_agent_spec(agent_id: str) -> AgentSpec:
    try:
        return AGENT_SPECS[agent_id]
    except KeyError as exc:
        raise LookupError(f"unknown agent spec: {agent_id}") from exc


__all__ = ["AGENT_SPECS", "get_agent_spec"]
