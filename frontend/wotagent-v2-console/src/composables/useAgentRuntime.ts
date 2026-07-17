import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { agentV2Api, type TraceSubscription } from '../api/v2'
import {
  ACTIVE_STATUSES,
  EMPTY_USAGE,
  type AgentRole,
  type RuntimeConversation,
  type RuntimeTask,
  type TaskCreateResponse,
  type TaskRecord,
  type TraceEvent,
} from '../types/agent'

const tasks = reactive<RuntimeTask[]>([])
const conversations = reactive<RuntimeConversation[]>([])
const activeConversationId = ref<string | null>(null)
const activeTaskId = ref<string | null>(null)
const role = ref<AgentRole>('operator')
const userId = ref('local-user')
const thinking = ref(localStorage.getItem('wotagent-v2-console.thinking') === 'true')
const thinkingSupported = ref(false)
const backendState = ref<'checking' | 'online' | 'offline'>('checking')
const subscriptions = new Map<string, TraceSubscription>()
const pollers = new Map<string, ReturnType<typeof setInterval>>()
const restoringTaskIds = new Set<string>()
const STORAGE_KEY = 'wotagent-v2-console.tasks'
const ACTIVE_CONVERSATION_KEY = 'wotagent-v2-console.active-conversation'
let hydrated = false

interface StoredTask {
  id: string
  turnId?: string
  conversationId?: string
  sessionId: string
  message: string
  role: AgentRole
  thinking?: boolean
  createdAt: string
}

function taskById(taskId: string): RuntimeTask | undefined {
  return tasks
    .filter((task) => task.id === taskId)
    .sort((left, right) => right.events.length - left.events.length)[0]
}

function conversationById(conversationId: string): RuntimeConversation | undefined {
  return conversations.find((conversation) => conversation.conversation_id === conversationId)
}

function readStoredTasks(): StoredTask[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(`${STORAGE_KEY}.${userId.value}`) ?? '[]')
    return Array.isArray(parsed) ? parsed.slice(0, 30) : []
  } catch {
    return []
  }
}

function persistState(): void {
  const snapshot: StoredTask[] = tasks.slice(0, 30).map((task) => ({
    id: task.id,
    turnId: task.turnId,
    conversationId: task.conversationId,
    sessionId: task.sessionId,
    message: task.message,
    role: task.role,
    thinking: task.thinking,
    createdAt: task.createdAt,
  }))
  localStorage.setItem(`${STORAGE_KEY}.${userId.value}`, JSON.stringify(snapshot))
  if (activeConversationId.value) {
    localStorage.setItem(`${ACTIVE_CONVERSATION_KEY}.${userId.value}`, activeConversationId.value)
  } else {
    localStorage.removeItem(`${ACTIVE_CONVERSATION_KEY}.${userId.value}`)
  }
}

function syncTurn(task: RuntimeTask): void {
  const conversation = conversationById(task.conversationId)
  if (!conversation) return
  let turn = conversation.turns.find((item) => item.task_id === task.id)
  if (!turn) {
    turn = {
      turn_id: task.turnId,
      conversation_id: task.conversationId,
      task_id: task.id,
      user_message: task.message,
      assistant_message: '',
      status: task.status,
      created_at: task.createdAt,
      updated_at: task.updatedAt,
    }
    conversation.turns.push(turn)
  }
  turn.status = task.status
  turn.updated_at = task.updatedAt
  if (task.response) turn.assistant_message = task.response
}

function applyResponse(task: RuntimeTask, response: TaskCreateResponse): void {
  task.status = response.status
  task.response = response.response || task.response
  task.plan = response.plan ?? task.plan
  task.confirmation = response.confirmation
  task.turnId = response.turn_id || task.turnId
  task.thinking = response.thinking ?? task.thinking
  task.updatedAt = new Date().toISOString()
  syncTurn(task)
}

