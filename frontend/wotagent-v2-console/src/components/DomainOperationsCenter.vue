<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Activity,
  BellRing,
  Check,
  Database,
  ExternalLink,
  Gauge,
  Play,
  RadioTower,
  RefreshCw,
  Send,
  Settings2,
  Users,
} from 'lucide-vue-next'
import { agentV2Api } from '../api/v2'
import type {
  DomainAction,
  DomainEvent,
  DomainManifest,
  DomainOverview,
  DomainResource,
  DomainSubject,
  DomainTelemetrySignal,
  MonitorStatus,
} from '../types/agent'

const props = defineProps<{
  principalId: string
  initialDomainId?: string
  initialSubjectId?: string
}>()
const emit = defineEmits<{ inspectRun: [runId: string] }>()

type Section = 'overview' | 'actors' | 'resources' | 'telemetry' | 'monitoring' | 'events'

const manifests = ref<DomainManifest[]>([])
const domainId = ref(props.initialDomainId ?? '')
const subjects = ref<DomainSubject[]>([])
const subjectId = ref('')
const overview = ref<DomainOverview | null>(null)
const resources = ref<DomainResource[]>([])
const events = ref<DomainEvent[]>([])
const actions = ref<DomainAction[]>([])
const monitor = ref<MonitorStatus | null>(null)
const activeSection = ref<Section>('overview')
const loading = ref(false)
const error = ref('')
const signalKey = ref('')
const signalValue = ref('')
const signalUnit = ref('')
let refreshTimer: ReturnType<typeof setInterval> | undefined
let refreshInFlight = false

const manifest = computed(() => manifests.value.find((item) => item.domain_id === domainId.value) ?? null)
const selectedSubject = computed(() => subjects.value.find((item) => item.subject_id === subjectId.value) ?? null)
const subjectAccessLabel = computed(() => {
  const metadata = overview.value?.subject.metadata
  if (!metadata) return ''
  if (metadata.caregiver) return String(metadata.caregiver)
  if (metadata.access === 'operator') return '可控制'
  if (metadata.access === 'viewer') return '只读'
  return ''
})
const signal = computed(() => manifest.value?.telemetry_signals.find((item) => item.key === signalKey.value) ?? null)
const sections = [
  { value: 'overview', label: '运行概览', icon: Gauge },
  { value: 'actors', label: '软件主体', icon: Users },
  { value: 'resources', label: '资源管理', icon: Settings2 },
  { value: 'telemetry', label: '数据观测', icon: Database },
  { value: 'monitoring', label: '规则监测', icon: RadioTower },
  { value: 'events', label: '事件与执行', icon: BellRing },
] as const

