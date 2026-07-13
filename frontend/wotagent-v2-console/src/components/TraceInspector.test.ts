import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TraceInspector from './TraceInspector.vue'
import { EMPTY_USAGE, type RuntimeTask } from '../types/agent'

const task: RuntimeTask = {
  id: 'task-1',
  turnId: 'turn-1',
  conversationId: 'conv-1',
  sessionId: 'session-1',
  message: '打开客厅灯',
  role: 'operator',
  thinking: true,
  status: 'running',
  response: '',
  plan: {
    plan_id: 'plan-1',
    intent: 'control',
    summary: '打开客厅灯',
    assumptions: [],
    steps: [{
      id: 's1',
      kind: 'action',
      device_id: 'light-001',
      action: 'turnOn',
      input: {},
      depends_on: [],
      postconditions: [{ property: 'on', operator: 'eq', value: true }],
    }],
  },
  confirmation: null,
  events: [{
    event_id: 'task-1:1',
    sequence: 1,
    timestamp: '2026-07-13T08:00:00Z',
    trace_id: 'task-1',
    task_id: 'task-1',
    session_id: 'session-1',
    type: 'agent.tool.completed',
    node: 'executor',
    iteration: 1,
    duration_ms: 20,
    data: { tool: 'light_001__action__turn_on', output: { success: true } },
  }],
  usage: { ...EMPTY_USAGE },
  loopCount: 0,
  recoveryCount: 0,
  createdAt: '2026-07-13T08:00:00Z',
  updatedAt: '2026-07-13T08:00:00Z',
  error: '',
  streamState: 'live',
}

describe('TraceInspector', () => {
  it('renders and expands ordered trace payloads', async () => {
    const wrapper = mount(TraceInspector, { props: { task } })
    expect(wrapper.text()).toContain('工具执行')
    expect(wrapper.text()).toContain('light_001__action__turn_on')

    await wrapper.get('.trace-event').trigger('click')
    expect(wrapper.get('.trace-payload').text()).toContain('success')
  })

  it('shows plan steps in the plan tab', async () => {
    const wrapper = mount(TraceInspector, { props: { task } })
    await wrapper.get('.segmented-tabs button:nth-child(2)').trigger('click')
    expect(wrapper.text()).toContain('turnOn')
    expect(wrapper.text()).toContain('light-001')
    expect(wrapper.text()).toContain('on eq true')
  })
})