function applyRecord(task: RuntimeTask, record: TaskRecord): void {
  task.status = record.status
  task.usage = { ...record.usage }
  task.loopCount = record.loop_count
  task.recoveryCount = record.recovery_count
  task.updatedAt = record.updated_at
  if (record.result && 'status' in record.result) {
    applyResponse(task, record.result)
  } else if (record.result && 'error' in record.result) {
    task.error = record.result.error ?? '任务执行失败'
  }
  syncTurn(task)
}

async function refreshConversation(conversationId: string): Promise<void> {
  const conversation = conversationById(conversationId)
  if (!conversation) return
  try {
    const response = await agentV2Api.getConversationTurns(conversationId)
    conversation.turns = response.turns
    conversation.turn_count = response.turns.length
    conversation.updated_at = response.turns.at(-1)?.updated_at ?? conversation.updated_at
  } catch {
    // A task can remain inspectable briefly while its conversation is being removed.
  }
}

async function refreshTask(taskId: string): Promise<void> {
  const task = taskById(taskId)
  if (!task) return
  try {
    applyRecord(task, await agentV2Api.getTask(taskId))
    if (!ACTIVE_STATUSES.has(task.status)) {
      stopTransport(taskId)
      await refreshConversation(task.conversationId)
    }
  } catch (error) {
    task.error = error instanceof Error ? error.message : String(error)
  }
}

function addEvent(task: RuntimeTask, event: TraceEvent, refreshRecord = true): void {
  if (task.events.some((current) => current.sequence === event.sequence)) return
  task.events.push(event)
  task.events.sort((a, b) => a.sequence - b.sequence)
  task.updatedAt = event.timestamp

  if (event.type === 'task.waiting_confirmation') {
    task.status = 'waiting_confirmation'
    task.confirmation = (event.data.confirmation ?? null) as RuntimeTask['confirmation']
  } else if (event.type === 'task.failed') {
    task.status = 'failed'
    task.error = String(event.data.error ?? '任务执行失败')
  } else if (event.type.startsWith('task.') && !['task.started', 'task.waiting_confirmation'].includes(event.type)) {
    task.status = event.type.slice(5)
    task.response = String(event.data.response ?? task.response)
  }
  syncTurn(task)

  const shouldRefreshRecord = refreshRecord && (
    event.type === 'model.request.completed'
    || event.type === 'agent.tool.completed'
    || event.type === 'agent.loop.completed'
    || event.type.startsWith('task.')
  )
  if (shouldRefreshRecord) {
    void refreshTask(task.id)
  }
}

function startPolling(taskId: string): void {
  if (pollers.has(taskId)) return
  const task = taskById(taskId)
  if (!task || !ACTIVE_STATUSES.has(task.status)) return
  task.streamState = 'polling'
  const poll = async () => {
    const current = taskById(taskId)
    if (!current) return
    const after = current.events.at(-1)?.sequence ?? 0
    try {
      const response = await agentV2Api.getEvents(taskId, after)
      response.events.forEach((event) => addEvent(current, event))
      await refreshTask(taskId)
    } catch (error) {
      current.error = error instanceof Error ? error.message : String(error)
    }
  }
  void poll()
  pollers.set(taskId, setInterval(poll, 1200))
}

function startTrace(taskId: string): void {
  const task = taskById(taskId)
  if (!task) return
  subscriptions.get(taskId)?.close()
  task.streamState = 'connecting'
  const cursor = task.events.at(-1)?.sequence ?? 0
  subscriptions.set(taskId, agentV2Api.subscribe(taskId, cursor, {
    onOpen: () => {
      task.streamState = 'live'
      const poller = pollers.get(taskId)
      if (poller) clearInterval(poller)
      pollers.delete(taskId)
    },
    onEvent: (event) => addEvent(task, event),
    onComplete: () => {
      task.streamState = 'closed'
      void refreshTask(taskId)
    },
    onError: () => startPolling(taskId),
  }))
}

function stopTransport(taskId: string): void {
  subscriptions.get(taskId)?.close()
  subscriptions.delete(taskId)
  const poller = pollers.get(taskId)
  if (poller) clearInterval(poller)
  pollers.delete(taskId)
  const task = taskById(taskId)
  if (task) task.streamState = 'closed'
}

