import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MetricStrip from './MetricStrip.vue'
import { EMPTY_USAGE, type RuntimeRun } from '../types/agent'

function run(cost: number, pricingAvailable: boolean): RuntimeRun {
  return {
    id: 'run-cost',
    turnId: 'turn-cost',
    conversationId: 'conversation-cost',
    message: '查询客厅灯',
    role: 'operator',
    thinking: false,
    status: 'completed',
    response: 'ok',
    executionStrategy: 'parallel_when_safe',
    successCriteria: [],
    tasks: [],
    events: [],
    usage: { ...EMPTY_USAGE, estimated_cost: cost, pricing_available: pricingAvailable },
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
    expect(mount(MetricStrip, { props: { run: run(0, false) } }).text()).toContain('未配置')
  })

  it('keeps a tiny estimated cost visible', () => {
    expect(mount(MetricStrip, { props: { run: run(0.00001, true) } }).text()).toContain('<¥0.0001')
  })
})
