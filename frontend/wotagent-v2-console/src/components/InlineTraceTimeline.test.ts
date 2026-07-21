import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import InlineTraceTimeline from './InlineTraceTimeline.vue'
import { EMPTY_USAGE, type RuntimeRun, type TraceEvent } from '../types/agent'

function event(sequence: number, type: string): TraceEvent {
  return {
    event_id: `run-1:${sequence}`,
    sequence,
    timestamp: `2026-07-16T08:00:${String(sequence).padStart(2, '0')}Z`,
    run_id: 'run-1',
    turn_id: 'turn-1',
    task_id: sequence > 1 ? 'task-1' : null,
    parent_task_id: null,
    agent_run_id: sequence > 1 ? 'agent-run-1' : null,
    tool_call_id: null,
    type,
    node: 'environment_agent',
    iteration: 1,
    data: {},
  }
}

const run: RuntimeRun = {
  id: 'run-1',
  turnId: 'turn-1',
  conversationId: 'conv-1',
  message: '查询客厅灯',
  role: 'operator',
  thinking: false,
  status: 'running',
  response: '',
  executionStrategy: 'parallel_when_safe',
  successCriteria: ['获得灯状态'],
  tasks: [{
    task_id: 'task-1',
    run_id: 'run-1',
    parent_task_id: null,
    local_id: 'inspect',
    agent_id: 'environment_agent',
    objective: '读取客厅灯状态',
    source_request: '读取客厅灯状态',
    reason: '需要状态证据',
    status: 'completed',
    expected_evidence: ['开关状态'],
    success_criteria: ['获得开关状态'],
    depends_on: [],
    parallel_candidate: true,
    priority: 1,
    principal: null,
    target_type: null,
    target_mention: null,
    target: null,
    requested_capability: '',
    effective_scope: null,
    finding: null,
    failure: null,
    confirmation: null,
    created_at: '',
    updated_at: '',
  }],
  confirmation: null,
  events: [event(2, 'task.completed'), event(1, 'run.started')],
  usage: { ...EMPTY_USAGE },
  supervisorIterations: 1,
  agentLoops: 1,
  recoveryCount: 0,
  durationMs: null,
  createdAt: '',
  updatedAt: '',
  error: '',
  streamState: 'live',
}

