import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type {
  LoginAccount,
  RegistrationTarget,
  RoleTemplate,
} from '../types/agent'
import LoginScreen from './LoginScreen.vue'

const accounts: LoginAccount[] = [
  {
    accountId: 'JH-1',
    displayName: '王敏',
    role: 'operator',
    domainRoles: ['caregiver'],
    roleLabels: ['护理员'],
    capabilities: ['health.summary.read'],
    relationships: [{ relation: 'caregiver_of', target_type: 'resident', target_id: 'elderly-001', target_label: '张爷爷' }],
    loginMode: 'one_click',
    subjectId: 'elderly-001',
    subjectName: '张爷爷',
  },
  {
    accountId: 'USR-1234',
    displayName: '周女士',
    role: 'viewer',
    domainRoles: ['family_observer'],
    roleLabels: ['家属观察者'],
    capabilities: ['health.summary.read'],
    relationships: [{ relation: 'family_of', target_type: 'resident', target_id: 'elderly-002', target_label: '李奶奶' }],
    loginMode: 'pin',
    subjectId: 'elderly-002',
    subjectName: '李奶奶',
  },
]

const templates: RoleTemplate[] = [{
  template_id: 'eldercare_family_observer',
  domain_id: 'eldercare',
  label: '家属观察者',
  description: '查看关联居民健康状态',
  platform_role: 'viewer',
  roles: ['family_observer'],
  capabilities: ['health.summary.read'],
  target_type: 'resident',
  relation: 'family_of',
  self_register: true,
}]

const targets: RegistrationTarget[] = [{
  domain_id: 'eldercare',
  target_type: 'resident',
  target_id: 'elderly-002',
  label: '李奶奶',
}]

function mountLogin() {
  return mount(LoginScreen, {
    props: {
      accounts,
      domainId: 'eldercare',
      roleTemplates: templates,
      registrationTargets: targets,
    },
  })
}

describe('LoginScreen', () => {
  it('keeps seeded accounts one-click and emits the selected identity', async () => {
    const wrapper = mountLogin()

    await wrapper.findAll('.login-account-list > button')[0].trigger('click')

    expect(wrapper.text()).toContain('JH-1')
    expect(wrapper.text()).toContain('USR-1234')
    expect(wrapper.text()).toContain('注册新主体')
    expect(wrapper.emitted('login')?.[0]).toEqual([accounts[0]])
  })

  it('requires a PIN for a registered persistent account', async () => {
    const wrapper = mountLogin()

    await wrapper.findAll('.login-account-list > button')[1].trigger('click')
    await wrapper.find('input[type="password"]').setValue('2468')
    await wrapper.find('form').trigger('submit')

    expect(wrapper.text()).toContain('周女士')
    expect(wrapper.emitted('login')?.[0]).toEqual([accounts[1], '2468'])
  })

  it('registers with a role template and resource target, not raw grants', async () => {
    const wrapper = mountLogin()

    await wrapper.find('.register-entry').trigger('click')
    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('新家属')
    await inputs[1].setValue('1357')
    await wrapper.find('form').trigger('submit')

    expect(wrapper.text()).toContain('有效权限由后端模板和资源关系计算')
    expect(wrapper.emitted('register')?.[0]).toEqual([{
      display_name: '新家属',
      pin: '1357',
      domain_id: 'eldercare',
      role_template_id: 'eldercare_family_observer',
      target_id: 'elderly-002',
    }])
  })
})
