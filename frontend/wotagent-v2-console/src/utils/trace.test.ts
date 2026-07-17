import { describe, expect, it } from 'vitest'
import type { TraceEvent } from '../types/agent'
import { eventSummary, eventTitle, eventTone } from './trace'

function event(type: string, data: Record<string, unknown> = {}): TraceEvent {
  return {
    event_id: 'event-1',
    sequence: 1,
    timestamp: '2026-07-13T08:00:00Z',
    run_id: 'run-1',
    turn_id: 'turn-1',
    task_id: 'task-1',
    parent_task_id: null,
    agent_run_id: null,
    tool_call_id: null,
    type,
    node: 'executor',
    iteration: 1,
    data: { duration_ms: 1234, ...data },
  }
}

describe('trace presentation', () => {
  it('labels model and tool events with concise summaries', () => {
    const model = event('model.request.completed', { total_tokens: 912 })
    const tool = event('agent.tool.completed', { tool: 'light_001__action__turn_on', step_id: 's1' })

    expect(eventTitle(model)).toBe('模型响应')
    expect(eventSummary(model)).toBe('912 tokens · 1.23 s')
    expect(eventTone(model)).toBe('model')
    expect(eventSummary(tool)).toContain('light_001__action__turn_on')
    expect(eventTone(tool)).toBe('tool')
  })

  it('surfaces verification failures', () => {
    const verification = event('verification.completed', { passed: false })
    expect(eventTitle(verification)).toBe('状态验证')
    expect(eventSummary(verification)).toBe('后置条件未满足')
    expect(eventTone(verification)).toBe('verify')
  })

  it('describes the bounded conversation context added to a task', () => {
    const context = event('context.built', {
      history_messages: 4,
      history_characters: 128,
    })
    expect(eventTitle(context)).toBe('会话上下文')
    expect(eventSummary(context)).toContain('4 条历史')
    expect(eventTone(context)).toBe('verify')
  })
})
