import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { agentV2Api, type TraceSubscription } from '../api/v2'
import {
  ACTIVE_STATUSES,
  EMPTY_USAGE,
  type AgentRole,
  type DeploymentProfile,
  type RunRecord,
  type RuntimeConversation,
  type RuntimeRun,
  type TraceEvent,
} from '../types/agent'

const runs = reactive<RuntimeRun[]>([])
const conversations = reactive<RuntimeConversation[]>([])
const activeConversationId = ref<string | null>(null)
const activeRunId = ref<string | null>(null)
const role = ref<AgentRole>('operator')
const userId = ref('local-user')
const thinking = ref(localStorage.getItem('wotagent-v2-console.thinking') === 'true')
const thinkingSupported = ref(false)
const speechSupported = ref(false)
const speechMaxSeconds = ref(30)
const backendState = ref<'checking' | 'online' | 'offline'>('checking')
const deploymentProfiles = ref<DeploymentProfile[]>([])
const activeProfileId = ref(localStorage.getItem('wotagent-v2-console.deployment-profile') ?? '')
const subscriptions = new Map<string, TraceSubscription>()
const pollers = new Map<string, ReturnType<typeof setInterval>>()
const restorePromises = new Map<string, Promise<void>>()
const STORAGE_KEY = 'wotagent-v2-console.runs'
const ACTIVE_CONVERSATION_KEY = 'wotagent-v2-console.active-conversation'
let hydrated = false

interface StoredRun {
  id: string
  turnId: string
  conversationId: string
  message: string
  role: AgentRole
  thinking: boolean
  createdAt: string
}

const runById = (runId: string): RuntimeRun | undefined => runs.find((run) => run.id === runId)
const conversationById = (id: string): RuntimeConversation | undefined => conversations.find((item) => item.conversation_id === id)

function readStoredRuns(): StoredRun[] {
  try {
    const value = JSON.parse(localStorage.getItem(`${STORAGE_KEY}.${userId.value}`) ?? '[]')
    if (!Array.isArray(value)) return []
    return value
      .filter((item): item is StoredRun => Boolean(item && typeof item.id === 'string'))
      .filter((item, index, items) => items.findIndex((candidate) => candidate.id === item.id) === index)
      .slice(0, 30)
  } catch {
    return []
  }
}

function persistState(): void {
  const value: StoredRun[] = runs.slice(0, 30).map((run) => ({
    id: run.id,
    turnId: run.turnId,
    conversationId: run.conversationId,
    message: run.message,
    role: run.role,
    thinking: run.thinking,
    createdAt: run.createdAt,
  }))
  localStorage.setItem(`${STORAGE_KEY}.${userId.value}`, JSON.stringify(value))
  if (activeConversationId.value) {
    localStorage.setItem(
      `${ACTIVE_CONVERSATION_KEY}.${userId.value}`,
      activeConversationId.value,
    )
  } else {
    localStorage.removeItem(`${ACTIVE_CONVERSATION_KEY}.${userId.value}`)
  }
}

function createRuntimeRun(record: RunRecord, roleValue: AgentRole, thinkingValue: boolean): RuntimeRun {
  return {
    id: record.run_id,
    turnId: record.turn_id,
    conversationId: record.conversation_id,
    message: record.root_goal,
    role: roleValue,
    thinking: thinkingValue,
    status: record.status,
    response: record.final_response,
    executionStrategy: record.execution_strategy,
    successCriteria: record.success_criteria,
    tasks: [],
    confirmation: null,
    events: [],
    usage: { ...EMPTY_USAGE, ...record.usage },
    supervisorIterations: record.supervisor_iterations,
    agentLoops: record.agent_loops,
    recoveryCount: record.recoveries,
    durationMs: record.duration_ms,
    createdAt: record.created_at,
    updatedAt: record.updated_at,
    error: record.error ?? '',
    streamState: 'idle',
  }
}

function normalizedRole(value: string): AgentRole {
  return value === 'viewer' || value === 'admin' ? value : 'operator'
}

function applyRecord(run: RuntimeRun, record: RunRecord): void {
  run.status = record.status
  run.response = record.final_response || run.response
  run.executionStrategy = record.execution_strategy
  run.successCriteria = record.success_criteria
  run.usage = { ...EMPTY_USAGE, ...record.usage }
  run.supervisorIterations = record.supervisor_iterations
  run.agentLoops = record.agent_loops
  run.recoveryCount = record.recoveries
  run.durationMs = record.duration_ms
  run.updatedAt = record.updated_at
  run.error = record.error ?? run.error
}

