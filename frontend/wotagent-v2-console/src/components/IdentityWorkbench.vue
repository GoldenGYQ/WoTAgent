<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  KeyRound,
  RefreshCw,
  Settings2,
  ShieldCheck,
  UserRound,
  Wrench,
} from 'lucide-vue-next'
import { agentV2Api } from '../api/v2'
import type {
  DeploymentProfile,
  DomainOverview,
  DomainResource,
  LoginAccount,
  ManagedUserCreatePayload,
  ManagedUserRecord,
  RegistrationTarget,
} from '../types/agent'

const props = defineProps<{
  currentAccount: LoginAccount
  domainId: string
  activeProfile: DeploymentProfile | null
  registrationTargets: RegistrationTarget[]
}>()

const loading = ref(false)
const error = ref('')
const managedUsers = ref<ManagedUserRecord[]>([])
const deviceRows = ref<Array<DomainResource & { subjectLabel: string }>>([])
const personalOverview = ref<DomainOverview | null>(null)
const createDisplayName = ref('')
const createLoginName = ref('')
const createPassword = ref('')
const createRole = ref<'admin' | 'viewer'>('viewer')
const createTargetId = ref('')
const roleDrafts = ref<Record<string, 'admin' | 'viewer'>>({})

const isAdmin = computed(() => props.currentAccount.role === 'admin')
const availableTargets = computed(() => props.registrationTargets.filter((item) => (
  props.domainId === 'eldercare'
    ? item.target_type === 'resident'
    : item.target_type === 'site'
)))

function resetCreateForm(): void {
  createDisplayName.value = ''
  createLoginName.value = ''
  createPassword.value = ''
  createRole.value = 'viewer'
  createTargetId.value = availableTargets.value[0]?.target_id ?? ''
}

