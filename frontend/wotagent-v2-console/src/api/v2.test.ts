import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentV2Api } from './v2'

afterEach(() => vi.unstubAllGlobals())

describe('agentV2Api', () => {
  it('submits only to the Agent 2.0 task endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        task_id: 'task-1',
        turn_id: 'turn-1',
        conversation_id: 'conv-1',
        session_id: 'conv-1',
        status: 'accepted',
        response: '',
        plan: null,
        trace: null,
        confirmation: null,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await agentV2Api.createTask('打开客厅灯', 'conv-1', 'operator')

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v2/tasks')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      message: '打开客厅灯',
      conversation_id: 'conv-1',
      role: 'operator',
    })
  })

  it('loads durable conversation turns from the v2 conversation endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ conversation_id: 'conv-1', turns: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await agentV2Api.getConversationTurns('conv-1')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v2/conversations/conv-1/turns')
  })
})