async function restoreTask(stored: StoredTask): Promise<void> {
  if (taskById(stored.id) || restoringTaskIds.has(stored.id)) return
  restoringTaskIds.add(stored.id)
  const conversationId = stored.conversationId || stored.sessionId
  if (!conversationById(conversationId)) {
    restoringTaskIds.delete(stored.id)
    return
  }
  const task: RuntimeTask = {
    id: stored.id,
    turnId: stored.turnId ?? '',
    conversationId,
    sessionId: stored.sessionId,
    message: stored.message,
    role: stored.role,
    thinking: stored.thinking ?? false,
    status: 'received',
    response: '',
    plan: null,
    confirmation: null,
    events: [],
    usage: { ...EMPTY_USAGE },
    loopCount: 0,
    recoveryCount: 0,
    createdAt: stored.createdAt,
    updatedAt: stored.createdAt,
    error: '',
    streamState: 'idle',
  }
  tasks.push(task)
  const runtimeTask = taskById(stored.id)
  if (!runtimeTask) {
    restoringTaskIds.delete(stored.id)
    return
  }
  try {
    applyRecord(runtimeTask, await agentV2Api.getTask(runtimeTask.id))
    const replay = await agentV2Api.getEvents(runtimeTask.id, 0)
    replay.events.forEach((event) => addEvent(runtimeTask, event, false))
    if (ACTIVE_STATUSES.has(runtimeTask.status)) startTrace(runtimeTask.id)
  } catch {
    const index = tasks.findIndex((item) => item.id === stored.id)
    if (index >= 0) tasks.splice(index, 1)
  } finally {
    restoringTaskIds.delete(stored.id)
  }
}

async function restoreConversationTasks(conversation: RuntimeConversation): Promise<void> {
  await Promise.all(conversation.turns.map((turn) => restoreTask({
    id: turn.task_id,
    turnId: turn.turn_id,
    conversationId: conversation.conversation_id,
    sessionId: conversation.conversation_id,
    message: turn.user_message,
    role: conversation.role,
    createdAt: turn.created_at,
  })))
}

async function hydrateRuntime(): Promise<void> {
  if (hydrated) return
  hydrated = true
  const response = await agentV2Api.listConversations(userId.value)
  conversations.splice(0, conversations.length, ...response.conversations.map((item) => ({
    ...item,
    turns: [],
    loading: false,
  })))
  await Promise.all(readStoredTasks().map(restoreTask))
  const storedConversation = localStorage.getItem(`${ACTIVE_CONVERSATION_KEY}.${userId.value}`)
  activeConversationId.value = conversationById(storedConversation ?? '')?.conversation_id
    ?? conversations[0]?.conversation_id
    ?? null
  if (activeConversationId.value) {
    await selectConversation(activeConversationId.value)
  }
  persistState()
}

async function ensureConversation(): Promise<RuntimeConversation> {
  const current = conversationById(activeConversationId.value ?? '')
  if (current) return current
  const created = await agentV2Api.createConversation(role.value, userId.value)
  const conversation: RuntimeConversation = { ...created, turns: [], loading: false }
  conversations.unshift(conversation)
  activeConversationId.value = conversation.conversation_id
  return conversation
}

async function submit(message: string): Promise<RuntimeTask> {
  const conversation = await ensureConversation()
  const response = await agentV2Api.createTask(
    message,
    conversation.conversation_id,
    role.value,
    thinking.value && thinkingSupported.value,
    userId.value,
  )
  if (!response.task_id) throw new Error('后端未返回 task_id')
  const now = new Date().toISOString()
  const task: RuntimeTask = {
    id: response.task_id,
    turnId: response.turn_id ?? '',
    conversationId: response.conversation_id,
    sessionId: response.session_id,
    message,
    role: role.value,
    thinking: response.thinking,
    status: response.status,
    response: response.response,
    plan: response.plan,
    confirmation: response.confirmation,
    events: [],
    usage: { ...EMPTY_USAGE },
    loopCount: 0,
    recoveryCount: 0,
    createdAt: now,
    updatedAt: now,
    error: '',
    streamState: 'idle',
  }
  tasks.unshift(task)
  activeTaskId.value = task.id
  if (conversation.title === '新会话') conversation.title = message.trim().slice(0, 36)
  conversation.updated_at = now
  conversation.turn_count += 1
  syncTurn(task)
  conversations.sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  startTrace(task.id)
  return task
}