function syncTurn(run: RuntimeRun): void {
  const conversation = conversationById(run.conversationId)
  if (!conversation) return
  let turn = conversation.turns.find((item) => item.run_id === run.id)
  if (!turn) {
    turn = {
      turn_id: run.turnId,
      conversation_id: run.conversationId,
      run_id: run.id,
      user_message: run.message,
      assistant_message: '',
      status: run.status,
      created_at: run.createdAt,
      updated_at: run.updatedAt,
    }
    conversation.turns.push(turn)
  }
  turn.status = run.status
  turn.updated_at = run.updatedAt
  if (run.response) turn.assistant_message = run.response
}

async function refreshConversation(conversationId: string): Promise<void> {
  const conversation = conversationById(conversationId)
  if (!conversation) return
  const response = await agentV2Api.getConversationTurns(conversationId)
  conversation.turns = response.turns
  conversation.turn_count = response.turns.length
}

async function refreshRun(runId: string): Promise<void> {
  const run = runById(runId)
  if (!run) return
  try {
    const shouldReplay = run.events.length === 0
    const [record, taskResponse, replay] = await Promise.all([
      agentV2Api.getRun(runId),
      agentV2Api.getRunTasks(runId),
      shouldReplay ? agentV2Api.getEvents(runId, 0) : Promise.resolve(null),
    ])
    applyRecord(run, record)
    run.tasks = taskResponse.tasks
    replay?.events.forEach((event) => addEvent(run, event, false))
    syncTurn(run)
    if (!ACTIVE_STATUSES.has(run.status)) {
      stopTransport(runId)
      await refreshConversation(run.conversationId)
    }
  } catch (error) {
    run.error = error instanceof Error ? error.message : String(error)
  }
}

function addEvent(run: RuntimeRun, event: TraceEvent, refresh = true): void {
  if (run.events.some((item) => item.sequence === event.sequence)) return
  run.events.push(event)
  run.events.sort((a, b) => a.sequence - b.sequence)
  run.updatedAt = event.timestamp
  if (event.type === 'task.waiting_confirmation' && event.task_id) {
    const payload = (event.data.confirmation ?? {}) as Record<string, unknown>
    run.status = 'waiting_confirmation'
    run.confirmation = {
      taskId: event.task_id,
      summary: String(payload.summary ?? '该设备操作需要人工确认'),
      payload,
    }
  }
  if (event.type === 'task.confirmation.resolved') {
    run.confirmation = null
    run.status = 'running'
  }
  if (event.type.startsWith('run.') && event.type !== 'run.started') {
    run.status = event.type.slice(4)
    run.response = String(event.data.response ?? run.response)
  }
  if (event.type === 'run.failed') run.error = String(event.data.error ?? '运行失败')
  if (refresh && (event.type.startsWith('task.') || event.type.startsWith('run.'))) {
    void refreshRun(run.id)
  }
  syncTurn(run)
}

function startPolling(runId: string): void {
  if (pollers.has(runId)) return
  const run = runById(runId)
  if (!run || !ACTIVE_STATUSES.has(run.status)) return
  run.streamState = 'polling'
  const poll = async () => {
    const current = runById(runId)
    if (!current) return
    const response = await agentV2Api.getEvents(runId, current.events.at(-1)?.sequence ?? 0)
    response.events.forEach((event) => addEvent(current, event))
    await refreshRun(runId)
  }
  void poll()
  pollers.set(runId, setInterval(() => void poll(), 1200))
}

function startTrace(runId: string): void {
  const run = runById(runId)
  if (!run) return
  subscriptions.get(runId)?.close()
  run.streamState = 'connecting'
  subscriptions.set(runId, agentV2Api.subscribe(runId, run.events.at(-1)?.sequence ?? 0, {
    onOpen: () => {
      run.streamState = 'live'
      const poller = pollers.get(runId)
      if (poller) clearInterval(poller)
      pollers.delete(runId)
    },
    onEvent: (event) => addEvent(run, event),
    onComplete: () => {
      run.streamState = 'closed'
      void refreshRun(runId)
    },
    onError: () => startPolling(runId),
  }))
}

