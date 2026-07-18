import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentV2Api } from '../api/v2'
import DomainOperationsCenter from './DomainOperationsCenter.vue'

afterEach(() => vi.restoreAllMocks())

describe('DomainOperationsCenter', () => {
  it('renders a registered domain and uses the principal-scoped API', async () => {
    vi.spyOn(agentV2Api, 'listDomains').mockResolvedValue({ domains: [{
      domain_id: 'eldercare',
      title: '养老照护',
      description: '领域描述',
      subject_label: '照护对象',
      subject_label_plural: '照护对象',
      telemetry_signals: [{ key: 'heart_rate', label: '心率', unit: 'bpm', value_type: 'number', default_value: '82' }],
      scenarios: [{ key: 'fall', label: '模拟跌倒' }],
      monitor_rules: [],
    }] })
    const listSubjects = vi.spyOn(agentV2Api, 'listDomainSubjects').mockResolvedValue({
      domain_id: 'eldercare',
      subjects: [{ subject_id: 'elderly-001', label: '张爷爷', subtitle: 'A-101', metadata: { caregiver: '王护理员' } }],
    })
    vi.spyOn(agentV2Api, 'getDomainOverview').mockResolvedValue({
      subject: { subject_id: 'elderly-001', label: '张爷爷', subtitle: 'A-101', metadata: { caregiver: '王护理员' } },
      metrics: [{ key: 'heart_rate', label: '当前心率', value: 82, unit: 'bpm', observed_at: '2026-07-18T08:00:00Z', source: 'test' }],
      open_event_count: 0,
      generated_at: '2026-07-18T08:00:00Z',
    })
    vi.spyOn(agentV2Api, 'listDomainResources').mockResolvedValue({ resources: [] })
    vi.spyOn(agentV2Api, 'listDomainEvents').mockResolvedValue({ events: [] })
    vi.spyOn(agentV2Api, 'listDomainActions').mockResolvedValue({ actions: [] })
    vi.spyOn(agentV2Api, 'getDomainMonitorStatus').mockResolvedValue({
      service_id: 'eldercare-monitor',
      status: 'offline',
      reported_status: 'unknown',
      details: {},
      updated_at: null,
      age_seconds: null,
    })

    const wrapper = mount(DomainOperationsCenter, {
      props: {
        principalId: 'JH-1',
        initialDomainId: 'eldercare',
        initialSubjectId: 'elderly-001',
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('领域运行中心')
    expect(wrapper.text()).toContain('养老照护')
    expect(wrapper.text()).toContain('张爷爷')
    expect(wrapper.text()).toContain('82 bpm')
    expect(wrapper.text()).toContain('离线')
    expect(listSubjects).toHaveBeenCalledWith('eldercare', 'JH-1')
    wrapper.unmount()
  })
})
