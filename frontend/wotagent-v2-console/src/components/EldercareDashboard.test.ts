import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentV2Api } from '../api/v2'
import EldercareDashboard from './EldercareDashboard.vue'

afterEach(() => vi.restoreAllMocks())

describe('EldercareDashboard', () => {
  it('runs a risk scenario and renders its multi-agent trace', async () => {
    vi.spyOn(agentV2Api, 'listResidents').mockResolvedValue({ residents: [{
      resident_id: 'elderly-001', name: '张爷爷', room: 'A-101', caregiver: '王护理员', active: true,
    }] })
    vi.spyOn(agentV2Api, 'getHealthSummary').mockResolvedValue({
      resident: { resident_id: 'elderly-001', name: '张爷爷', room: 'A-101', caregiver: '王护理员', active: true },
      latest: { heart_rate: { value: 82, unit: 'bpm', observed_at: '2026-07-16T08:00:00Z', source: 'test' } },
      heart_rate_avg: 82,
      open_risks: [],
      generated_at: '2026-07-16T08:00:00Z',
    })
    vi.spyOn(agentV2Api, 'listRiskEvents').mockResolvedValue({ events: [] })
    vi.spyOn(agentV2Api, 'listCareActions').mockResolvedValue({ actions: [] })
    const simulate = vi.spyOn(agentV2Api, 'simulateEldercare').mockResolvedValue({
      scenario: 'fall', resident_id: 'elderly-001', events: [],
      tasks: [{ task_id: 'eldercare-1', status: 'succeeded', response: '已处置' }],
    })
    vi.spyOn(agentV2Api, 'getEvents').mockResolvedValue({
      next_cursor: 1,
      events: [{
        event_id: 'e1', sequence: 1, timestamp: '2026-07-16T08:00:00Z', trace_id: 'eldercare-1',
        task_id: 'eldercare-1', session_id: 'resident:elderly-001', type: 'agent.subgraph.completed',
        node: 'care_agent', iteration: 0, duration_ms: null, data: {},
      }],
    })

    const wrapper = mount(EldercareDashboard)
    await flushPromises()
    const fallButton = wrapper.findAll('button').find((button) => button.text().includes('模拟跌倒'))
    await fallButton!.trigger('click')
    await flushPromises()

    expect(simulate).toHaveBeenCalledWith('elderly-001', 'fall')
    expect(wrapper.text()).toContain('eldercare-1')
    expect(wrapper.text()).toContain('care_agent')
    expect(wrapper.text()).toContain('agent.subgraph.completed')
    wrapper.unmount()
  })
})
