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
      actors: [],
    }] })
    const listSubjects = vi.spyOn(agentV2Api, 'listDomainSubjects').mockResolvedValue({
      domain_id: 'eldercare',
      subjects: [{ subject_id: 'elderly-001', label: '张爷爷', subtitle: 'A-101', metadata: { caregiver: '王护理员' } }],
    })
    vi.spyOn(agentV2Api, 'getDomainOverview').mockResolvedValue({
      subject: { subject_id: 'elderly-001', label: '张爷爷', subtitle: 'A-101', metadata: { caregiver: '王护理员' } },
      metrics: [
        { key: 'heart_rate', label: '当前心率', value: 82, unit: 'bpm', observed_at: '2026-07-18T08:00:00Z', source: 'test' },
        { key: 'blood_pressure', label: '当前血压', value: '122/78', unit: 'mmHg', observed_at: '2026-07-18T08:00:00Z', source: 'test' },
      ],
      open_event_count: 0,
      generated_at: '2026-07-18T08:00:00Z',
    })
    vi.spyOn(agentV2Api, 'getEldercareTrend').mockResolvedValue({
      resident: { resident_id: 'elderly-001', name: '张爷爷', room: 'A-101', caregiver: '王护理员', active: true },
      since: '2026-07-18T05:00:00Z',
      generated_at: '2026-07-18T08:00:00Z',
      series: [{
        sensor_type: 'blood_pressure',
        label: '血压',
        unit: 'mmHg',
        points: [
          { observed_at: '2026-07-18T07:30:00Z', value: '126/80', display_value: '126/80', numeric_value: 126, secondary_numeric_value: 80, source: 'test' },
          { observed_at: '2026-07-18T08:00:00Z', value: '122/78', display_value: '122/78', numeric_value: 122, secondary_numeric_value: 78, source: 'test' },
        ],
      }],
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
    expect(wrapper.text()).toContain('122/78 mmHg')
    expect(wrapper.text()).toContain('离线')
    expect(listSubjects).toHaveBeenCalledWith('eldercare', 'JH-1')
    await wrapper.get('[data-section="history"]').trigger('click')
    expect(wrapper.text()).toContain('健康历史记录与趋势')
    expect(wrapper.text()).toContain('血压')
    wrapper.unmount()
  })

  it('shows the smart-home actors for the villa profile', async () => {
    vi.spyOn(agentV2Api, 'listDomains').mockResolvedValue({ domains: [
      {
        domain_id: 'eldercare',
        title: '养老照护',
        description: '养老领域',
        subject_label: '照护对象',
        subject_label_plural: '照护对象',
        telemetry_signals: [],
        scenarios: [],
        monitor_rules: [],
        actors: [],
      },
      {
        domain_id: 'smart_home',
        title: '别墅智能管控',
        description: '住宅领域',
        subject_label: '管理范围',
        subject_label_plural: '管理范围',
        telemetry_signals: [],
        scenarios: [],
        monitor_rules: [],
        actors: [
          {
            actor_id: 'butler_run_supervisor',
            label: '目标编排 Supervisor',
            actor_type: 'supervisor',
            responsibility: '协调专业主体',
            capabilities: ['task.delegate'],
            resource_scope: ['site:villa-demo'],
            delegates_to: ['security_agent'],
            llm_driven: true,
          },
          {
            actor_id: 'security_agent',
            label: '安防处置 Agent',
            actor_type: 'specialist_agent',
            responsibility: '核查安防风险',
            capabilities: ['wot.security'],
            resource_scope: ['entrance'],
            delegates_to: [],
            llm_driven: true,
          },
        ],
      },
    ] })
    const listSubjects = vi.spyOn(agentV2Api, 'listDomainSubjects').mockResolvedValue({
      domain_id: 'smart_home',
      subjects: [{
        subject_id: 'villa-demo',
        label: '云栖别墅',
        subtitle: '12 个空间 · 23 台设备',
        metadata: { access: 'operator' },
      }],
    })
    vi.spyOn(agentV2Api, 'getDomainOverview').mockResolvedValue({
      subject: {
        subject_id: 'villa-demo',
        label: '云栖别墅',
        subtitle: '12 个空间 · 23 台设备',
        metadata: { access: 'operator' },
      },
      metrics: [],
      open_event_count: 0,
      generated_at: '2026-07-19T08:00:00Z',
    })
    vi.spyOn(agentV2Api, 'listDomainResources').mockResolvedValue({ resources: [] })
    vi.spyOn(agentV2Api, 'listDomainEvents').mockResolvedValue({ events: [] })
    vi.spyOn(agentV2Api, 'listDomainActions').mockResolvedValue({ actions: [] })
    vi.spyOn(agentV2Api, 'getDomainMonitorStatus').mockResolvedValue({
      service_id: 'villa-rule-monitor',
      status: 'online',
      reported_status: 'deterministic_on_ingest',
      details: { llm_driven: false },
      updated_at: '2026-07-19T08:00:00Z',
      age_seconds: 0,
    })

    const wrapper = mount(DomainOperationsCenter, {
      props: {
        principalId: 'JH-1',
        initialDomainId: 'smart_home',
      },
    })
    await flushPromises()
    await wrapper.get('[data-section="actors"]').trigger('click')

    expect(listSubjects).toHaveBeenCalledWith('smart_home', 'JH-1')
    expect(wrapper.text()).toContain('目标编排 Supervisor')
    expect(wrapper.text()).toContain('安防处置 Agent')
    expect(wrapper.text()).toContain('LLM 驱动')
    expect(wrapper.text()).not.toContain('养老照护')
    wrapper.unmount()
  })
})
