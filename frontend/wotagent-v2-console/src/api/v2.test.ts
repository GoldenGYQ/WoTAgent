import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentV2Api } from './v2'
import { EMPTY_USAGE } from '../types/agent'

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
        usage: { ...EMPTY_USAGE },
        supervisor_iterations: 0,
        agent_loops: 0,
        recoveries: 0,
        duration_ms: null,
        finished_at: null,
        created_at: '',
        updated_at: '',
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await agentV2Api.createRun('打开客厅灯', 'conv-1', 'operator', true, 'local-user', undefined, {
      phoneMode: true,
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v2/runs')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      message: '打开客厅灯',
      conversation_id: 'conv-1',
      role: 'operator',
      thinking: true,
      phone_mode: true,
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

  it('uploads recorded audio without forcing a JSON content type', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        text: '打开 living room 的灯',
        language: 'zh',
        language_probability: 0.96,
        duration_seconds: 1.8,
        processing_ms: 42,
        model: 'base',
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await agentV2Api.transcribeSpeech(
      new Blob(['audio'], { type: 'audio/webm' }),
    )

    expect(result.text).toBe('打开 living room 的灯')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v2/speech/transcriptions')
    expect(init.body).toBeInstanceOf(FormData)
    expect(init.headers).not.toHaveProperty('Content-Type')
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

  it('loads domain-specific human principal contexts', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ domain_id: 'smart_home', principals: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await agentV2Api.listPrincipals('smart_home')

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/v2/principals?domain_id=smart_home&login_only=true',
    )
  })

  it('registers from a role template without sending raw capabilities', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        token: 'session-token',
        login_mode: 'pin',
        principal: { principal_id: 'USR-1' },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await agentV2Api.registerPrincipal({
      display_name: '新用户',
      pin: '2468',
      domain_id: 'smart_home',
      role_template_id: 'villa_family_member',
      target_id: 'villa-demo',
    })

    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body).toEqual({
      display_name: '新用户',
      pin: '2468',
      domain_id: 'smart_home',
      role_template_id: 'villa_family_member',
      target_id: 'villa-demo',
    })
    expect(body).not.toHaveProperty('capabilities')
  })

  it('logs in password users with login_name and password', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        token: 'session-token',
        login_mode: 'password',
        principal: { principal_id: 'ADMIN-1' },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await agentV2Api.loginPrincipal({
      loginName: 'admin',
      domainId: 'eldercare',
      password: 'Admin@123',
    })

    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body).toEqual({
      login_name: 'admin',
      domain_id: 'eldercare',
      password: 'Admin@123',
    })
  })

  it('requests managed users for the current domain', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ domain_id: 'eldercare', users: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await agentV2Api.listManagedUsers('eldercare')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v2/principals/users?domain_id=eldercare')
  })

  it('attaches the principal session token to later API requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ conversations: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)
    agentV2Api.setSessionToken('session-token')

    await agentV2Api.listConversations('USR-1')

    expect(fetchMock.mock.calls[0][1].headers).toMatchObject({
      Authorization: 'Bearer session-token',
    })
    agentV2Api.setSessionToken(null)
  })
})
