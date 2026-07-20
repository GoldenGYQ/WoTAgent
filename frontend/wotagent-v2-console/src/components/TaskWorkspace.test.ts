import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TaskWorkspace from './TaskWorkspace.vue'
import type { RuntimeConversation } from '../types/agent'

const conversation: RuntimeConversation = {
  conversation_id: 'conv-1',
  user_id: 'local-user',
  role: 'operator',
  title: '客厅灯控制',
  created_at: '2026-07-13T08:00:00Z',
  updated_at: '2026-07-13T08:01:00Z',
  turn_count: 2,
  loading: false,
  turns: [
    {
      turn_id: 'turn-1',
      conversation_id: 'conv-1',
      run_id: 'run-1',
      user_message: '查询客厅灯状态',
      assistant_message: '客厅灯已开启',
      status: 'succeeded',
      created_at: '2026-07-13T08:00:00Z',
      updated_at: '2026-07-13T08:00:10Z',
    },
    {
      turn_id: 'turn-2',
      conversation_id: 'conv-1',
      run_id: 'run-2',
      user_message: '把它关闭',
      assistant_message: '客厅灯已关闭',
      status: 'succeeded',
      created_at: '2026-07-13T08:01:00Z',
      updated_at: '2026-07-13T08:01:10Z',
    },
  ],
}

describe('TaskWorkspace', () => {
  it('renders every turn in one conversation instead of replacing the previous task', () => {
    const wrapper = mount(TaskWorkspace, {
      props: {
        conversation,
        runs: [],
        activeRun: null,
        currentAccountId: 'JH-1',
        currentAccountName: '王护理员',
        currentAccountRole: 'operator',
        currentSubjectName: '张爷爷',
        sidebarCollapsed: false,
      },
    })

    expect(wrapper.text()).toContain('查询客厅灯状态')
    expect(wrapper.text()).toContain('客厅灯已开启')
    expect(wrapper.text()).toContain('把它关闭')
    expect(wrapper.text()).toContain('客厅灯已关闭')
    expect(wrapper.findAll('.turn-group')).toHaveLength(2)
  })

  it('shows the current account id and welcome copy in the empty state', () => {
    const wrapper = mount(TaskWorkspace, {
      props: {
        conversation: null,
        runs: [],
        activeRun: null,
        currentAccountId: 'JH-2',
        currentAccountName: '赵观察员',
        currentAccountRole: 'viewer',
        currentAccountRoleLabel: '家属观察者',
        currentSubjectName: '李奶奶',
        currentDomainId: 'eldercare',
        sidebarCollapsed: false,
      },
    })

    expect(wrapper.text()).toContain('JH-2')
    expect(wrapper.text()).toContain('新会话')
    expect(wrapper.text()).toContain('家属观察者')
    expect(wrapper.text()).toContain('查看李奶奶今天的健康状态')
    expect(wrapper.text()).not.toContain('把李奶奶房间温度调到 24 度')
  })

  it('shows operator capabilities and emits a selected task example', async () => {
    const wrapper = mount(TaskWorkspace, {
      props: {
        conversation: null,
        runs: [],
        activeRun: null,
        currentAccountId: 'JH-1',
        currentAccountName: '王护理员',
        currentAccountRole: 'operator',
        currentSubjectName: '张爷爷',
        currentDomainId: 'eldercare',
        sidebarCollapsed: false,
      },
    })

    expect(wrapper.text()).toContain('设备控制')
    expect(wrapper.text()).toContain('条件联动任务')
    expect(wrapper.text()).toContain('打开张爷爷房间的灯并调到 45% 亮度')
    expect(wrapper.text()).not.toContain('打开客厅灯并调到 45% 亮度')
    const example = wrapper.findAll('.guidance-examples button').find(
      (item) => item.text().includes('如果偏热就降温'),
    )
    expect(example).toBeDefined()
    await example!.trigger('click')
    expect(wrapper.emitted('example')?.[0]).toEqual([
      '检查张爷爷房间当前温湿度，如果偏热就降温，如果偏干就提醒补充湿度',
    ])
  })

  it('shows smart home examples when the current domain is smart_home', () => {
    const wrapper = mount(TaskWorkspace, {
      props: {
        conversation: null,
        runs: [],
        activeRun: null,
        currentAccountId: 'JH-9',
        currentAccountName: '周管家',
        currentAccountRole: 'operator',
        currentSubjectName: '别墅',
        currentDomainId: 'smart_home',
        sidebarCollapsed: false,
      },
    })

    expect(wrapper.text()).toContain('打开客厅灯并调到 45% 亮度')
    expect(wrapper.text()).toContain('检查开着的灯数量，奇数就打开空调，偶数就打开加湿器')
    expect(wrapper.text()).not.toContain('打开别墅房间的灯并调到 45% 亮度')
  })
})
