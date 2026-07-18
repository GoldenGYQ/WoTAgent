import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { LoginAccount } from '../types/agent'
import LoginScreen from './LoginScreen.vue'

describe('LoginScreen', () => {
  it('offers two one-click experiment accounts and emits the selected identity', async () => {
    const accounts: LoginAccount[] = [
      { accountId: 'JH-1', displayName: '王护理员', role: 'operator', subjectId: 'elderly-001', subjectName: '张爷爷' },
      { accountId: 'JH-2', displayName: '赵观察员', role: 'viewer', subjectId: 'elderly-002', subjectName: '李奶奶' },
    ]
    const wrapper = mount(LoginScreen, { props: { accounts } })

    await wrapper.findAll('button')[1].trigger('click')

    expect(wrapper.text()).toContain('JH-1')
    expect(wrapper.text()).toContain('JH-2')
    expect(wrapper.emitted('login')?.[0]).toEqual([accounts[1]])
  })
})
