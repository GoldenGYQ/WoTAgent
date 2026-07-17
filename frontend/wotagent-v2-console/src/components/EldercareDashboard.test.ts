import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentV2Api } from '../api/v2'
import EldercareDashboard from './EldercareDashboard.vue'

afterEach(() => vi.restoreAllMocks())

describe('EldercareDashboard', () => {
  it('runs a risk scenario and refreshes the monitoring view', async () => {
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
    vi.spyOn(agentV2Api, 'listEldercareDevices').mockResolvedValue({
      resident_id: 'elderly-001', devices: [],
    })
    const simulate = vi.spyOn(agentV2Api, 'simulateEldercare').mockResolvedValue({
      scenario: 'fall', resident_id: 'elderly-001', events: [],
      tasks: [{ task_id: 'eldercare-1', status: 'succeeded', response: '已处置' }],
    })

    const wrapper = mount(EldercareDashboard)
    await flushPromises()
    const collectionTab = wrapper.findAll('button').find((button) => button.text().includes('数据采集'))
    await collectionTab!.trigger('click')
    const fallButton = wrapper.findAll('button').find((button) => button.text().includes('模拟跌倒'))
    await fallButton!.trigger('click')
    await flushPromises()
    expect(simulate).toHaveBeenCalledWith('elderly-001', 'fall')
    expect(agentV2Api.getHealthSummary).toHaveBeenCalled()
    wrapper.unmount()
  })
})
