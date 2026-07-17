import type { TraceEvent } from '../types/agent'

export type EventTone = 'neutral' | 'model' | 'policy' | 'tool' | 'verify' | 'success' | 'danger'

export function eventTone(event: TraceEvent): EventTone {
  if (event.type.includes('failed') || event.type.includes('fallback')) return 'danger'
  if (event.type.startsWith('model.')) return 'model'
  if (event.type === 'context.built') return 'verify'
  if (event.type.startsWith('policy.')) return 'policy'
  if (event.type.startsWith('agent.tool') || event.type.startsWith('agent.resource')) return 'tool'
  if (
    event.type.startsWith('verification.')
    || event.type === 'agent.finding.completed'
    || event.type === 'agent.goal.evaluated'
  ) return 'verify'
  if (event.type === 'task.completed' || event.type === 'run.completed') return 'success'
  return 'neutral'
}

export function eventTitle(event: TraceEvent): string {
  const labels: Record<string, string> = {
    'task.started': '任务开始',
    'task.waiting_confirmation': '等待确认',
    'task.succeeded': '任务完成',
    'task.failed': '任务失败',
    'context.built': '会话上下文',
    'agent.node.started': `${nodeLabel(event.node)}启动`,
    'agent.node.completed': `${nodeLabel(event.node)}完成`,
    'agent.node.failed': `${nodeLabel(event.node)}失败`,
    'model.request.started': '模型请求',
    'model.request.completed': '模型响应',
    'model.reasoning.summary': '推理摘要',
    'policy.evaluated': '策略判定',
    'agent.tool.completed': '工具执行',
    'verification.completed': '状态验证',
    'agent.loop.completed': '循环完成',
    'agent.recovery.completed': '恢复规划',
    'agent.fast_path.selected': '规则快速通道',
    'agent.goal.created': 'Butler 建立目标',
    'agent.goal.evaluated': 'Butler 评估目标',
    'agent.delegation.created': '创建专业委派',
    'agent.delegation.returned': '专业 Agent 退回 Butler',
    'agent.reroute.selected': 'Butler 重新委派',
    'agent.delegation.started': `${nodeLabel(event.node)}开始任务`,
    'agent.health.decision': 'Health Agent 决策',
    'agent.resource.completed': '读取 MCP Resource',
    'agent.finding.completed': `${nodeLabel(event.node)}提交发现`,
    'agent.fallback.activated': '启用规则回退',
    'agent.shadow.compared': 'Shadow 策略比较',
  }
  const runLabels: Record<string, string> = {
    'run.started': 'Run 开始',
    'run.completed': 'Run 完成',
    'run.partial_success': 'Run 部分完成',
    'run.failed': 'Run 失败',
    'supervisor.decision.completed': 'Supervisor 决策',
    'task.created': '创建专项 Task',
    'task.started': '专业 Agent 开始',
    'task.completed': '专项 Task 完成',
    'task.blocked': '专项 Task 受阻',
    'scheduler.parallel_batch.completed': '调度批次完成',
  }
  return runLabels[event.type] ?? labels[event.type] ?? event.type
}

