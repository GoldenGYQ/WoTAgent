import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentV2Api } from './v2'

afterEach(() => vi.unstubAllGlobals())

describe('agentV2Api', () => {
  it('loads server thinking capabilities', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        thinking: { supported: true, default: false, reasoning_effort: 'high', private_reasoning_exposed: false },
        streaming: true,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await agentV2Api.capabilities()

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v2/capabilities')
    expect(result.thinking.supported).toBe(true)
  })

  it('submits one user query as a Run', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        run_id: 'run-1',
        turn_id: 'turn-1',
        conversation_id: 'conv-1',
        root_goal: '打开客厅灯',
        status: 'accepted',
        execution_strategy: 'parallel_when_safe',
        success_criteria: [],
        task_ids: [],
        final_response: '',
        created_at: '',
        updated_at: '',
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await agentV2Api.createRun('打开客厅灯', 'conv-1', 'operator', true)

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v2/runs')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      message: '打开客厅灯',
      conversation_id: 'conv-1',
      role: 'operator',
      thinking: true,
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

  it('scopes conversation listing to the login account', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ conversations: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await agentV2Api.listConversations('JH-2')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v2/conversations?user_id=JH-2')
  })
})