async function loadIdentityData(): Promise<void> {
  if (!props.domainId) return
  loading.value = true
  error.value = ''
  try {
    if (isAdmin.value) {
      const [{ users }, { subjects }] = await Promise.all([
        agentV2Api.listManagedUsers(props.domainId),
        agentV2Api.listDomainSubjects(props.domainId, props.currentAccount.accountId),
      ])
      managedUsers.value = users
      roleDrafts.value = Object.fromEntries(users.map((item) => [
        item.principal_id,
        item.roles[0] === 'admin' ? 'admin' : 'viewer',
      ]))
      const resourceGroups = await Promise.all(subjects.map(async (subject) => {
        const response = await agentV2Api.listDomainResources(
          props.domainId,
          subject.subject_id,
          props.currentAccount.accountId,
        )
        return response.resources.map((resource) => ({
          ...resource,
          subjectLabel: subject.label,
        }))
      }))
      deviceRows.value = resourceGroups.flat()
      personalOverview.value = null
      if (!createTargetId.value) {
        createTargetId.value = availableTargets.value[0]?.target_id ?? ''
      }
    } else {
      managedUsers.value = []
      roleDrafts.value = {}
      deviceRows.value = []
      if (props.currentAccount.subjectId) {
        personalOverview.value = await agentV2Api.getDomainOverview(
          props.domainId,
          props.currentAccount.subjectId,
          props.currentAccount.accountId,
        )
      } else {
        personalOverview.value = null
      }
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function createUser(): Promise<void> {
  if (
    !createDisplayName.value.trim()
    || !createLoginName.value.trim()
    || createPassword.value.length < 8
    || (createRole.value === 'viewer' && !createTargetId.value)
  ) return
  loading.value = true
  error.value = ''
  try {
    const payload: ManagedUserCreatePayload = {
      display_name: createDisplayName.value.trim(),
      login_name: createLoginName.value.trim(),
      password: createPassword.value,
      domain_id: props.domainId,
      platform_role: createRole.value,
      target_id: createRole.value === 'viewer' ? createTargetId.value : undefined,
    }
    await agentV2Api.createManagedUser(payload)
    resetCreateForm()
    await loadIdentityData()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
    loading.value = false
  }
}

async function saveRole(user: ManagedUserRecord): Promise<void> {
  const nextRole = roleDrafts.value[user.principal_id] ?? 'viewer'
  loading.value = true
  error.value = ''
  try {
    await agentV2Api.updateManagedUserRole(user.principal_id, {
      domain_id: props.domainId,
      platform_role: nextRole,
      target_id: nextRole === 'viewer'
        ? (
          user.relationships[0]?.target_id
          ?? availableTargets.value[0]?.target_id
        )
        : undefined,
    })
    await loadIdentityData()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
    loading.value = false
  }
}

onMounted(() => {
  resetCreateForm()
  void loadIdentityData()
})

watch(
  () => [props.currentAccount.accountId, props.currentAccount.role, props.domainId],
  () => {
    resetCreateForm()
    void loadIdentityData()
  },
)
</script>

<template>
  <section class="identity-workbench">
    <header class="identity-header">
      <div>
        <strong>{{ isAdmin ? '管理员工作台' : '个人信息中心' }}</strong>
        <small>
          {{ isAdmin ? '系统配置、设备管理、用户角色管理' : '查询当前登录用户的个人信息与绑定对象概览' }}
        </small>
      </div>
      <button class="identity-refresh" type="button" :disabled="loading" @click="loadIdentityData">
        <RefreshCw :size="15" :class="{ spin: loading }" />
        刷新
      </button>
    </header>

    <p v-if="error" class="identity-error">{{ error }}</p>

    <template v-if="isAdmin">
      <div class="identity-grid">
        <article class="identity-card">
          <div class="identity-card-head">
            <span><Settings2 :size="16" />系统配置</span>
            <i>{{ activeProfile?.profile_id ?? '--' }}</i>
          </div>
          <ul class="identity-tag-list">
            <li>场景：{{ activeProfile?.title ?? '--' }}</li>
            <li>领域：{{ activeProfile?.domain_id ?? domainId }}</li>
            <li>站点：{{ activeProfile?.site_id ?? '--' }}</li>
            <li>空间数：{{ activeProfile?.spaces.length ?? 0 }}</li>
            <li>设备数：{{ activeProfile?.device_ids.length ?? 0 }}</li>
            <li>启用 Agent：{{ activeProfile?.enabled_agents.length ?? 0 }}</li>
          </ul>
          <p>{{ activeProfile?.description ?? '当前激活部署配置' }}</p>
        </article>

        <article class="identity-card">
          <div class="identity-card-head">
            <span><ShieldCheck :size="16" />新增用户</span>
            <i>账号密码登录</i>
          </div>
          <div class="identity-form-grid">
            <label>
              <span>显示名称</span>
              <input v-model="createDisplayName" maxlength="30" placeholder="例如：李女士">
            </label>
            <label>
              <span>登录账号</span>
              <input v-model="createLoginName" maxlength="32" placeholder="例如：user02">
            </label>
            <label>
              <span>登录密码</span>
              <input v-model="createPassword" type="password" maxlength="64" placeholder="至少 8 位">
            </label>
            <label>
              <span>角色</span>
              <select v-model="createRole">
                <option value="viewer">普通用户</option>
                <option value="admin">管理员</option>
              </select>
            </label>
            <label v-if="createRole === 'viewer'">
              <span>绑定对象</span>
              <select v-model="createTargetId">
                <option v-for="target in availableTargets" :key="target.target_id" :value="target.target_id">
                  {{ target.label }}
                </option>
              </select>
            </label>
          </div>
          <button class="identity-primary" type="button" :disabled="loading" @click="createUser">
            <KeyRound :size="15" />创建用户
          </button>
        </article>
      </div>

      <article class="identity-card">
        <div class="identity-card-head">
          <span><UserRound :size="16" />用户角色管理</span>
          <i>{{ managedUsers.length }} 个账号</i>
        </div>
        <table class="identity-table">
          <thead>
            <tr>
              <th>账号</th>
              <th>姓名</th>
              <th>当前角色</th>
              <th>绑定对象</th>
              <th>调整</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in managedUsers" :key="user.principal_id">
              <td>
                <strong>{{ user.login_name || user.principal_id }}</strong>
                <small>{{ user.principal_id }}</small>
              </td>
              <td>{{ user.display_name }}</td>
              <td>{{ user.role_labels.join(' / ') }}</td>
              <td>{{ user.relationships[0]?.target_label ?? '全局' }}</td>
              <td class="role-actions">
                <select v-model="roleDrafts[user.principal_id]">
                  <option value="viewer">普通用户</option>
                  <option value="admin">管理员</option>
                </select>
                <button type="button" :disabled="loading" @click="saveRole(user)">保存</button>
              </td>
            </tr>
          </tbody>
        </table>
      </article>

      <article class="identity-card">
        <div class="identity-card-head">
          <span><Wrench :size="16" />设备管理</span>
          <i>{{ deviceRows.length }} 台/项资源</i>
        </div>
        <table class="identity-table">
          <thead>
            <tr>
              <th>所属对象</th>
              <th>设备</th>
              <th>类型</th>
              <th>状态</th>
              <th>能力</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="resource in deviceRows" :key="`${resource.subjectLabel}-${resource.resource_id}`">
              <td>{{ resource.subjectLabel }}</td>
              <td>{{ resource.label }}</td>
              <td>{{ resource.kind }}</td>
              <td>{{ resource.status }}</td>
              <td>{{ resource.capabilities.join(', ') || '--' }}</td>
            </tr>
          </tbody>
        </table>
      </article>
    </template>

    <template v-else>
      <div class="identity-grid">
        <article class="identity-card">
          <div class="identity-card-head">
            <span><UserRound :size="16" />个人信息</span>
            <i>{{ currentAccount.roleLabels.join(' / ') }}</i>
          </div>
          <ul class="identity-tag-list">
            <li>账号：{{ currentAccount.loginName || currentAccount.accountId }}</li>
            <li>姓名：{{ currentAccount.displayName }}</li>
            <li>角色：{{ currentAccount.role }}</li>
            <li>绑定对象：{{ currentAccount.subjectName || '未绑定' }}</li>
          </ul>
          <p>当前用户仅展示与自身账号和绑定对象相关的信息，不提供管理员配置能力。</p>
        </article>

        <article class="identity-card">
          <div class="identity-card-head">
            <span><ShieldCheck :size="16" />权限范围</span>
            <i>{{ currentAccount.capabilities.length }} 项</i>
          </div>
          <ul class="identity-tag-list">
            <li v-for="capability in currentAccount.capabilities" :key="capability">{{ capability }}</li>
          </ul>
        </article>
      </div>

      <article class="identity-card" v-if="personalOverview">
        <div class="identity-card-head">
          <span><Settings2 :size="16" />绑定对象概览</span>
          <i>{{ personalOverview.subject.label }}</i>
        </div>
        <table class="identity-table">
          <thead>
            <tr>
              <th>指标</th>
              <th>当前值</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="metric in personalOverview.metrics" :key="metric.key">
              <td>{{ metric.label }}</td>
              <td>{{ metric.value ?? '--' }} {{ metric.unit }}</td>
              <td>{{ metric.observed_at ?? '--' }}</td>
            </tr>
          </tbody>
        </table>
      </article>
    </template>
  </section>
</template>

<style scoped>
.identity-workbench { display: flex; flex-direction: column; gap: 14px; }
.identity-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.identity-header strong { font-size: 16px; }
.identity-header small { display: block; margin-top: 4px; color: var(--text-muted); }
.identity-refresh, .identity-primary, .role-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 36px;
  padding: 0 12px;
  border: 1px solid #cfe3df;
  border-radius: 10px;
  background: #fff;
}
.identity-primary, .role-actions button {
  border: 0;
  color: #fff;
  background: #176b64;
}
.identity-error {
  padding: 10px 12px;
  border-radius: 10px;
  color: #a33636;
  background: #fff1f1;
}
.identity-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
}
.identity-card {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 10px 24px rgba(18, 43, 41, 0.05);
}
.identity-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.identity-card-head span {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
}
.identity-card-head i {
  color: var(--text-muted);
  font-style: normal;
  font-size: 12px;
}
.identity-tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.identity-tag-list li {
  padding: 6px 10px;
  border-radius: 999px;
  background: #f3faf8;
  color: #245954;
  font-size: 12px;
}
.identity-form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-muted);
  font-size: 12px;
}
input, select {
  min-height: 38px;
  padding: 8px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: #fff;
}
.identity-table {
  width: 100%;
  border-collapse: collapse;
}
.identity-table th,
.identity-table td {
  padding: 10px 8px;
  border-top: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
  font-size: 13px;
}
.identity-table td small {
  display: block;
  margin-top: 4px;
  color: var(--text-muted);
}
.role-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.spin { animation: spin 0.9s linear infinite; }
@media (max-width: 720px) {
  .identity-header { align-items: flex-start; flex-direction: column; }
  .role-actions { flex-direction: column; align-items: stretch; }
}
</style>
