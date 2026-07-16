export type AgentRole = 'viewer' | 'operator' | 'admin'

export interface LoginAccount {
  accountId: 'JH-1' | 'JH-2'
  displayName: string
  role: AgentRole
  residentId: string
  residentName: string
}

export interface UsageSummary {
  model_calls: number
  tool_calls: number
  input_tokens: number
  output_tokens: number
  reasoning_tokens: number
  total_tokens: number
  cache_hit_tokens: number
  cache_miss_tokens: number
  estimated_cost: number
  currency: string
  pricing_available: boolean
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
  turn_id: string | null
  conversation_id: string
  session_id: string
  status: string
  response: string
  plan: TaskPlan | null
  trace: Record<string, unknown> | null
  confirmation: ConfirmationRequest | null
  thinking: boolean
}

export interface ApiCapabilities {
  thinking: {
    supported: boolean
    default: boolean
    reasoning_effort: string
    private_reasoning_exposed: boolean
  }
  streaming: boolean
}

export type SensorType = 'heart_rate' | 'spo2' | 'fall_detected' | 'bed_occupancy' | 'sleep_quality' | 'room_temperature' | 'room_humidity'

export interface Resident {
  resident_id: string
  name: string
  room: string
  caregiver: string
  active: boolean
}

export interface HealthSummary {
  resident: Resident
  latest: Record<string, { value: number | boolean | string; unit: string; observed_at: string; source: string }>
  heart_rate_avg: number | null
  open_risks: RiskEvent[]
  generated_at: string
}

export interface RiskEvent {
  event_id: string
  resident_id: string
  event_type: string
  severity: 'medium' | 'high' | 'critical'
  value: number | boolean
  message: string
  status: string
  occurred_at: string
}

export interface CareAction {
  action_id: string
  resident_id: string
  agent_id: string
  action: string
  arguments: Record<string, unknown>
  result: Record<string, unknown>
  created_at: string
}

export interface EldercareSimulationResult {
  scenario: string
  resident_id: string
  events: RiskEvent[]
  tasks: Array<{ task_id: string; status: string; response: string }>
}

export interface EldercareDevice {
  device_id: string
  name: string
  kind: 'sensor' | 'actuator'
  transport: string
  status: 'online' | 'stale' | 'no_data' | 'offline'
  last_seen: string | null
  capabilities: string[]
  state: Record<string, unknown>
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
  turnId: string
  conversationId: string
  sessionId: string
  message: string
  role: AgentRole
  thinking: boolean
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

export interface ConversationTurn {
  turn_id: string
  conversation_id: string
  task_id: string
  user_message: string
  assistant_message: string
  status: string
  created_at: string
  updated_at: string
}

export interface ConversationRecord {
  conversation_id: string
  user_id: string
  role: AgentRole
  title: string
  created_at: string
  updated_at: string
  turn_count: number
}

export interface RuntimeConversation extends ConversationRecord {
  turns: ConversationTurn[]
  loading: boolean
}

export const EMPTY_USAGE: UsageSummary = {
  model_calls: 0,
  tool_calls: 0,
  input_tokens: 0,
  output_tokens: 0,
  reasoning_tokens: 0,
  total_tokens: 0,
  cache_hit_tokens: 0,
  cache_miss_tokens: 0,
  estimated_cost: 0,
  currency: 'CNY',
  pricing_available: false,
  model_latency_ms: 0,
  ttft_ms_total: 0,
  ttft_measurements: 0,
}

export const ACTIVE_STATUSES = new Set(['accepted', 'received', 'running', 'waiting_confirmation'])
