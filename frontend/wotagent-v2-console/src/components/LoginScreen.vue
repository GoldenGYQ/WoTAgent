<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  ArrowLeft,
  Eye,
  HeartHandshake,
  KeyRound,
  LockKeyhole,
  LogIn,
  ShieldCheck,
  UserPlus,
} from 'lucide-vue-next'
import type {
  LoginAccount,
  PrincipalRegistration,
  RegistrationTarget,
  RoleTemplate,
} from '../types/agent'

const props = defineProps<{
  accounts: LoginAccount[]
  sceneTitle?: string
  domainId: string
  roleTemplates: RoleTemplate[]
  registrationTargets: RegistrationTarget[]
  busy?: boolean
  error?: string
}>()
const emit = defineEmits<{
  login: [account: LoginAccount, pin?: string]
  register: [payload: PrincipalRegistration]
}>()

const mode = ref<'accounts' | 'pin' | 'password' | 'register'>('accounts')
const selectedAccount = ref<LoginAccount | null>(null)
const loginPin = ref('')
const loginPassword = ref('')
const displayName = ref('')
const registerPin = ref('')
const templateId = ref('')
const targetId = ref('')

const selectedTemplate = computed(() => props.roleTemplates.find(
  (item) => item.template_id === templateId.value,
) ?? null)
const availableTargets = computed(() => props.registrationTargets.filter(
  (item) => item.target_type === selectedTemplate.value?.target_type,
))
const canRegister = computed(() => (
  displayName.value.trim().length >= 2
  && /^\d{4,12}$/.test(registerPin.value)
  && Boolean(selectedTemplate.value)
  && Boolean(targetId.value)
))

function chooseAccount(account: LoginAccount): void {
  if (account.loginMode === 'pin') {
    selectedAccount.value = account
    loginPin.value = ''
    mode.value = 'pin'
    return
  }
  if (account.loginMode === 'password') {
    selectedAccount.value = account
    loginPassword.value = ''
    mode.value = 'password'
    return
  }
  emit('login', account)
}

function submitPin(): void {
  if (!selectedAccount.value || !/^\d{4,12}$/.test(loginPin.value)) return
  emit('login', selectedAccount.value, loginPin.value)
}

function submitPassword(): void {
  if (!selectedAccount.value || loginPassword.value.length < 8) return
  emit('login', selectedAccount.value, loginPassword.value)
}

function startRegistration(): void {
  displayName.value = ''
  registerPin.value = ''
  templateId.value = props.roleTemplates[0]?.template_id ?? ''
  targetId.value = ''
  if (selectedTemplate.value) {
    targetId.value = availableTargets.value[0]?.target_id ?? ''
  }
  mode.value = 'register'
}

function selectTemplate(): void {
  targetId.value = availableTargets.value[0]?.target_id ?? ''
}

function submitRegistration(): void {
  if (!canRegister.value) return
  emit('register', {
    display_name: displayName.value.trim(),
    pin: registerPin.value,
    domain_id: props.domainId,
    role_template_id: templateId.value,
    target_id: targetId.value,
  })
}

function back(): void {
  mode.value = 'accounts'
  selectedAccount.value = null
  loginPin.value = ''
  loginPassword.value = ''
}

const capabilityLabels: Record<string, string> = {
  'domain.read': '查看领域运行信息',
  'device.read': '查看关联设备状态',
  'device.control': '控制普通设备',
  'health.status.read': '查看健康状态',
  'health.summary.read': '查看健康摘要',
  'health.vitals.read': '查看生命体征',
  'health.trend.read': '查看健康趋势',
  'care.coordinate': '发起照护协同',
  'environment.control': '调节养老环境',
  'environment.use': '控制住宅环境',
  'security.observe': '查看安防状态',
  'housekeeping.use': '使用家务设备',
}
</script>

