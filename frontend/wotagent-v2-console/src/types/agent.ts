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
  run_id: string
  turn_id: string
  task_id: string | null
  parent_task_id: string | null
  agent_run_id: string | null
  tool_call_id: string | null
  type: string
  node: string
  iteration: number
  data: Record<string, unknown>
}

export interface RunRecord {
  run_id: string
  conversation_id: string
  turn_id: string
  root_goal: string
  role: string
  user_id: string
  thinking: boolean
  status: string
  execution_strategy: 'sequential' | 'parallel_when_safe'
  success_criteria: string[]
  task_ids: string[]
  final_response: string
  created_at: string
  updated_at: string
}

export interface AgentTaskRecord {
  task_id: string
  run_id: string
  parent_task_id: string | null
  local_id: string
  agent_id: string
  objective: string
  reason: string
  status: string
  expected_evidence: string[]
  success_criteria: string[]
  depends_on: string[]
  parallel_candidate: boolean
  priority: number
  principal: {
    principal_id: string
    roles: string[]
  } | null
  target_type: string | null
  target_mention: string | null
  target: {
    type: string
    id: string
    display_name: string | null
  } | null
  requested_capability: string
  effective_scope: {
    resource_ids: string[]
    capabilities: string[]
  } | null
  finding: Record<string, unknown> | null
  failure: {
    category: string
    code: string
    message: string
    retryable: boolean
    fingerprint: string
  } | null
  confirmation: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface AgentRunRecord {
  agent_run_id: string
  run_id: string
  task_id: string
  agent_id: string
  attempt: number
  status: string
  iteration_count: number
  tool_call_count: number
  started_at: string
  finished_at: string | null
}

export interface RunConfirmation {
  taskId: string
  summary: string
  payload: Record<string, unknown>
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

export interface RuntimeRun {
  id: string
  turnId: string
  conversationId: string
  message: string
  role: AgentRole
  thinking: boolean
  status: string
  response: string
  executionStrategy: 'sequential' | 'parallel_when_safe'
  successCriteria: string[]
  tasks: AgentTaskRecord[]
  confirmation: RunConfirmation | null
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
  run_id: string
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
