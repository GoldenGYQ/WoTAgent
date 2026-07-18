import type {
  AgentRole,
  AgentRunRecord,
  AgentTaskRecord,
  ApiCapabilities,
  CareAction,
  ConversationRecord,
  ConversationTurn,
  EldercareDevice,
  EldercareSimulationResult,
  DomainAction,
  DomainEvent,
  DomainManifest,
  DomainOverview,
  DomainResource,
  DomainSubject,
  HealthSummary,
  MonitorStatus,
  Resident,
  RiskEvent,
  RunRecord,
  SensorType,
  TraceEvent,
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
  health: (): Promise<{ status: string; pipeline: string }> => request('/api/health'),
  capabilities: (): Promise<ApiCapabilities> => request('/api/v2/capabilities'),

  listDomains: (): Promise<{ domains: DomainManifest[] }> => request('/api/v2/domains'),
  listDomainSubjects(domainId: string, principalId: string): Promise<{ domain_id: string; subjects: DomainSubject[] }> {
    return request(`/api/v2/domains/${encodeURIComponent(domainId)}/subjects?principal_id=${encodeURIComponent(principalId)}`)
  },
  getDomainOverview(domainId: string, subjectId: string, principalId: string): Promise<DomainOverview> {
    return request(`/api/v2/domains/${encodeURIComponent(domainId)}/subjects/${encodeURIComponent(subjectId)}/overview?principal_id=${encodeURIComponent(principalId)}`)
  },
  listDomainResources(domainId: string, subjectId: string, principalId: string): Promise<{ resources: DomainResource[] }> {
    return request(`/api/v2/domains/${encodeURIComponent(domainId)}/subjects/${encodeURIComponent(subjectId)}/resources?principal_id=${encodeURIComponent(principalId)}`)
  },
  listDomainEvents(domainId: string, subjectId: string, principalId: string): Promise<{ events: DomainEvent[] }> {
    return request(`/api/v2/domains/${encodeURIComponent(domainId)}/subjects/${encodeURIComponent(subjectId)}/events?principal_id=${encodeURIComponent(principalId)}`)
  },
  listDomainActions(domainId: string, subjectId: string, principalId: string): Promise<{ actions: DomainAction[] }> {
    return request(`/api/v2/domains/${encodeURIComponent(domainId)}/subjects/${encodeURIComponent(subjectId)}/actions?principal_id=${encodeURIComponent(principalId)}`)
  },
  getDomainMonitorStatus(domainId: string): Promise<MonitorStatus> {
    return request(`/api/v2/domains/${encodeURIComponent(domainId)}/monitor/status`)
  },
  ingestDomainTelemetry(domainId: string, payload: { subject_id: string; signal: string; value: number | boolean | string; unit: string; source: string; principal_id: string }): Promise<Record<string, unknown>> {
    return request(`/api/v2/domains/${encodeURIComponent(domainId)}/telemetry`, { method: 'POST', body: JSON.stringify(payload) })
  },
  simulateDomainScenario(domainId: string, payload: { subject_id: string; scenario: string; principal_id: string }): Promise<Record<string, unknown>> {
    return request(`/api/v2/domains/${encodeURIComponent(domainId)}/scenarios`, { method: 'POST', body: JSON.stringify(payload) })
  },
  acknowledgeDomainEvent(domainId: string, eventId: string, principalId: string): Promise<Record<string, unknown>> {
    return request(`/api/v2/domains/${encodeURIComponent(domainId)}/events/${encodeURIComponent(eventId)}/acknowledge`, { method: 'POST', body: JSON.stringify({ principal_id: principalId }) })
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

  createRun(message: string, conversationId: string, role: AgentRole, thinking: boolean, userId = 'local-user'): Promise<RunRecord> {
    return request('/api/v2/runs', {
      method: 'POST',
      body: JSON.stringify({ message, conversation_id: conversationId, role, thinking, user_id: userId }),
    })
  },
  getRun(runId: string): Promise<RunRecord> {
    return request(`/api/v2/runs/${encodeURIComponent(runId)}`)
  },
  getRunTasks(runId: string): Promise<{ run_id: string; tasks: AgentTaskRecord[] }> {
    return request(`/api/v2/runs/${encodeURIComponent(runId)}/tasks`)
  },
  getTaskAgentRuns(runId: string, taskId: string): Promise<{ run_id: string; task_id: string; agent_runs: AgentRunRecord[] }> {
    return request(`/api/v2/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/agent-runs`)
  },
  confirmTask(runId: string, taskId: string, approved: boolean): Promise<RunRecord> {
    return request(`/api/v2/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ approved }),
    })
  },
  getEvents(runId: string, after = 0): Promise<{ run_id: string; events: TraceEvent[]; next_cursor: number }> {
    return request(`/api/v2/runs/${encodeURIComponent(runId)}/events?after=${after}`)
  },

  listResidents: (): Promise<{ residents: Resident[] }> => request('/api/v2/eldercare/residents'),
  getHealthSummary: (residentId: string): Promise<HealthSummary> => request(`/api/v2/eldercare/residents/${encodeURIComponent(residentId)}/summary`),
  ingestReading(payload: { resident_id: string; sensor_type: SensorType; value: number | boolean | string; unit?: string; source?: string }): Promise<{ events: RiskEvent[]; tasks: Array<{ task_id: string; status: string }> }> {
    return request('/api/v2/eldercare/readings', { method: 'POST', body: JSON.stringify(payload) })
  },
  simulateEldercare(residentId: string, scenario: string): Promise<EldercareSimulationResult> {
    return request('/api/v2/eldercare/simulate', { method: 'POST', body: JSON.stringify({ resident_id: residentId, scenario }) })
  },
  listRiskEvents(residentId?: string): Promise<{ events: RiskEvent[] }> {
    return request(`/api/v2/eldercare/events${residentId ? `?resident_id=${encodeURIComponent(residentId)}` : ''}`)
  },
  listCareActions(residentId?: string): Promise<{ actions: CareAction[] }> {
    return request(`/api/v2/eldercare/actions${residentId ? `?resident_id=${encodeURIComponent(residentId)}` : ''}`)
  },
  listEldercareDevices: (residentId: string): Promise<{ resident_id: string; devices: EldercareDevice[] }> => request(`/api/v2/eldercare/residents/${encodeURIComponent(residentId)}/devices`),
  acknowledgeRisk: (eventId: string): Promise<RiskEvent> => request(`/api/v2/eldercare/events/${encodeURIComponent(eventId)}/acknowledge`, { method: 'POST' }),

  subscribe(
    runId: string,
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
      socket.send(JSON.stringify({ run_id: runId, after }))
      handlers.onOpen()
    }
    socket.onmessage = (message) => {
      const payload = JSON.parse(message.data) as TraceEvent | { type: string; status?: string }
      if (payload.type === 'stream.completed') handlers.onComplete(('status' in payload ? payload.status : undefined) ?? 'completed')
      else if (payload.type === 'error') handlers.onError()
      else handlers.onEvent(payload as TraceEvent)
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