function stopTransport(runId: string): void {
  subscriptions.get(runId)?.close()
  subscriptions.delete(runId)
  const poller = pollers.get(runId)
  if (poller) clearInterval(poller)
  pollers.delete(runId)
  const run = runById(runId)
  if (run) run.streamState = 'closed'
}

function restoreRun(stored: StoredRun): Promise<void> {
  const inFlight = restorePromises.get(stored.id)
  if (inFlight) return inFlight

  const restore = (async () => {
    if (!conversationById(stored.conversationId)) return
    try {
      const record = await agentV2Api.getRun(stored.id)
      let run = runById(stored.id)
      if (!run) {
        run = createRuntimeRun(record, stored.role, stored.thinking)
        runs.push(run)
      } else {
        applyRecord(run, record)
      }
      await refreshRun(run.id)
      if (ACTIVE_STATUSES.has(run.status)) startTrace(run.id)
    } catch {
      // The backend may have been reset between frontend sessions.
    }
  })().finally(() => {
    restorePromises.delete(stored.id)
  })

  restorePromises.set(stored.id, restore)
  return restore
}

async function inspectRun(runId: string): Promise<void> {
  const existing = runById(runId)
  if (existing) {
    activeConversationId.value = existing.conversationId
    activeRunId.value = runId
    return
  }
  const record = await agentV2Api.getRun(runId)
  let conversation = conversationById(record.conversation_id)
  if (!conversation) {
    const response = await agentV2Api.getConversationTurns(record.conversation_id)
    conversation = {
      conversation_id: record.conversation_id,
      user_id: record.user_id,
      role: normalizedRole(record.role),
      title: record.source === 'monitor' ? 'Monitor 事件处置' : record.root_goal.slice(0, 36),
      created_at: record.created_at,
      updated_at: record.updated_at,
      turn_count: response.turns.length,
      turns: response.turns,
      loading: false,
    }
    conversations.unshift(conversation)
  }
  const run = createRuntimeRun(record, normalizedRole(record.role), record.thinking)
  runs.unshift(run)
  const replay = await agentV2Api.getEvents(run.id)
  replay.events.forEach((event) => addEvent(run, event))
  await refreshRun(run.id)
  activeConversationId.value = run.conversationId
  activeRunId.value = run.id
}

async function restoreConversationRuns(conversation: RuntimeConversation): Promise<void> {
  await Promise.all(conversation.turns.map((turn) => restoreRun({
    id: turn.run_id,
    turnId: turn.turn_id,
    conversationId: conversation.conversation_id,
    message: turn.user_message,
    role: conversation.role,
    thinking: false,
    createdAt: turn.created_at,
  })))
}

async function hydrateRuntime(): Promise<void> {
  if (hydrated) return
  hydrated = true
  const response = await agentV2Api.listConversations(userId.value)
  conversations.splice(0, conversations.length, ...response.conversations.map((item) => ({ ...item, turns: [], loading: false })))
  await Promise.all(readStoredRuns().map(restoreRun))
  activeConversationId.value = null
  activeRunId.value = null
  localStorage.removeItem(`${ACTIVE_CONVERSATION_KEY}.${userId.value}`)
}

async function ensureConversation(): Promise<RuntimeConversation> {
  const current = conversationById(activeConversationId.value ?? '')
  if (current) return current
  const created = await agentV2Api.createConversation(role.value, userId.value)
  const conversation = { ...created, turns: [], loading: false }
  conversations.unshift(conversation)
  activeConversationId.value = conversation.conversation_id
  return conversation
}

async function submit(
  message: string,
  options?: { phoneMode?: boolean },
): Promise<RuntimeRun> {
  const conversation = await ensureConversation()
  const record = await agentV2Api.createRun(
    message,
    conversation.conversation_id,
    role.value,
    thinking.value && thinkingSupported.value,
    userId.value,
    activeProfileId.value || undefined,
    options,
  )
  const run = createRuntimeRun(record, role.value, thinking.value && thinkingSupported.value)
  runs.unshift(run)
  activeRunId.value = run.id
  if (conversation.title === '新会话') conversation.title = message.trim().slice(0, 36)
  conversation.updated_at = run.createdAt
  conversation.turn_count += 1
  syncTurn(run)
  startTrace(run.id)
  return run
}