export function eventSummary(event: TraceEvent): string {
  const data = event.data
  if (event.type === 'task.created') return `${String(data.agent_id ?? event.node)} · ${String(data.objective ?? '')}`
  if (event.type === 'task.started') return String(data.objective ?? '')
  if (event.type === 'scheduler.parallel_batch.completed') {
    const ids = Array.isArray(data.task_ids) ? data.task_ids : []
    return `${ids.length} 个 Task · ${data.parallel === true ? '并行' : '串行'}`
  }
  if (event.type === 'supervisor.decision.completed') return String(data.reason ?? data.action ?? '')
  if (event.type.startsWith('run.')) return String(data.response ?? data.root_goal ?? '')
  if (event.type === 'context.built') {
    return `${number(data.history_messages)} 条历史 · ${number(data.history_characters)} 字符`
  }
  if (event.type === 'model.request.started') return String(data.purpose ?? '调用模型')
  if (event.type === 'model.request.completed') {
    return `${number(data.total_tokens)} tokens · ${duration(Number(data.duration_ms ?? 0))}`
  }
  if (event.type === 'model.reasoning.summary') return String(data.summary ?? data.intent ?? '')
  if (event.type === 'policy.evaluated') {
    const decisions = Array.isArray(data.decisions) ? data.decisions : []
    const denied = decisions.filter((item) => isRecord(item) && item.effect === 'deny').length
    return denied ? `${denied} 项被拒绝` : `${decisions.length} 项通过`
  }
  if (event.type === 'agent.tool.completed') {
    return `${String(data.tool ?? 'tool')} · ${String(data.step_id ?? '')}`
  }
  if (event.type === 'agent.resource.completed') {
    return `${String(data.tool ?? data.uri ?? 'resource')} · ${outputState(data.output)}`
  }
  if (event.type === 'agent.goal.created') {
    const plan = record(data.goal_plan)
    return String(plan.goal ?? data.goal ?? '已生成目标与成功条件')
  }
  if (event.type === 'agent.goal.evaluated') {
    const evaluation = record(data.evaluation)
    const complete = evaluation.goal_completed === true
    const remaining = Array.isArray(evaluation.unsatisfied_criteria)
      ? evaluation.unsatisfied_criteria.length
      : 0
    return complete ? '成功条件已满足' : `${remaining} 项成功条件尚未满足`
  }
  if (event.type === 'agent.delegation.created') {
    const delegations = Array.isArray(data.delegations) ? data.delegations : []
    if (data.agent_id) {
      return `${String(data.agent_id)} · ${String(data.resolved_request ?? data.reason ?? '')}`
    }
    return `${delegations.length} 个专业任务`
  }
  if (event.type === 'agent.delegation.returned') {
    return `${event.node || 'specialist'} · ${String(data.reason ?? '请求 Butler 重新评估')}`
  }
  if (event.type === 'agent.reroute.selected') {
    return `${String(data.agent_id ?? 'unknown')} · ${String(data.reason ?? '')}`
  }
  if (event.type === 'agent.delegation.started') {
    return String(data.objective ?? data.reason ?? '执行专业任务')
  }
  if (event.type === 'agent.health.decision') {
    return `${String(data.action ?? 'decision')} · ${String(data.tool ?? data.reason ?? '')}`
  }
  if (event.type === 'agent.finding.completed') {
    const finding = record(data.finding)
    return String(finding.finding ?? finding.status ?? '已返回结构化证据')
  }
  if (event.type === 'agent.fast_path.selected') return String(data.reason ?? '规则直接生成计划')
  if (event.type === 'agent.fallback.activated') {
    return `${String(data.component ?? 'agent')} → ${String(data.fallback ?? 'rule')}`
  }
  if (event.type === 'agent.shadow.compared') return '规则路径真实执行，Hybrid 路径仅比较'
  if (event.type === 'verification.completed') {
    return data.passed === true ? '后置条件通过' : '后置条件未满足'
  }
  if (event.type === 'agent.loop.completed') return `第 ${event.iteration || 1} 轮 · ${String(data.status ?? '')}`
  if (event.type === 'agent.node.completed' && data.duration_ms) return duration(Number(data.duration_ms))
  if (event.type === 'task.failed') return String(data.error ?? '执行失败')
  if (event.type.startsWith('task.')) return String(data.response ?? '')
  return event.node ? nodeLabel(event.node) : ''
}

export function nodeLabel(node: string): string {
  const labels: Record<string, string> = {
    planner: 'Planner',
    policy: 'Policy',
    executor: 'Executor',
    verifier: 'Verifier',
    recovery: 'Recovery',
    responder: 'Response',
    orchestrator: 'Loop',
    butler: 'Butler',
    butler_supervisor: 'Butler',
    butler_goal_planner: 'Butler Planner',
    butler_goal_evaluator: 'Butler Evaluator',
    health_agent: 'Health Agent',
    care_agent: 'Care Agent',
    environment_agent: 'Environment Agent',
    security_agent: 'Security Agent',
    energy_agent: 'Energy Agent',
    housekeeping_agent: 'Housekeeping Agent',
    device_ops_agent: 'DeviceOps Agent',
    delegation_policy: 'Delegation Policy',
    rule_planner: 'Rule Planner',
    api: 'API',
    context: 'Context',
  }
  return labels[node] ?? (node || 'Agent')
}

export function formatTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

export function duration(value: number | null | undefined): string {
  if (!value) return '0 ms'
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`
}

function number(value: unknown): string {
  return Number(value ?? 0).toLocaleString('zh-CN')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

function outputState(value: unknown): string {
  const output = record(value)
  if (output.verified === true || output.success === true) return '已获得结果'
  if (output.error_message || output.error) return '读取失败'
  return '已完成'
}
