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
      task_id: 'task-1',
      user_message: '查询客厅灯状态',
      assistant_message: '客厅灯已开启',
      status: 'succeeded',
      created_at: '2026-07-13T08:00:00Z',
      updated_at: '2026-07-13T08:00:10Z',
    },
    {
      turn_id: 'turn-2',
      conversation_id: 'conv-1',
      task_id: 'task-2',
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
        tasks: [],
        activeTask: null,
        currentAccountId: 'JH-1',
        currentAccountName: '王护理员',
        sidebarCollapsed: false,
        traceCollapsed: false,
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
        tasks: [],
        activeTask: null,
        currentAccountId: 'JH-2',
        currentAccountName: '赵观察员',
        sidebarCollapsed: false,
        traceCollapsed: false,
      },
    })

    expect(wrapper.text()).toContain('JH-2')
    expect(wrapper.text()).toContain('欢迎赵观察员')
  })
})
