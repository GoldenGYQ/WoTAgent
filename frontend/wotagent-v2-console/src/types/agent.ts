export type AgentRole = 'viewer' | 'operator' | 'admin'

export interface UsageSummary {
  model_calls: number
  tool_calls: number
  input_tokens: number
  output_tokens: number
  reasoning_tokens: number
  total_tokens: number
  estimated_cost: number
  currency: string
  model_latency_ms: number
  ttft_ms_total: number
  ttft_measurements: number
}

export interface TraceEvent {
  event_id: string
  sequence: number
  timestamp: string
  trace_id: string
  task_id: string
  session_id: string
  type: string
  node: string
  iteration: number
  duration_ms: number | null
  data: Record<string, unknown>
}

export interface TaskCreateResponse {
  task_id: string | null
  session_id: string
  status: string
  response: string
  plan: TaskPlan | null
  trace: Record<string, unknown> | null
  confirmation: ConfirmationRequest | null
}

export interface TaskRecord {
  trace_id: string
  task_id: string
  session_id: string
  request_id: string
  status: string
  created_at: string
  updated_at: string
  loop_count: number
  recovery_count: number
  usage: UsageSummary
  result: TaskCreateResponse | { error?: string } | null
}

export interface PlanCondition {
  property: string
  operator: string
  value: unknown
}

export interface PlanStep {
  id: string
  kind: 'query' | 'action'
  device_id: string
  action: string
  input: Record<string, unknown>
  depends_on: string[]
  postconditions: PlanCondition[]
}

export interface TaskPlan {
  plan_id: string
  intent: string
  summary: string
  assumptions: string[]
  steps: PlanStep[]
}

export interface ConfirmationRequest {
  type: string
  plan_id: string
  summary: string
  steps: PlanStep[]
}

export type StreamState = 'idle' | 'connecting' | 'live' | 'polling' | 'closed'

export interface RuntimeTask {
  id: string
  sessionId: string
  message: string
  role: AgentRole
  status: string
  response: string
  plan: TaskPlan | null
  confirmation: ConfirmationRequest | null
  events: TraceEvent[]
  usage: UsageSummary
  loopCount: number
  recoveryCount: number
  createdAt: string
  updatedAt: string
  error: string
  streamState: StreamState
}

export const EMPTY_USAGE: UsageSummary = {
  model_calls: 0,
  tool_calls: 0,
  input_tokens: 0,
  output_tokens: 0,
  reasoning_tokens: 0,
  total_tokens: 0,
  estimated_cost: 0,
  currency: 'CNY',
  model_latency_ms: 0,
  ttft_ms_total: 0,
  ttft_measurements: 0,
}

export const ACTIVE_STATUSES = new Set(['accepted', 'received', 'running', 'waiting_confirmation'])