describe('InlineTraceTimeline', () => {
  beforeEach(() => {
    localStorage.removeItem('wotagent-v2-console.trace-detail-level')
  })

  it('keeps a focused trace by default and reveals technical details on demand', async () => {
    vi.useFakeTimers()
    const detailedRun: RuntimeRun = {
      ...run,
      events: [
        event(1, 'run.started'),
        {
          ...event(2, 'model.request.started'),
          task_id: 'task-1',
          data: { purpose: 'task_planning' },
        },
        {
          ...event(3, 'model.request.completed'),
          task_id: 'task-1',
          data: { total_tokens: 128, raw_plan: { intent: 'control', steps: [{ id: 's1' }] } },
        },
        {
          ...event(4, 'agent.tool.completed'),
          data: { tool: 'ac_003__action__turn_on', step_id: 'step-1' },
        },
      ],
    }
    const wrapper = mount(InlineTraceTimeline, {
      props: { run: detailedRun, selected: true },
    })

    await vi.advanceTimersByTimeAsync(220)
    expect(wrapper.text()).toContain('已过滤 2')
    expect(wrapper.text()).not.toContain('128 tokens')

    await wrapper.find('.trace-technical-toggle').trigger('click')
    expect(wrapper.text()).toContain('128 tokens')
    expect(wrapper.text()).toContain('收起技术细节')
    expect(localStorage.getItem('wotagent-v2-console.trace-detail-level')).toBe('full')
    vi.useRealTimers()
  })

  it('reveals Run events progressively while preserving hierarchy', async () => {
    vi.useFakeTimers()
    const wrapper = mount(InlineTraceTimeline, { props: { run, selected: true } })
    expect(wrapper.findAll('.inline-trace-event')).toHaveLength(0)

    await vi.advanceTimersByTimeAsync(60)

    expect(wrapper.text()).toContain('environment_agent')
    expect(wrapper.text()).toContain('读取客厅灯状态')
    expect(wrapper.findAll('.inline-trace-event')).toHaveLength(1)
    vi.useRealTimers()
  })

  it('groups Tasks by actual scheduler wave and exposes dependency flow', async () => {
    vi.useFakeTimers()
    const healthTask = {
      ...run.tasks[0],
      task_id: 'task-health',
      local_id: 'health',
      agent_id: 'health_agent',
      objective: '检查健康状态',
    }
    const environmentTask = {
      ...run.tasks[0],
      task_id: 'task-environment',
      local_id: 'environment',
      agent_id: 'environment_agent',
      objective: '检查房间环境',
    }
    const careTask = {
      ...run.tasks[0],
      task_id: 'task-care',
      local_id: 'care',
      agent_id: 'care_agent',
      objective: '根据健康结果创建护理任务',
      depends_on: ['task-health'],
    }
    const waveRun: RuntimeRun = {
      ...run,
      status: 'completed',
      streamState: 'closed',
      tasks: [healthTask, environmentTask, careTask],
      events: [
        { ...event(1, 'run.started'), task_id: null, data: { root_goal: '并行检查后安排护理' } },
        { ...event(2, 'supervisor.decision.completed'), task_id: null, node: 'butler_supervisor', data: { action: 'create_tasks', reason: '健康和环境可以并行检查' } },
        { ...event(3, 'task.created'), task_id: 'task-health', node: 'health_agent' },
        { ...event(4, 'task.created'), task_id: 'task-environment', node: 'environment_agent' },
        { ...event(5, 'scheduler.parallel_batch.started'), task_id: null, node: 'task_scheduler', data: { task_ids: ['task-health', 'task-environment'], parallel: true } },
        { ...event(6, 'task.completed'), task_id: 'task-health', node: 'health_agent' },
        { ...event(7, 'task.completed'), task_id: 'task-environment', node: 'environment_agent' },
        { ...event(8, 'scheduler.parallel_batch.completed'), task_id: null, node: 'task_scheduler', data: { task_ids: ['task-health', 'task-environment'], parallel: true } },
        { ...event(9, 'supervisor.decision.completed'), task_id: null, node: 'butler_supervisor', iteration: 2, data: { action: 'create_tasks', reason: '健康证据满足后安排护理' } },
        { ...event(10, 'task.created'), task_id: 'task-care', node: 'care_agent' },
        { ...event(11, 'scheduler.parallel_batch.started'), task_id: null, node: 'task_scheduler', data: { task_ids: ['task-care'], parallel: false } },
        { ...event(12, 'task.completed'), task_id: 'task-care', node: 'care_agent' },
        { ...event(13, 'scheduler.parallel_batch.completed'), task_id: null, node: 'task_scheduler', data: { task_ids: ['task-care'], parallel: false } },
        { ...event(14, 'run.completed'), task_id: null, node: 'run_coordinator', data: { response: '检查和护理安排已完成' } },
      ],
    }
    const wrapper = mount(InlineTraceTimeline, {
      props: { run: waveRun, selected: true },
    })

    await wrapper.find('.inline-trace-header').trigger('click')
    await vi.advanceTimersByTimeAsync(800)

    expect(wrapper.findAll('.trace-supervisor-decision')).toHaveLength(2)
    expect(wrapper.findAll('.trace-wave')).toHaveLength(2)
    expect(wrapper.find('.trace-wave[data-parallel="true"]').text()).toContain('2 个 Task 并行')
    expect(wrapper.findAll('.trace-agent-card')).toHaveLength(3)
    expect(wrapper.text()).toContain('依赖已满足：Health Agent')
    expect(wrapper.text()).toContain('检查和护理安排已完成')
    vi.useRealTimers()
  })

  it('keeps completed Run history collapsible', async () => {
    const wrapper = mount(InlineTraceTimeline, {
      props: { run: { ...run, status: 'completed', streamState: 'closed' }, selected: false },
    })
    expect(wrapper.find('.inline-trace-body').exists()).toBe(false)
    await wrapper.find('.inline-trace-header').trigger('click')
    expect(wrapper.find('.inline-trace-body').exists()).toBe(true)
  })

  it('treats needs_more_information as a terminal clarification state', async () => {
    const wrapper = mount(InlineTraceTimeline, {
      props: {
        run: {
          ...run,
          status: 'needs_more_information',
          response: '请补充房间信息。',
          streamState: 'closed',
        },
        selected: false,
      },
    })
    expect(wrapper.find('.inline-trace-body').exists()).toBe(false)

    await wrapper.find('.inline-trace-header').trigger('click')

    expect(wrapper.text()).toContain('待补充信息')
  })

  it('opens while running and folds when the Run reaches a terminal status', async () => {
    const wrapper = mount(InlineTraceTimeline, {
      props: { run, selected: true },
    })
    expect(wrapper.find('.inline-trace-body').exists()).toBe(true)

    await wrapper.setProps({
      run: {
        ...run,
        status: 'completed',
        response: '客厅灯当前为关闭状态。',
        streamState: 'closed',
      },
    })

    expect(wrapper.find('.inline-trace-body').exists()).toBe(false)
    expect(wrapper.find('.inline-trace-header').attributes('aria-expanded')).toBe('false')

    await wrapper.find('.inline-trace-header').trigger('click')
    expect(wrapper.find('.inline-trace-body').exists()).toBe(true)
  })

  it('nests tool input and output under an active Task event', async () => {
    vi.useFakeTimers()
    const toolEvent = {
      ...event(3, 'agent.tool.completed'),
      data: {
        tool: 'light_001__read_state',
        step_id: 'step-1',
        input: { device_id: 'light-001' },
        output: { success: true, raw_result: { on: false } },
      },
    }
    const activeRun: RuntimeRun = {
      ...run,
      tasks: [{ ...run.tasks[0], status: 'running' }],
      events: [event(1, 'run.started'), event(2, 'task.started'), toolEvent],
    }
    const wrapper = mount(InlineTraceTimeline, {
      props: { run: activeRun, selected: true },
    })

    await vi.advanceTimersByTimeAsync(120)

    const toolRow = wrapper.findAll('.inline-trace-event').find(
      (item) => item.text().includes('light_001__read_state'),
    )
    expect(toolRow).toBeDefined()
    await toolRow!.trigger('click')

    expect(wrapper.text()).toContain('输入')
    expect(wrapper.text()).toContain('输出')
    expect(wrapper.text()).toContain('"device_id": "light-001"')
    expect(wrapper.text()).toContain('"on": false')
    expect(wrapper.find('.trace-group[data-status="running"]').exists()).toBe(true)
    expect(wrapper.find('.trace-stage[data-stage="execution"]').exists()).toBe(true)
    expect(wrapper.find('.trace-stage-events .inline-trace-event').exists()).toBe(true)
    expect(wrapper.find('.trace-waiting .runtime-spinner').exists()).toBe(true)
    vi.useRealTimers()
  })

  it('scrolls to the latest trace event when new events arrive', async () => {
    vi.useFakeTimers()
    const scrollIntoView = vi.fn()
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })
    const wrapper = mount(InlineTraceTimeline, { props: { run, selected: true } })

    await vi.advanceTimersByTimeAsync(60)
    expect(scrollIntoView).toHaveBeenCalled()

    scrollIntoView.mockClear()
    await wrapper.setProps({
      run: {
        ...run,
        events: [...run.events, event(3, 'agent.tool.completed')],
      },
    })

    await vi.advanceTimersByTimeAsync(80)

    expect(scrollIntoView).toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('collapses low-signal model events into technical details while keeping key events visible', async () => {
    vi.useFakeTimers()
    const detailedRun: RuntimeRun = {
      ...run,
      events: [
        event(1, 'run.started'),
        {
          ...event(2, 'model.request.started'),
          task_id: 'task-1',
          data: { purpose: 'task_planning' },
        },
        {
          ...event(3, 'model.request.completed'),
          task_id: 'task-1',
          data: { total_tokens: 128, raw_plan: { intent: 'control', steps: [{ id: 's1' }] } },
        },
        {
          ...event(4, 'agent.tool.completed'),
          data: { tool: 'ac_003__action__turn_on', step_id: 'step-1' },
        },
      ],
    }
    const wrapper = mount(InlineTraceTimeline, {
      props: { run: detailedRun, selected: true },
    })

    await vi.advanceTimersByTimeAsync(220)

    expect(wrapper.text()).toContain('已过滤 2')
    expect(wrapper.text()).toContain('ac_003__action__turn_on')
    expect(wrapper.text()).not.toContain('128 tokens')

    await wrapper.find('.trace-technical-toggle').trigger('click')
    expect(wrapper.text()).toContain('128 tokens')
    vi.useRealTimers()
  })
})