async function confirm(runId: string, taskId: string, approved: boolean): Promise<void> {
  const run = runById(runId)
  if (!run) return
  try {
    run.confirmation = null
    run.status = 'running'
    applyRecord(run, await agentV2Api.confirmTask(runId, taskId, approved))
    await refreshRun(runId)
  } catch (error) {
    run.error = error instanceof Error ? error.message : String(error)
  }
}

async function selectConversation(conversationId: string): Promise<void> {
  activeConversationId.value = conversationId
  const conversation = conversationById(conversationId)
  if (!conversation) return
  conversation.loading = true
  await refreshConversation(conversationId)
  await restoreConversationRuns(conversation)
  conversation.loading = false
  activeRunId.value = runs.filter((run) => run.conversationId === conversationId)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]?.id ?? null
}

function newConversation(): void {
  activeConversationId.value = null
  activeRunId.value = null
  localStorage.removeItem(`${ACTIVE_CONVERSATION_KEY}.${userId.value}`)
}

async function deleteConversation(conversationId: string): Promise<void> {
  await agentV2Api.deleteConversation(conversationId)
  const index = conversations.findIndex((item) => item.conversation_id === conversationId)
  if (index >= 0) conversations.splice(index, 1)
  runs.filter((run) => run.conversationId === conversationId).forEach((run) => {
    stopTransport(run.id)
    runs.splice(runs.indexOf(run), 1)
  })
  newConversation()
}

async function checkBackend(): Promise<void> {
  backendState.value = 'checking'
  try {
    await agentV2Api.health()
    const [capabilities, deployments] = await Promise.all([
      agentV2Api.capabilities(),
      agentV2Api.listDeploymentProfiles(),
    ])
    deploymentProfiles.value = deployments.profiles
    if (!deploymentProfiles.value.some((item) => item.profile_id === activeProfileId.value)) {
      activeProfileId.value = deployments.default_profile_id
    }
    await agentV2Api.activateDeploymentProfile(activeProfileId.value)
    thinkingSupported.value = capabilities.thinking.supported
    speechSupported.value = capabilities.speech_transcription?.supported ?? false
    speechMaxSeconds.value = capabilities.speech_transcription?.max_seconds ?? 30
    if (!thinkingSupported.value) thinking.value = false
    backendState.value = 'online'
  } catch {
    backendState.value = 'offline'
  }
}

async function switchDeploymentProfile(profileId: string): Promise<void> {
  if (profileId === activeProfileId.value) return
  const response = await agentV2Api.activateDeploymentProfile(profileId)
  activeProfileId.value = response.profile.profile_id
  localStorage.setItem(
    'wotagent-v2-console.deployment-profile',
    activeProfileId.value,
  )
  newConversation()
}

function resetRuntimeState(): void {
  subscriptions.forEach((item) => item.close())
  pollers.forEach((item) => clearInterval(item))
  subscriptions.clear()
  pollers.clear()
  restorePromises.clear()
  runs.splice(0)
  conversations.splice(0)
  activeConversationId.value = null
  activeRunId.value = null
  hydrated = false
}

async function switchIdentity(nextUserId: string, nextRole: AgentRole): Promise<void> {
  resetRuntimeState()
  userId.value = nextUserId
  role.value = nextRole
  await hydrateRuntime()
}

function clearIdentity(): void {
  resetRuntimeState()
  userId.value = 'local-user'
  role.value = 'operator'
}

export function useAgentRuntime() {
  const activeConversation = computed(() => conversationById(activeConversationId.value ?? '') ?? null)
  const activeRun = computed(() => runById(activeRunId.value ?? '') ?? null)
  watch([runs, activeConversationId], persistState, { deep: true })
  watch(thinking, (value) => localStorage.setItem('wotagent-v2-console.thinking', String(value)))
  onBeforeUnmount(resetRuntimeState)
  return {
    runs,
    conversations,
    activeConversationId,
    activeConversation,
    activeRunId,
    activeRun,
    role,
    userId,
    thinking,
    thinkingSupported,
    speechSupported,
    speechMaxSeconds,
    backendState,
    deploymentProfiles,
    activeProfileId,
    submit,
    confirm,
    selectConversation,
    newConversation,
    deleteConversation,
    refreshRun,
    checkBackend,
    hydrateRuntime,
    switchIdentity,
    clearIdentity,
    inspectRun,
    switchDeploymentProfile,
  }
}
