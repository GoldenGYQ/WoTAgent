import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { agentV2Api, type TraceSubscription } from '../api/v2'
import {
  ACTIVE_STATUSES,
  EMPTY_USAGE,
  type AgentRole,
  type RuntimeTask,
  type TaskCreateResponse,
  type TaskRecord,
  type TraceEvent,
} from '../types/agent'

const tasks = reactive<RuntimeTask[]>([])
const activeTaskId = ref<string | null>(null)
const role = ref<AgentRole>('operator')
const backendState = ref<'checking' | 'online' | 'offline'>('checking')
const subscriptions = new Map<string, TraceSubscription>()
const pollers = new Map<string, ReturnType<typeof setInterval>>()
const STORAGE_KEY = 'wotagent-v2-console.tasks'
const ACTIVE_TASK_KEY = 'wotagent-v2-console.active-task'
let hydrated = false

interface StoredTask {
  id: string
  sessionId: string
  message: string
  role: AgentRole
  createdAt: string
}

function readStoredTasks(): StoredTask[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.slice(0, 20) : []
  } catch {
    return []
  }
}

function persistTasks(): void {
  const snapshot: StoredTask[] = tasks.slice(0, 20).map((task) => ({
    id: task.id,
    sessionId: task.sessionId,
    message: task.message,
    role: task.role,
    createdAt: task.createdAt,
  }))
  localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot))
  if (activeTaskId.value) localStorage.setItem(ACTIVE_TASK_KEY, activeTaskId.value)
  else localStorage.removeItem(ACTIVE_TASK_KEY)
}

function newSessionId(): string {
  return `sess_ui_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`
}

function taskById(taskId: string): RuntimeTask | undefined {
  return tasks.find((task) => task.id === taskId)
}

function applyResponse(task: RuntimeTask, response: TaskCreateResponse): void {
  task.status = response.status
  task.response = response.response || task.response
  task.plan = response.plan ?? task.plan
  task.confirmation = response.confirmation
  task.updatedAt = new Date().toISOString()
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
}

async function refreshTask(taskId: string): Promise<void> {
  const task = taskById(taskId)
  if (!task) return
  try {
    applyRecord(task, await agentV2Api.getTask(taskId))
    if (!ACTIVE_STATUSES.has(task.status)) stopTransport(taskId)
  } catch (error) {
    task.error = error instanceof Error ? error.message : String(error)
  }
}

function addEvent(task: RuntimeTask, event: TraceEvent): void {
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

  if (
    event.type === 'model.request.completed' ||
    event.type === 'agent.tool.completed' ||
    event.type === 'agent.loop.completed' ||
    event.type.startsWith('task.')
  ) {
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
  const task: RuntimeTask = {
    id: stored.id,
    sessionId: stored.sessionId,
    message: stored.message,
    role: stored.role,
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
  try {
    applyRecord(task, await agentV2Api.getTask(task.id))
    const replay = await agentV2Api.getEvents(task.id, 0)
    replay.events.forEach((event) => addEvent(task, event))
    if (ACTIVE_STATUSES.has(task.status)) startTrace(task.id)
  } catch {
    tasks.splice(tasks.indexOf(task), 1)
  }
}

async function hydrateRuntime(): Promise<void> {
  if (hydrated) return
  hydrated = true
  const storedTasks = readStoredTasks()
  await Promise.all(storedTasks.map(restoreTask))
  tasks.sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  const storedActiveTask = localStorage.getItem(ACTIVE_TASK_KEY)
  activeTaskId.value = taskById(storedActiveTask ?? '')?.id ?? tasks[0]?.id ?? null
  persistTasks()
}

async function submit(message: string): Promise<RuntimeTask> {
  const sessionId = newSessionId()
  const response = await agentV2Api.createTask(message, sessionId, role.value)
  if (!response.task_id) throw new Error('后端未返回 task_id')
  const now = new Date().toISOString()
  const task: RuntimeTask = {
    id: response.task_id,
    sessionId: response.session_id,
    message,
    role: role.value,
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
  startTrace(task.id)
  return task
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
    backendState.value = 'online'
  } catch {
    backendState.value = 'offline'
  }
}

export function useAgentRuntime() {
  const activeTask = computed(() => taskById(activeTaskId.value ?? '') ?? null)

  watch([tasks, activeTaskId], persistTasks, { deep: true })

  onBeforeUnmount(() => {
    subscriptions.forEach((subscription) => subscription.close())
    pollers.forEach((poller) => clearInterval(poller))
  })

  return {
    tasks,
    activeTaskId,
    activeTask,
    role,
    backendState,
    submit,
    confirm,
    refreshTask,
    checkBackend,
    hydrateRuntime,
  }
}
