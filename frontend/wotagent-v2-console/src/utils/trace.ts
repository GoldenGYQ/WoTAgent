import type { TraceEvent } from '../types/agent'

export type EventTone = 'neutral' | 'model' | 'policy' | 'tool' | 'verify' | 'success' | 'danger'

export function eventTone(event: TraceEvent): EventTone {
  if (event.type.includes('failed')) return 'danger'
  if (event.type.startsWith('model.')) return 'model'
  if (event.type === 'context.built') return 'verify'
  if (event.type.startsWith('policy.')) return 'policy'
  if (event.type.startsWith('agent.tool')) return 'tool'
  if (event.type.startsWith('verification.')) return 'verify'
  if (event.type.startsWith('task.succeeded')) return 'success'
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
  }
  return labels[event.type] ?? event.type
}

export function eventSummary(event: TraceEvent): string {
  const data = event.data
  if (event.type === 'context.built') {
    return `${number(data.history_messages)} 条历史 · ${number(data.history_characters)} 字符`
  }
  if (event.type === 'model.request.started') return String(data.purpose ?? '调用模型')
  if (event.type === 'model.request.completed') {
    return `${number(data.total_tokens)} tokens · ${duration(event.duration_ms)}`
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
  if (event.type === 'verification.completed') {
    return data.passed === true ? '后置条件通过' : '后置条件未满足'
  }
  if (event.type === 'agent.loop.completed') return `第 ${event.iteration || 1} 轮 · ${String(data.status ?? '')}`
  if (event.type === 'agent.node.completed' && event.duration_ms) return duration(event.duration_ms)
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
