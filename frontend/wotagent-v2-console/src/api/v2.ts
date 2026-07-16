import type {
  AgentRole,
  ApiCapabilities,
  ConversationRecord,
  ConversationTurn,
  TaskCreateResponse,
  TaskRecord,
  TraceEvent,
  Resident,
  HealthSummary,
  RiskEvent,
  CareAction,
  EldercareSimulationResult,
  SensorType,
  EldercareDevice,
} from '../types/agent'

export interface TraceSubscription {
  close: () => void
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(String(body.detail ?? body.error ?? `HTTP ${response.status}`))
  }
  return response.json() as Promise<T>
}

function websocketUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}

export const agentV2Api = {
  health(): Promise<{ status: string; pipeline: string }> {
    return request('/api/health')
  },

  capabilities(): Promise<ApiCapabilities> {
    return request('/api/v2/capabilities')
  },

  createConversation(role: AgentRole, userId = 'local-user'): Promise<ConversationRecord> {
    return request('/api/v2/conversations', {
      method: 'POST',
      body: JSON.stringify({ role, user_id: userId }),
    })
  },

  listConversations(userId = 'local-user'): Promise<{ conversations: ConversationRecord[] }> {
    return request(`/api/v2/conversations?user_id=${encodeURIComponent(userId)}`)
  },

  getConversationTurns(conversationId: string): Promise<{ conversation_id: string; turns: ConversationTurn[] }> {
    return request(`/api/v2/conversations/${encodeURIComponent(conversationId)}/turns`)
  },

  deleteConversation(conversationId: string): Promise<{ status: string; conversation_id: string }> {
    return request(`/api/v2/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })
  },

  createTask(message: string, conversationId: string, role: AgentRole, thinking: boolean, userId = 'local-user'): Promise<TaskCreateResponse> {
    return request('/api/v2/tasks', {
      method: 'POST',
      body: JSON.stringify({ message, conversation_id: conversationId, role, thinking, user_id: userId }),
    })
  },

  getTask(taskId: string): Promise<TaskRecord> {
    return request(`/api/v2/tasks/${encodeURIComponent(taskId)}`)
  },

  getEvents(taskId: string, after = 0): Promise<{ events: TraceEvent[]; next_cursor: number }> {
    return request(`/api/v2/tasks/${encodeURIComponent(taskId)}/events?after=${after}`)
  },

  confirmTask(taskId: string, approved: boolean): Promise<TaskCreateResponse> {
    return request(`/api/v2/tasks/${encodeURIComponent(taskId)}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ approved }),
    })
  },

  listResidents(): Promise<{ residents: Resident[] }> {
    return request('/api/v2/eldercare/residents')
  },

  getHealthSummary(residentId: string): Promise<HealthSummary> {
    return request(`/api/v2/eldercare/residents/${encodeURIComponent(residentId)}/summary`)
  },

  ingestReading(payload: { resident_id: string; sensor_type: SensorType; value: number | boolean | string; unit?: string; source?: string }): Promise<{ events: RiskEvent[]; tasks: Array<{ task_id: string; status: string }> }> {
    return request('/api/v2/eldercare/readings', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  simulateEldercare(residentId: string, scenario: string): Promise<EldercareSimulationResult> {
    return request('/api/v2/eldercare/simulate', {
      method: 'POST',
      body: JSON.stringify({ resident_id: residentId, scenario }),
    })
  },

  listRiskEvents(residentId?: string): Promise<{ events: RiskEvent[] }> {
    const query = residentId ? `?resident_id=${encodeURIComponent(residentId)}` : ''
    return request(`/api/v2/eldercare/events${query}`)
  },

  listCareActions(residentId?: string): Promise<{ actions: CareAction[] }> {
    const query = residentId ? `?resident_id=${encodeURIComponent(residentId)}` : ''
    return request(`/api/v2/eldercare/actions${query}`)
  },

  listEldercareDevices(residentId: string): Promise<{ resident_id: string; devices: EldercareDevice[] }> {
    return request(`/api/v2/eldercare/residents/${encodeURIComponent(residentId)}/devices`)
  },

  acknowledgeRisk(eventId: string): Promise<RiskEvent> {
    return request(`/api/v2/eldercare/events/${encodeURIComponent(eventId)}/acknowledge`, {
      method: 'POST',
    })
  },

  subscribe(
    taskId: string,
    after: number,
    handlers: {
      onEvent: (event: TraceEvent) => void
      onComplete: (status: string) => void
      onOpen: () => void
      onError: () => void
    },
  ): TraceSubscription {
    const socket = new WebSocket(websocketUrl('/ws/v2'))
    let closedByClient = false

    socket.onopen = () => {
      socket.send(JSON.stringify({ task_id: taskId, after }))
      handlers.onOpen()
    }
    socket.onmessage = (message) => {
      const payload = JSON.parse(message.data) as TraceEvent | { type: string; status?: string }
      if (payload.type === 'stream.completed') {
        handlers.onComplete(('status' in payload ? payload.status : undefined) ?? 'completed')
      } else if (payload.type === 'error') {
        handlers.onError()
      } else {
        handlers.onEvent(payload as TraceEvent)
      }
    }
    socket.onerror = () => handlers.onError()
    socket.onclose = () => {
      if (!closedByClient) handlers.onError()
    }

    return {
      close() {
        closedByClient = true
        socket.close()
      },
    }
  },
}