async function selectConversation(conversationId: string): Promise<void> {
  activeConversationId.value = conversationId
  const conversation = conversationById(conversationId)
  if (!conversation) return
  conversation.loading = true
  await refreshConversation(conversationId)
  await restoreConversationTasks(conversation)
  conversation.loading = false
  activeTaskId.value = tasks
    .filter((task) => task.conversationId === conversationId)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]?.id ?? null
}

function newConversation(): void {
  activeConversationId.value = null
  activeTaskId.value = null
}

async function deleteConversation(conversationId: string): Promise<void> {
  await agentV2Api.deleteConversation(conversationId)
  const index = conversations.findIndex((item) => item.conversation_id === conversationId)
  if (index >= 0) conversations.splice(index, 1)
  tasks.filter((task) => task.conversationId === conversationId).forEach((task) => {
    stopTransport(task.id)
    const taskIndex = tasks.indexOf(task)
    if (taskIndex >= 0) tasks.splice(taskIndex, 1)
  })
  if (activeConversationId.value === conversationId) {
    const next = conversations[0]?.conversation_id ?? null
    activeConversationId.value = next
    activeTaskId.value = null
    if (next) await selectConversation(next)
  }
}

async function confirm(taskId: string, approved: boolean): Promise<void> {
  const task = taskById(taskId)
  if (!task) return
  task.status = 'running'
  task.confirmation = null
  try {
    applyResponse(task, await agentV2Api.confirmTask(taskId, approved))
    if (ACTIVE_STATUSES.has(task.status)) startTrace(taskId)
    else await refreshTask(taskId)
  } catch (error) {
    task.error = error instanceof Error ? error.message : String(error)
    await refreshTask(taskId)
  }
}

async function checkBackend(): Promise<void> {
  backendState.value = 'checking'
  try {
    await agentV2Api.health()
    const capabilities = await agentV2Api.capabilities()
    thinkingSupported.value = capabilities.thinking.supported
    if (!thinkingSupported.value) thinking.value = false
    backendState.value = 'online'
  } catch {
    backendState.value = 'offline'
  }
}

function resetRuntimeState(): void {
  subscriptions.forEach((subscription) => subscription.close())
  subscriptions.clear()
  pollers.forEach((poller) => clearInterval(poller))
  pollers.clear()
  restoringTaskIds.clear()
  tasks.splice(0)
  conversations.splice(0)
  activeConversationId.value = null
  activeTaskId.value = null
  hydrated = false
}

async function switchIdentity(nextUserId: string, nextRole: AgentRole): Promise<void> {
  if (hydrated && userId.value === nextUserId && role.value === nextRole) return
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
  const activeTask = computed(() => taskById(activeTaskId.value ?? '') ?? null)

  watch([tasks, activeConversationId], persistState, { deep: true })
  watch(thinking, (value) => localStorage.setItem('wotagent-v2-console.thinking', String(value)))

  onBeforeUnmount(() => {
    subscriptions.forEach((subscription) => subscription.close())
    pollers.forEach((poller) => clearInterval(poller))
  })

  return {
    tasks,
    conversations,
    activeConversationId,
    activeConversation,
    activeTaskId,
    activeTask,
    role,
    userId,
    thinking,
    thinkingSupported,
    backendState,
    submit,
    confirm,
    selectConversation,
    newConversation,
    deleteConversation,
    refreshTask,
    checkBackend,
    hydrateRuntime,
    switchIdentity,
    clearIdentity,
  }
}
