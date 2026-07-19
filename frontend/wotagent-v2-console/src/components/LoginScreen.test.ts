import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { LoginAccount } from '../types/agent'
import LoginScreen from './LoginScreen.vue'

describe('LoginScreen', () => {
  it('offers two one-click experiment accounts and emits the selected identity', async () => {
    const accounts: LoginAccount[] = [
      {
        accountId: 'JH-1',
        displayName: '王敏',
        role: 'operator',
        domainRoles: ['caregiver'],
        roleLabels: ['护理员'],
        capabilities: ['health.summary.read'],
        relationships: [{ relation: 'caregiver_of', target_type: 'resident', target_id: 'elderly-001', target_label: '张爷爷' }],
        subjectId: 'elderly-001',
        subjectName: '张爷爷',
      },
      {
        accountId: 'JH-2',
        displayName: '赵强',
        role: 'viewer',
        domainRoles: ['family_observer'],
        roleLabels: ['家属观察者'],
        capabilities: ['health.summary.read'],
        relationships: [{ relation: 'family_of', target_type: 'resident', target_id: 'elderly-002', target_label: '李奶奶' }],
        subjectId: 'elderly-002',
        subjectName: '李奶奶',
      },
    ]
    const wrapper = mount(LoginScreen, { props: { accounts } })

    await wrapper.findAll('button')[1].trigger('click')

    expect(wrapper.text()).toContain('JH-1')
    expect(wrapper.text()).toContain('JH-2')
    expect(wrapper.text()).toContain('人类用户主体')
    expect(wrapper.text()).toContain('护理员')
    expect(wrapper.emitted('login')?.[0]).toEqual([accounts[1]])
  })
})