<template>
  <main class="login-screen">
    <section class="login-panel">
      <header>
        <button
          v-if="mode !== 'accounts'"
          class="back-button"
          type="button"
          title="返回账号列表"
          @click="back"
        >
          <ArrowLeft :size="18" />
        </button>
        <span class="login-mark"><HeartHandshake :size="25" /></span>
        <div>
          <h1>WoTAgent 多主体协作平台</h1>
          <p>{{ sceneTitle ?? '当前场景' }} · 人类用户主体</p>
        </div>
      </header>

      <template v-if="mode === 'accounts'">
        <div class="section-heading">
          <div>
            <strong>选择账号</strong>
            <small>支持演示账号一键进入，以及账号密码 / PIN 登录</small>
          </div>
          <button class="register-entry" type="button" @click="startRegistration">
            <UserPlus :size="15" />注册新主体
          </button>
        </div>
        <div class="login-account-list">
          <button
            v-for="account in accounts"
            :key="account.accountId"
            type="button"
            :disabled="busy"
            @click="chooseAccount(account)"
          >
            <span class="account-icon">
              <LockKeyhole v-if="account.loginMode === 'pin'" :size="20" />
              <ShieldCheck v-else-if="account.role === 'operator'" :size="21" />
              <Eye v-else :size="21" />
            </span>
            <span class="account-copy">
              <span class="account-title">
                <strong>{{ account.accountId }}</strong>
                <i v-if="account.loginMode === 'pin'">PIN</i>
                <i v-else-if="account.loginMode === 'password'">账号密码</i>
                <i v-else>演示</i>
              </span>
              <small>{{ account.displayName }}</small>
              <em>{{ account.roleLabels.join(' / ') }} · {{ account.role }}</em>
              <span v-if="account.loginName">账号：{{ account.loginName }}</span>
              <span>{{ account.relationships[0]?.target_label ?? sceneTitle }}</span>
            </span>
            <LogIn :size="17" />
          </button>
        </div>
      </template>

      <form v-else-if="mode === 'pin'" class="auth-form" @submit.prevent="submitPin">
        <div class="form-intro">
          <span class="account-icon"><KeyRound :size="21" /></span>
          <div>
            <strong>{{ selectedAccount?.displayName }}</strong>
            <small>{{ selectedAccount?.accountId }} · {{ selectedAccount?.roleLabels.join(' / ') }}</small>
          </div>
        </div>
        <label>
          <span>登录 PIN</span>
          <input
            v-model="loginPin"
            type="password"
            inputmode="numeric"
            maxlength="12"
            autocomplete="current-password"
            placeholder="输入 4-12 位数字 PIN"
            autofocus
          >
        </label>
        <button class="primary-action" type="submit" :disabled="busy || !/^\d{4,12}$/.test(loginPin)">
          <LogIn :size="16" />{{ busy ? '正在验证' : '进入当前场景' }}
        </button>
      </form>

      <form v-else-if="mode === 'password'" class="auth-form" @submit.prevent="submitPassword">
        <div class="form-intro">
          <span class="account-icon"><LockKeyhole :size="21" /></span>
          <div>
            <strong>{{ selectedAccount?.displayName }}</strong>
            <small>
              {{ selectedAccount?.loginName || selectedAccount?.accountId }}
              · {{ selectedAccount?.roleLabels.join(' / ') }}
            </small>
          </div>
        </div>
        <label>
          <span>登录密码</span>
          <input
            v-model="loginPassword"
            type="password"
            minlength="8"
            maxlength="64"
            autocomplete="current-password"
            placeholder="输入账号密码"
            autofocus
          >
        </label>
        <button class="primary-action" type="submit" :disabled="busy || loginPassword.length < 8">
          <LogIn :size="16" />{{ busy ? '正在验证' : '进入当前场景' }}
        </button>
      </form>

      <form v-else class="auth-form registration-form" @submit.prevent="submitRegistration">
        <div class="form-heading">
          <strong>注册持久化账号</strong>
          <small>角色模板决定能力，绑定对象决定资源范围</small>
        </div>
        <div class="form-grid">
          <label>
            <span>显示名称</span>
            <input v-model="displayName" maxlength="30" placeholder="例如：张女士">
          </label>
          <label>
            <span>登录 PIN</span>
            <input
              v-model="registerPin"
              type="password"
              inputmode="numeric"
              maxlength="12"
              autocomplete="new-password"
              placeholder="4-12 位数字"
            >
          </label>
          <label>
            <span>领域角色</span>
            <select v-model="templateId" @change="selectTemplate">
              <option
                v-for="template in roleTemplates"
                :key="template.template_id"
                :value="template.template_id"
              >
                {{ template.label }}
              </option>
            </select>
          </label>
          <label>
            <span>绑定对象</span>
            <select v-model="targetId">
              <option
                v-for="target in availableTargets"
                :key="target.target_id"
                :value="target.target_id"
              >
                {{ target.label }}
              </option>
            </select>
          </label>
        </div>
        <div v-if="selectedTemplate" class="permission-preview">
          <div>
            <strong>{{ selectedTemplate.label }}</strong>
            <code>{{ selectedTemplate.platform_role }}</code>
          </div>
          <p>{{ selectedTemplate.description }}</p>
          <ul>
            <li v-for="capability in selectedTemplate.capabilities" :key="capability">
              {{ capabilityLabels[capability] ?? capability }}
            </li>
          </ul>
          <small>有效权限由后端模板和资源关系计算，不能在注册时任意扩权。</small>
        </div>
        <button class="primary-action" type="submit" :disabled="busy || !canRegister">
          <UserPlus :size="16" />{{ busy ? '正在创建账号' : '注册并进入' }}
        </button>
      </form>

      <p v-if="error" class="auth-error">{{ error }}</p>
      <footer>Principal 账号持久化保存 · 软件 Agent 权限独立受 AgentSpec 与 MCP Scope 约束</footer>
    </section>
  </main>
