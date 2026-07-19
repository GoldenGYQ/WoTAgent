import { defineComponent, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { agentV2Api } from '../api/v2'
import type { RunRecord, TraceEvent } from '../types/agent'
import { useAgentRuntime } from './useAgentRuntime'

const run: RunRecord = {
  run_id: 'run-refresh',
  turn_id: 'turn-refresh',
  conversation_id: 'conv-refresh',
  user_id: 'JH-1',
  role: 'operator',
  source: 'user',
  domain_id: null,
  source_event_id: null,
  source_context: {},
  root_goal: '查看历史运行链路',
  status: 'completed',
  execution_strategy: 'sequential',
  success_criteria: [],
  task_ids: [],
  final_response: '已完成',
  thinking: false,
  created_at: '2026-07-18T10:00:00Z',
  updated_at: '2026-07-18T10:00:03Z',
}

const events: TraceEvent[] = [
  {
    event_id: 'run-refresh:1',
    sequence: 1,
    timestamp: '2026-07-18T10:00:00Z',
    run_id: 'run-refresh',
    turn_id: 'turn-refresh',
    task_id: null,
    parent_task_id: null,
    agent_run_id: null,
    iteration: 0,
    tool_call_id: null,
    type: 'run.started',
    node: 'run_coordinator',
    data: {},
  },
  {
    event_id: 'run-refresh:2',
    sequence: 2,
    timestamp: '2026-07-18T10:00:03Z',
    run_id: 'run-refresh',
    turn_id: 'turn-refresh',
    task_id: null,
    parent_task_id: null,
    agent_run_id: null,
    iteration: 0,
    tool_call_id: null,
    type: 'run.completed',
    node: 'run_coordinator',
    data: { response: '已完成' },
  },
]

describe('useAgentRuntime refresh recovery', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
    useAgentRuntime().clearIdentity()
  })

  it('deduplicates cached runs and replays persisted events after refresh', async () => {
    localStorage.setItem('wotagent-v2-console.runs.JH-1', JSON.stringify([
      {
        id: run.run_id,
        turnId: run.turn_id,
        conversationId: run.conversation_id,
        message: run.root_goal,
        role: 'operator',
        thinking: false,
        createdAt: run.created_at,
      },
      {
        id: run.run_id,
        turnId: run.turn_id,
        conversationId: run.conversation_id,
        message: run.root_goal,
        role: 'operator',
        thinking: false,
        createdAt: run.created_at,
      },
    ]))
    localStorage.setItem(
      'wotagent-v2-console.active-conversation.JH-1',
      run.conversation_id,
    )
    vi.spyOn(agentV2Api, 'listConversations').mockResolvedValue({
      conversations: [{
        conversation_id: run.conversation_id,
        user_id: run.user_id,
        role: 'operator',
        title: '刷新恢复',
        created_at: run.created_at,
        updated_at: run.updated_at,
        turn_count: 1,
      }],
    })
    vi.spyOn(agentV2Api, 'getConversationTurns').mockResolvedValue({
      conversation_id: run.conversation_id,
      turns: [{
        turn_id: run.turn_id,
        conversation_id: run.conversation_id,
        run_id: run.run_id,
        user_message: run.root_goal,
        assistant_message: run.final_response,
        status: run.status,
        created_at: run.created_at,
        updated_at: run.updated_at,
      }],
    })
    vi.spyOn(agentV2Api, 'getRun').mockResolvedValue(run)
    vi.spyOn(agentV2Api, 'getRunTasks').mockResolvedValue({ run_id: run.run_id, tasks: [] })
    vi.spyOn(agentV2Api, 'getEvents').mockResolvedValue({
      run_id: run.run_id,
      events,
      next_cursor: 2,
    })

    let runtime!: ReturnType<typeof useAgentRuntime>
    const wrapper = mount(defineComponent({
      setup() {
        runtime = useAgentRuntime()
        return () => null
      },
    }))

    await runtime.switchIdentity('JH-1', 'operator')
    await nextTick()

    expect(runtime.runs).toHaveLength(1)
    expect(runtime.runs[0].events.map((event) => event.sequence)).toEqual([1, 2])
    expect(runtime.runs[0].streamState).toBe('closed')
    expect(agentV2Api.getEvents).toHaveBeenCalledWith(run.run_id, 0)
    expect(runtime.activeConversationId.value).toBeNull()
    expect(runtime.activeRunId.value).toBeNull()
    expect(localStorage.getItem(
      'wotagent-v2-console.active-conversation.JH-1',
    )).toBeNull()

    wrapper.unmount()
  })
})