function formatTime(value: string | null): string {
  if (!value) return '--'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

function metricValue(value: unknown, unit = ''): string {
  if (value === null || value === undefined || value === '') return '--'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return `${String(value)}${unit ? ` ${unit}` : ''}`
}

function resourceState(resource: DomainResource): string {
  if (resource.error) return resource.error
  const entries = Object.entries(resource.state).filter(([key]) => key !== 'observedAt').slice(0, 2)
  return entries.length
    ? entries.map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`).join(' · ')
    : resource.capabilities.join(' · ') || '已接入'
}

function executionLabel(action: DomainAction): string {
  const labels: Record<string, string> = {
    wot_mcp: 'WoT/MCP',
    simulated: '模拟执行',
    'simulated-duty-roster': '模拟值班表',
    recorded: '平台记录',
  }
  return labels[action.execution_mode] ?? action.execution_mode
}

async function loadDomains(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const available = (await agentV2Api.listDomains()).domains
    manifests.value = props.initialDomainId
      ? available.filter((item) => item.domain_id === props.initialDomainId)
      : available
    domainId.value = manifests.value.some((item) => item.domain_id === domainId.value)
      ? domainId.value
      : manifests.value[0]?.domain_id ?? ''
    await loadSubjects()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function loadSubjects(): Promise<void> {
  if (!domainId.value) return
  const response = await agentV2Api.listDomainSubjects(domainId.value, props.principalId)
  subjects.value = response.subjects
  const preferred = props.initialSubjectId
  subjectId.value = subjects.value.some((item) => item.subject_id === preferred)
    ? preferred!
    : subjects.value[0]?.subject_id ?? ''
  configureSignal(manifest.value?.telemetry_signals[0])
  await refresh()
}

async function refresh(): Promise<void> {
  if (!domainId.value || !subjectId.value || refreshInFlight) return
  refreshInFlight = true
  error.value = ''
  try {
    const [nextOverview, resourceData, eventData, actionData, monitorData] = await Promise.all([
      agentV2Api.getDomainOverview(domainId.value, subjectId.value, props.principalId),
      agentV2Api.listDomainResources(domainId.value, subjectId.value, props.principalId),
      agentV2Api.listDomainEvents(domainId.value, subjectId.value, props.principalId),
      agentV2Api.listDomainActions(domainId.value, subjectId.value, props.principalId),
      agentV2Api.getDomainMonitorStatus(domainId.value),
    ])
    overview.value = nextOverview
    resources.value = resourceData.resources
    events.value = eventData.events
    actions.value = actionData.actions
    monitor.value = monitorData
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    refreshInFlight = false
  }
}

function configureSignal(next?: DomainTelemetrySignal | null): void {
  if (!next) return
  signalKey.value = next.key
  signalValue.value = next.default_value
  signalUnit.value = next.unit
}

function parseSignalValue(): number | boolean | string {
  if (signal.value?.value_type === 'boolean') {
    return ['true', '1', 'yes', '是'].includes(signalValue.value.trim().toLowerCase())
  }
  if (signal.value?.value_type === 'string') return signalValue.value.trim()
  const numeric = Number(signalValue.value)
  if (Number.isNaN(numeric)) throw new Error('该数据项需要数值输入')
  return numeric
}

async function submitTelemetry(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    await agentV2Api.ingestDomainTelemetry(domainId.value, {
      subject_id: subjectId.value,
      signal: signalKey.value,
      value: parseSignalValue(),
      unit: signalUnit.value,
      source: 'domain-console-simulator',
      principal_id: props.principalId,
    })
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function runScenario(scenario: string): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    await agentV2Api.simulateDomainScenario(domainId.value, {
      subject_id: subjectId.value,
      scenario,
      principal_id: props.principalId,
    })
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function acknowledge(eventId: string): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    await agentV2Api.acknowledgeDomainEvent(domainId.value, eventId, props.principalId)
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

function stopTimer(): void {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = undefined
}

function syncTimer(): void {
  stopTimer()
  if (document.visibilityState === 'visible') {
    refreshTimer = setInterval(() => void refresh(), 10000)
  }
}

function visibilityChanged(): void {
  syncTimer()
  if (document.visibilityState === 'visible') void refresh()
}

watch(domainId, () => void loadSubjects())
watch(subjectId, () => void refresh())
watch(signalKey, (key) => configureSignal(manifest.value?.telemetry_signals.find((item) => item.key === key)))
watch(() => props.principalId, () => void loadSubjects())

onMounted(() => {
  void loadDomains()
  document.addEventListener('visibilitychange', visibilityChanged)
  syncTimer()
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', visibilityChanged)
  stopTimer()
})
</script>

<template>
  <main class="domain-center">
    <header class="domain-heading">
      <div>
        <span class="platform-label">DOMAIN OPERATIONS</span>
        <h1>领域运行中心</h1>
        <p>{{ manifest?.title ?? '领域未加载' }} · {{ manifest?.description }}</p>
      </div>
      <div class="domain-toolbar">
        <select v-model="domainId" aria-label="选择领域">
          <option v-for="item in manifests" :key="item.domain_id" :value="item.domain_id">{{ item.title }}</option>
        </select>
        <select v-model="subjectId" :aria-label="`选择${manifest?.subject_label ?? '对象'}`">
          <option v-for="item in subjects" :key="item.subject_id" :value="item.subject_id">{{ item.label }} · {{ item.subtitle }}</option>
        </select>
        <button class="icon-button" type="button" title="刷新领域状态" @click="refresh">
          <RefreshCw :size="16" :class="{ spin: loading }" />
        </button>
      </div>
    </header>

    <div v-if="error" class="domain-error">{{ error }}</div>

    <section class="service-strip">
      <div>
        <span>当前领域</span>
        <strong>{{ manifest?.title ?? '--' }}</strong>
      </div>
      <div>
        <span>{{ manifest?.subject_label ?? '对象' }}</span>
        <strong>{{ selectedSubject?.label ?? '--' }}</strong>
      </div>
      <div>
        <span>Monitor</span>
        <strong class="monitor-state" :data-state="monitor?.status ?? 'offline'">
          <i />{{ monitor?.status === 'online' ? '在线' : '离线' }}
        </strong>
      </div>
      <div>
        <span>开放事件</span>
        <strong :class="{ danger: overview?.open_event_count }">{{ overview?.open_event_count ?? 0 }}</strong>
      </div>
      <div>
        <span>最后心跳</span>
        <strong>{{ formatTime(monitor?.updated_at ?? null) }}</strong>
      </div>
    </section>

    <nav class="domain-tabs" aria-label="领域运行模块">
      <button
        v-for="section in sections"
        :key="section.value"
        type="button"
        :data-section="section.value"
        :class="{ active: activeSection === section.value }"
        @click="activeSection = section.value"
      >
        <component :is="section.icon" :size="15" />{{ section.label }}
      </button>
    </nav>

    <section v-if="activeSection === 'overview'" class="overview-layout">
      <div class="subject-band">
        <Activity :size="20" />
        <div>
          <strong>{{ overview?.subject.label ?? '--' }}</strong>
          <span>
            {{ overview?.subject.subtitle }}
            <template v-if="subjectAccessLabel"> · {{ subjectAccessLabel }}</template>
          </span>
        </div>
      </div>
      <div class="metric-grid">
        <article v-for="item in overview?.metrics ?? []" :key="item.key">
          <span>{{ item.label }}</span>
          <strong>{{ metricValue(item.value, item.unit) }}</strong>
          <small>{{ item.observed_at ? formatTime(item.observed_at) : '暂无数据' }} · {{ item.source ?? '--' }}</small>
        </article>
      </div>
    </section>

    <section v-else-if="activeSection === 'actors'" class="section-body">
      <div class="section-heading">
        <div>
          <h2>软件能力主体</h2>
          <p>这里展示 Agent 与确定性服务；护理员、居民、业主和安保人员属于人类 Principal</p>
        </div>
        <span>{{ manifest?.actors?.length ?? 0 }} 个主体</span>
      </div>
      <div class="actor-grid">
        <article v-for="actor in manifest?.actors ?? []" :key="actor.actor_id">
          <header>
            <div>
              <strong>{{ actor.label }}</strong>
              <code>{{ actor.actor_id }}</code>
            </div>
            <span :data-llm="actor.llm_driven">
              {{ actor.llm_driven ? 'LLM 驱动' : '确定性服务' }}
            </span>
          </header>
          <p>{{ actor.responsibility }}</p>
          <dl>
            <div>
              <dt>能力</dt>
              <dd>{{ actor.capabilities.join(' · ') || '--' }}</dd>
            </div>
            <div>
              <dt>资源范围</dt>
              <dd>{{ actor.resource_scope.join(' · ') || '--' }}</dd>
            </div>
            <div v-if="actor.delegates_to.length">
              <dt>可委派</dt>
              <dd>{{ actor.delegates_to.join(' · ') }}</dd>
            </div>
          </dl>
        </article>
      </div>
    </section>

    <section v-else-if="activeSection === 'resources'" class="section-body">
      <div class="section-heading">
        <div><h2>领域资源</h2><p>设备、传感器与执行端统一通过 TD/MCP 接入</p></div>
        <span>{{ resources.length }} 项</span>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>资源</th><th>类别</th><th>接入</th><th>状态</th><th>最近观测</th><th>能力 / 当前值</th></tr></thead>
          <tbody>
            <tr v-for="item in resources" :key="item.resource_id">
              <td><strong>{{ item.label }}</strong><small>{{ item.resource_id }}</small></td>
              <td>{{ item.kind === 'sensor' ? '观测资源' : '执行资源' }}</td>
              <td>{{ item.transport }}</td>
              <td><span class="resource-status" :data-state="item.status"><i />{{ item.status }}</span></td>
              <td>{{ formatTime(item.last_seen) }}</td>
              <td class="state-cell">{{ resourceState(item) }}</td>
            </tr>
            <tr v-if="!resources.length"><td colspan="6" class="empty-row">当前对象暂无登记资源</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-else-if="activeSection === 'telemetry'" class="section-body telemetry-layout">
      <div>
        <div class="section-heading">
          <div><h2>仿真数据注入</h2><p>写入统一设备入口，等待独立 Monitor 采集与规则判断</p></div>
        </div>
        <div class="telemetry-form">
          <select v-model="signalKey" aria-label="数据项">
            <option v-for="item in manifest?.telemetry_signals ?? []" :key="item.key" :value="item.key">{{ item.label }}</option>
          </select>
          <input v-model="signalValue" aria-label="数据值" />
          <input v-model="signalUnit" class="unit-input" aria-label="单位" placeholder="单位" />
          <button type="button" :disabled="loading || !subjectId" @click="submitTelemetry"><Send :size="15" />注入</button>
        </div>
      </div>
      <div>
        <div class="section-heading">
          <div><h2>领域场景</h2><p>用于可重复的 Monitor 与 Agent 闭环实验</p></div>
        </div>
        <div class="scenario-list">
          <button v-for="item in manifest?.scenarios ?? []" :key="item.key" type="button" :disabled="loading || !subjectId" @click="runScenario(item.key)">
            <Play :size="14" />{{ item.label }}
          </button>
        </div>
      </div>
    </section>

    <section v-else-if="activeSection === 'monitoring'" class="section-body">
      <div class="section-heading">
        <div><h2>确定性 Monitor 规则</h2><p>规则服务不依赖 LLM；命中后生成领域事件</p></div>
        <span class="monitor-state" :data-state="monitor?.status ?? 'offline'"><i />{{ monitor?.status === 'online' ? '运行中' : '未运行' }}</span>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>规则</th><th>判定条件</th><th>数据窗口</th><th>事件处理</th></tr></thead>
          <tbody>
            <tr v-for="rule in manifest?.monitor_rules ?? []" :key="rule.key">
              <td><strong>{{ rule.label }}</strong><small>{{ rule.key }}</small></td>
              <td>{{ rule.condition }}</td>
              <td>{{ rule.freshness }}</td>
              <td>{{ rule.default_response }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-else class="event-layout">
      <div class="section-body">
        <div class="section-heading"><div><h2>领域事件</h2><p>Monitor 事件与关联智能体 Run</p></div><span>{{ events.length }} 项</span></div>
        <div class="table-scroll">
          <table>
            <thead><tr><th>时间</th><th>级别</th><th>事件</th><th>关联 Run</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="item in events" :key="item.event_id">
                <td>{{ formatTime(item.occurred_at) }}</td>
                <td><span class="severity" :data-level="item.severity">{{ item.severity }}</span></td>
                <td>{{ item.message }}</td>
                <td>
                  <button v-if="item.run_id" class="run-link" type="button" title="查看 Agent Run" @click="emit('inspectRun', item.run_id)">
                    <ExternalLink :size="12" />{{ item.run_id }}
                  </button>
                  <span v-else class="pending-label">等待处置</span>
                </td>
                <td>
                  <button v-if="item.status === 'open'" class="ack-button" type="button" @click="acknowledge(item.event_id)"><Check :size="13" />确认</button>
                  <span v-else>已处理</span>
                </td>
              </tr>
              <tr v-if="!events.length"><td colspan="5" class="empty-row">暂无领域事件</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="section-body">
        <div class="section-heading"><div><h2>执行记录</h2><p>区分真实执行、模拟交付与平台记录</p></div><span>{{ actions.length }} 项</span></div>
        <div class="table-scroll">
          <table>
            <thead><tr><th>时间</th><th>Agent</th><th>动作</th><th>执行方式</th><th>验证</th></tr></thead>
            <tbody>
              <tr v-for="item in actions" :key="item.action_id">
                <td>{{ formatTime(item.created_at) }}</td>
                <td>{{ item.agent_id }}</td>
                <td>{{ item.action }}</td>
                <td>{{ executionLabel(item) }}</td>
                <td>{{ item.verified ? '已验证' : '未验证' }}</td>
              </tr>
              <tr v-if="!actions.length"><td colspan="5" class="empty-row">暂无执行记录</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.domain-center { height: calc(100dvh - 50px); overflow: auto; padding: 18px 22px 30px; background: #f6f7f9; }
.domain-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.platform-label { color: var(--teal); font-size: 9px; font-weight: 800; }
.domain-heading h1 { margin: 2px 0 0; font-size: 20px; }
.domain-heading p, .section-heading p { margin: 3px 0 0; color: var(--text-muted); font-size: 11px; }
.domain-toolbar, .telemetry-form, .scenario-list { display: flex; align-items: center; gap: 8px; }
select, input { height: 34px; border: 1px solid var(--line-strong); border-radius: 5px; padding: 0 9px; color: var(--text); background: #fff; }
.domain-error { margin-top: 10px; padding: 9px 10px; color: var(--red); background: var(--red-soft); border: 1px solid #efcaca; border-radius: 5px; font-size: 12px; }
.service-strip { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); margin-top: 14px; border: 1px solid var(--line); background: #fff; }
.service-strip > div { min-width: 0; display: flex; flex-direction: column; gap: 5px; padding: 12px 14px; border-left: 1px solid var(--line); }
.service-strip > div:first-child { border-left: 0; }
.service-strip span { color: var(--text-muted); font-size: 9px; }
.service-strip strong { overflow: hidden; text-overflow: ellipsis; font-size: 13px; white-space: nowrap; }
.danger { color: var(--red); }
.monitor-state { display: inline-flex !important; flex-direction: row !important; align-items: center; gap: 6px !important; }
.monitor-state i, .resource-status i { width: 6px; height: 6px; border-radius: 50%; background: var(--red); }
.monitor-state[data-state="online"] { color: var(--green); }
.monitor-state[data-state="online"] i, .resource-status[data-state="online"] i { background: #20a265; }
.domain-tabs { display: flex; gap: 2px; margin-top: 14px; border-bottom: 1px solid var(--line); }
.domain-tabs button { height: 36px; display: inline-flex; align-items: center; gap: 6px; border: 0; border-bottom: 2px solid transparent; padding: 0 13px; color: var(--text-muted); background: transparent; font-size: 11px; font-weight: 650; }
.domain-tabs button.active { color: var(--teal); border-bottom-color: var(--teal); }
.overview-layout, .section-body { margin-top: 18px; }
.subject-band { display: flex; align-items: center; gap: 11px; padding: 14px 0; border-bottom: 1px solid var(--line); }
.subject-band > svg { color: var(--teal); }
.subject-band div { display: flex; flex-direction: column; gap: 3px; }
.subject-band span { color: var(--text-muted); font-size: 10px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--line); }
.metric-grid article { min-width: 0; display: flex; flex-direction: column; gap: 7px; padding: 16px 14px; border-left: 1px solid var(--line); }
.metric-grid article:first-child { border-left: 0; }
.metric-grid span, .metric-grid small { color: var(--text-muted); font-size: 9px; }
.metric-grid strong { font-size: 18px; }
.metric-grid small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.actor-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.actor-grid article { min-width: 0; padding: 13px; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
.actor-grid header { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.actor-grid header div { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.actor-grid code { overflow: hidden; color: var(--text-faint); font-size: 9px; text-overflow: ellipsis; }
.actor-grid header > span { flex: none; padding: 2px 5px; border-radius: 3px; color: var(--text-muted); background: var(--surface-muted); font-size: 9px; }
.actor-grid header > span[data-llm="true"] { color: var(--teal); background: var(--teal-soft); }
.actor-grid p { min-height: 34px; margin: 10px 0; color: var(--text-muted); font-size: 10px; line-height: 1.6; }
.actor-grid dl { display: grid; gap: 7px; margin: 0; }
.actor-grid dl div { display: grid; grid-template-columns: 48px 1fr; gap: 6px; }
.actor-grid dt { color: var(--text-faint); font-size: 9px; }
.actor-grid dd { min-width: 0; margin: 0; overflow-wrap: anywhere; font-size: 9px; line-height: 1.5; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.section-heading h2 { margin: 0; font-size: 14px; }
.section-heading > span { color: var(--text-muted); font-size: 10px; }
.table-scroll { overflow-x: auto; border: 1px solid var(--line); background: #fff; }
table { width: 100%; border-collapse: collapse; font-size: 10px; }
th, td { padding: 9px 10px; border-bottom: 1px solid #e8ebee; text-align: left; vertical-align: middle; }
th { color: var(--text-muted); background: #f8fafb; font-weight: 700; white-space: nowrap; }
td strong, td small { display: block; }
td small { margin-top: 3px; color: var(--text-faint); font-size: 9px; }
.state-cell { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.resource-status { display: inline-flex; align-items: center; gap: 5px; color: var(--text-muted); }
.resource-status[data-state="stale"] i { background: var(--amber); }
.resource-status[data-state="offline"] i { background: var(--red); }
.telemetry-layout, .event-layout { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; margin-top: 18px; }
.telemetry-layout .section-body { margin-top: 0; }
.telemetry-form, .scenario-list { flex-wrap: wrap; padding: 14px 0; }
.telemetry-form input { width: 120px; }
.telemetry-form .unit-input { width: 70px; }
.telemetry-form button, .scenario-list button, .ack-button, .run-link { height: 32px; display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--line-strong); border-radius: 5px; padding: 0 10px; background: #fff; font-size: 10px; }
.telemetry-form button { color: #fff; border-color: var(--teal); background: var(--teal); }
.scenario-list button:hover { border-color: var(--teal); color: var(--teal); }
.severity { display: inline-flex; padding: 2px 5px; border-radius: 3px; color: var(--amber); background: var(--amber-soft); }
.severity[data-level="critical"] { color: var(--red); background: var(--red-soft); }
.severity[data-level="high"] { color: #9b4a13; background: #fff0e7; }
.ack-button { height: 26px; color: var(--green); border-color: #b9d9cc; background: var(--green-soft); }
.run-link { height: 25px; max-width: 150px; overflow: hidden; color: var(--teal); border: 0; padding: 0; background: transparent; font-family: "Cascadia Code", Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }
.pending-label, .empty-row { color: var(--text-faint); }
.empty-row { padding: 22px; text-align: center; }
@media (max-width: 900px) {
  .service-strip { grid-template-columns: repeat(3, 1fr); }
  .metric-grid { grid-template-columns: repeat(2, 1fr); }
  .telemetry-layout, .event-layout { grid-template-columns: 1fr; }
  .actor-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 620px) {
  .domain-center { height: calc(100dvh - 50px); padding: 12px; }
  .domain-heading { align-items: flex-start; }
  .domain-heading p, .platform-label { display: none; }
  .domain-toolbar { flex-wrap: wrap; justify-content: flex-end; }
  .domain-toolbar select { max-width: 125px; }
  .actor-grid { grid-template-columns: 1fr; }
  .service-strip { grid-template-columns: repeat(2, 1fr); }
  .domain-tabs { overflow-x: auto; }
  .domain-tabs button { flex: 0 0 auto; padding: 0 9px; }
}
</style>
