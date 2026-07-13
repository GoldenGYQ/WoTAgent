import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MetricStrip from './MetricStrip.vue'
import { EMPTY_USAGE, type RuntimeTask } from '../types/agent'

function task(cost: number, pricingAvailable: boolean): RuntimeTask {
  return {
    id: 'task-cost',
    turnId: 'turn-cost',
    conversationId: 'conversation-cost',
    sessionId: 'session-cost',
    message: '查询客厅灯',
    role: 'operator',
    status: 'succeeded',
    response: 'ok',
    plan: null,
    confirmation: null,
    events: [],
    usage: {
      ...EMPTY_USAGE,
      estimated_cost: cost,
      pricing_available: pricingAvailable,
    },
    loopCount: 1,
    recoveryCount: 0,
    createdAt: '',
    updatedAt: '',
    error: '',
    streamState: 'closed',
  }
}

describe('MetricStrip', () => {
  it('distinguishes an unsupported price from a real zero cost', () => {
    expect(mount(MetricStrip, { props: { task: task(0, false) } }).text()).toContain('未配置')
  })

  it('keeps a tiny estimated cost visible', () => {
    expect(mount(MetricStrip, { props: { task: task(0.00001, true) } }).text()).toContain('<¥0.0001')
  })
})
