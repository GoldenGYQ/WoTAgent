import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import InlineTraceTimeline from './InlineTraceTimeline.vue'
import type { RuntimeTask, TraceEvent } from '../types/agent'
import { EMPTY_USAGE } from '../types/agent'

function traceEvent(sequence: number, type: string, data: Record<string, unknown> = {}): TraceEvent {
  return {
    event_id: `event-${sequence}`,
    sequence,
    timestamp: `2026-07-16T08:00:0${sequence}Z`,
    trace_id: 'task-1',
    task_id: 'task-1',
    session_id: 'conv-1',
    type,
    node: type === 'agent.goal.created' ? 'butler_goal_planner' : 'health_agent',
    iteration: sequence,
    duration_ms: 12,
    data,
  }
}

const task: RuntimeTask = {
  id: 'task-1',
  turnId: 'turn-1',
  conversationId: 'conv-1',
  sessionId: 'conv-1',
  message: '查看张爷爷今天的健康状态',
  role: 'operator',
  thinking: false,
  status: 'running',
  response: '',
  plan: null,
  confirmation: null,
  events: [
    traceEvent(3, 'agent.finding.completed', {
      finding: { finding: '健康数据已完成核验' },
    }),
    traceEvent(1, 'agent.goal.created', {
      goal_plan: { goal: '汇总并核验张爷爷今日健康状态' },
    }),
    traceEvent(2, 'agent.health.decision', {
      action: 'call_tool',
      tool: 'get_latest_vitals',
    }),
  ],
  usage: { ...EMPTY_USAGE },
  loopCount: 1,
  recoveryCount: 0,
  createdAt: '2026-07-16T08:00:00Z',
  updatedAt: '2026-07-16T08:00:03Z',
  error: '',
  streamState: 'live',
}

describe('InlineTraceTimeline', () => {
  it('renders trace events by sequence and expands payload in place', async () => {
    const wrapper = mount(InlineTraceTimeline, {
      props: { task, selected: true },
    })

    const titles = wrapper.findAll('.inline-trace-title strong').map((item) => item.text())
    expect(titles).toEqual([
      'Butler 建立目标',
      'Health Agent 决策',
      'Health Agent提交发现',
    ])

    const events = wrapper.findAll('.inline-trace-event')
    await events[1].trigger('click')
    expect(events[1].find('.inline-trace-payload').text()).toContain('get_latest_vitals')
  })

  it('collapses a completed trace without removing it from the conversation', async () => {
    const wrapper = mount(InlineTraceTimeline, {
      props: {
        task: { ...task, status: 'succeeded', streamState: 'closed' },
        selected: false,
      },
    })

    expect(wrapper.find('.inline-trace-body').exists()).toBe(false)
    await wrapper.find('.inline-trace-header').trigger('click')
    expect(wrapper.find('.inline-trace-body').exists()).toBe(true)
  })
})