</template>

<style scoped>
.login-screen { min-height: 100dvh; display: grid; place-items: center; padding: 20px; background: linear-gradient(135deg, #dff6e8 0%, #bceccc 45%, #8ed9a9 100%); }
.login-panel { width: min(100%, 650px); padding: 26px; border: 1px solid var(--line); border-radius: 8px; background: #fff; box-shadow: 0 16px 50px rgba(22, 37, 46, .10); }
header { display: flex; align-items: center; gap: 12px; padding-bottom: 20px; border-bottom: 1px solid var(--line); }
.login-mark, .account-icon { display: grid; place-items: center; color: #fff; background: #176b64; }
.login-mark { width: 46px; height: 46px; border-radius: 7px; }
h1 { margin: 0; font-size: 19px; }
p { margin: 4px 0 0; color: var(--text-muted); font-size: 12px; }
.back-button { width: 34px; height: 34px; display: grid; place-items: center; border: 0; color: var(--text-muted); background: transparent; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 20px; }
.section-heading div, .form-heading { display: flex; flex-direction: column; gap: 3px; }
.section-heading small, .form-heading small { color: var(--text-muted); font-size: 10px; }
.register-entry { display: inline-flex; align-items: center; gap: 6px; padding: 8px 10px; border: 1px solid #6aa69f; border-radius: 6px; color: #176b64; background: #f3faf8; }
.login-account-list { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 13px; max-height: 390px; overflow: auto; }
.login-account-list > button { min-width: 0; min-height: 112px; display: grid; grid-template-columns: 34px 1fr 18px; align-items: center; gap: 9px; padding: 14px; border: 1px solid var(--line-strong); border-radius: 7px; color: var(--text); text-align: left; background: #fff; }
.login-account-list > button:hover { border-color: #4b918a; background: #f3faf8; }
.account-icon { width: 34px; height: 34px; border-radius: 50%; }
.account-copy { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.account-title { display: flex; align-items: center; gap: 6px; }
.account-copy strong { font-size: 14px; }
.account-copy small { color: var(--text); font-size: 11px; }
.account-copy em, .account-copy > span:last-child { color: var(--text-muted); font-size: 9px; font-style: normal; overflow-wrap: anywhere; }
.account-title i { padding: 2px 4px; border-radius: 3px; color: #176b64; font-size: 8px; font-style: normal; background: #dff3ed; }
.auth-form { display: flex; flex-direction: column; gap: 16px; margin-top: 22px; }
.form-intro { display: flex; align-items: center; gap: 11px; }
.form-intro div { display: flex; flex-direction: column; gap: 3px; }
.form-intro small { color: var(--text-muted); }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
label { display: flex; flex-direction: column; gap: 6px; color: var(--text-muted); font-size: 10px; }
input, select { width: 100%; min-height: 40px; padding: 9px 10px; border: 1px solid var(--line-strong); border-radius: 6px; color: var(--text); background: #fff; }
input:focus, select:focus { outline: 2px solid rgba(23, 107, 100, .18); border-color: #4b918a; }
.permission-preview { padding: 13px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.permission-preview > div { display: flex; align-items: center; gap: 8px; }
.permission-preview code { padding: 2px 5px; color: #176b64; background: #e9f6f2; }
.permission-preview ul { display: flex; flex-wrap: wrap; gap: 5px; margin: 10px 0; padding: 0; list-style: none; }
.permission-preview li { padding: 4px 6px; border: 1px solid #d7e7e3; border-radius: 4px; color: #315d58; font-size: 9px; background: #f7fbfa; }
.permission-preview small { color: var(--text-muted); font-size: 9px; }
.primary-action { min-height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: 7px; border: 0; border-radius: 6px; color: #fff; background: #176b64; }
.primary-action:disabled { opacity: .45; }
.auth-error { padding: 9px 10px; border-left: 3px solid #c94d4d; color: #a33636; background: #fff3f3; }
footer { margin-top: 17px; color: var(--text-faint); text-align: center; font-size: 9px; }
@media (max-width: 600px) {
  .login-screen { align-items: start; padding: 12px; }
  .login-panel { padding: 18px; }
  .login-account-list, .form-grid { grid-template-columns: 1fr; }
  .login-account-list { max-height: none; }
  .section-heading { align-items: flex-start; }
}
</style>
