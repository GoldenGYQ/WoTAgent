<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Activity, AlertTriangle, BedDouble, BellRing, Check, Database, HeartPulse, Play, RadioTower, RefreshCw, Send, Settings2, Thermometer, UserRoundCheck } from 'lucide-vue-next'
import { agentV2Api } from '../api/v2'
import type { CareAction, EldercareDevice, HealthSummary, Resident, RiskEvent, SensorType } from '../types/agent'

const props = defineProps<{ initialResidentId?: string }>()

const residents = ref<Resident[]>([])
const selectedId = ref('')
const summary = ref<HealthSummary | null>(null)
const risks = ref<RiskEvent[]>([])
const actions = ref<CareAction[]>([])
const devices = ref<EldercareDevice[]>([])
const activeSection = ref<'devices' | 'collection' | 'health' | 'alerts'>('health')
const loading = ref(false)
const error = ref('')
const sensorType = ref<SensorType>('heart_rate')
const sensorValue = ref('82')
const unit = ref('bpm')
let refreshTimer: ReturnType<typeof setInterval> | undefined

const latest = computed(() => summary.value?.latest ?? {})
const selectedResident = computed(() => residents.value.find((item) => item.resident_id === selectedId.value))
const sensorOptions: Array<{ value: SensorType; label: string; unit: string }> = [
  { value: 'heart_rate', label: '心率', unit: 'bpm' },
  { value: 'spo2', label: '血氧', unit: '%' },
  { value: 'fall_detected', label: '跌倒状态', unit: '' },
  { value: 'bed_occupancy', label: '在床状态', unit: '' },
  { value: 'sleep_quality', label: '睡眠质量', unit: '' },
  { value: 'room_temperature', label: '室温', unit: 'C' },
  { value: 'room_humidity', label: '湿度', unit: '%' },
]
const scenarios = [
  { value: 'normal', label: '正常数据' },
  { value: 'high_heart_rate', label: '持续高心率' },
  { value: 'low_spo2', label: '低血氧' },
  { value: 'fall', label: '模拟跌倒' },
  { value: 'hot_room', label: '房间过热' },
]
const sections = [
  { value: 'devices', label: '设备管理', icon: Settings2 },
  { value: 'collection', label: '数据采集', icon: Database },
  { value: 'health', label: '健康检测', icon: HeartPulse },
  { value: 'alerts', label: '异常告警', icon: BellRing },
] as const
const POLL_INTERVAL_MS = 10000
let refreshInFlight = false

function displayMetric(type: string, fallback = '--'): string {
  const item = latest.value[type]
  if (!item) return fallback
  if (typeof item.value === 'boolean') return item.value ? '是' : '否'
  return `${item.value}${item.unit ? ` ${item.unit}` : ''}`
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value))
}

function deviceState(device: EldercareDevice): string {
  if (device.status === 'no_data') return '尚无数据'
  if (device.status === 'offline') return String(device.state.error ?? '连接失败')
  const entries = Object.entries(device.state).slice(0, 2)
  return entries.length ? entries.map(([key, value]) => {
    const display = typeof value === 'object' && value !== null && 'value' in value ? (value as { value: unknown }).value : value
    return `${key}: ${display}`
  }).join(' · ') : '已连接'
}

async function refresh(): Promise<void> {
  if (!selectedId.value || refreshInFlight) return
  refreshInFlight = true
  try {
    const [health, riskData, actionData, deviceData] = await Promise.all([
      agentV2Api.getHealthSummary(selectedId.value),
      agentV2Api.listRiskEvents(selectedId.value),
      agentV2Api.listCareActions(selectedId.value),
      agentV2Api.listEldercareDevices(selectedId.value),
    ])
    summary.value = health
    risks.value = riskData.events
    actions.value = actionData.actions
    devices.value = deviceData.devices
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    refreshInFlight = false
  }
}

async function loadResidents(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    residents.value = (await agentV2Api.listResidents()).residents
    selectedId.value ||= residents.value.find(
      (item) => item.resident_id === props.initialResidentId,
    )?.resident_id ?? residents.value[0]?.resident_id ?? ''
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

function parseValue(): number | boolean | string {
  if (['fall_detected', 'bed_occupancy'].includes(sensorType.value)) {
    return ['true', '1', '是', 'yes'].includes(sensorValue.value.trim().toLowerCase())
  }
  if (sensorType.value === 'sleep_quality') return sensorValue.value.trim()
  const numeric = Number(sensorValue.value)
  if (Number.isNaN(numeric)) throw new Error('该传感器需要数值输入')
  return numeric
}

async function submitReading(): Promise<void> {
  error.value = ''
  loading.value = true
  try {
    await agentV2Api.ingestReading({
      resident_id: selectedId.value,
      sensor_type: sensorType.value,
      value: parseValue(),
      unit: unit.value,
      source: 'web-console',
    })
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function runScenario(scenario: string): Promise<void> {
  error.value = ''
  loading.value = true
  try {
    await agentV2Api.simulateEldercare(selectedId.value, scenario)
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
    await agentV2Api.acknowledgeRisk(eventId)
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

watch(selectedId, () => void refresh())
watch(() => props.initialResidentId, (value) => {
  if (value && residents.value.some((item) => item.resident_id === value)) selectedId.value = value
})
watch(sensorType, (value) => {
  const option = sensorOptions.find((item) => item.value === value)
  unit.value = option?.unit ?? ''
  sensorValue.value = value === 'sleep_quality' ? 'good' : ['fall_detected', 'bed_occupancy'].includes(value) ? 'false' : value === 'room_temperature' ? '24' : value === 'spo2' ? '97' : '82'
})

function stopRefreshTimer(): void {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = undefined
  }
}

function startRefreshTimer(): void {
  if (refreshTimer || document.visibilityState === 'hidden') return
  refreshTimer = setInterval(() => {
    void refresh()
  }, POLL_INTERVAL_MS)
}

function syncRefreshTimer(): void {
  if (document.visibilityState === 'hidden') {
    stopRefreshTimer()
    return
  }
  startRefreshTimer()
}

function handleVisibilityChange(): void {
  syncRefreshTimer()
  if (document.visibilityState === 'visible') {
    void refresh()
  }
}

onMounted(() => {
  void loadResidents()
  document.addEventListener('visibilitychange', handleVisibilityChange)
  syncRefreshTimer()
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  stopRefreshTimer()
})
</script>

<template>
  <main class="eldercare-dashboard">
    <header class="eldercare-heading">
      <div>
        <h1>养老院健康监测</h1>
        <p>传感器事件、风险分派与照护执行</p>
      </div>
      <div class="eldercare-toolbar">
        <select v-model="selectedId" aria-label="选择老人">
          <option v-for="resident in residents" :key="resident.resident_id" :value="resident.resident_id">
            {{ resident.name }} · {{ resident.room }}
          </option>
        </select>
        <button class="icon-button" type="button" title="刷新数据" @click="refresh"><RefreshCw :size="16" :class="{ spin: loading }" /></button>
      </div>
    </header>

    <div v-if="error" class="eldercare-error">{{ error }}</div>

    <nav class="module-tabs" aria-label="养老业务模块">
      <button v-for="section in sections" :key="section.value" type="button" :class="{ active: activeSection === section.value }" @click="activeSection = section.value">
        <component :is="section.icon" :size="15" />{{ section.label }}
      </button>
    </nav>

    <section v-if="activeSection === 'health'" class="resident-strip">
      <div class="resident-identity">
        <span><UserRoundCheck :size="20" /></span>
        <div><strong>{{ selectedResident?.name ?? '未选择' }}</strong><small>{{ selectedResident?.room }} · {{ selectedResident?.caregiver }}</small></div>
      </div>
      <div><HeartPulse :size="17" /><span>当前心率</span><strong>{{ displayMetric('heart_rate') }}</strong></div>
      <div><Activity :size="17" /><span>血氧</span><strong>{{ displayMetric('spo2') }}</strong></div>
      <div><BedDouble :size="17" /><span>睡眠</span><strong>{{ displayMetric('sleep_quality') }}</strong></div>
      <div><Thermometer :size="17" /><span>室温</span><strong>{{ displayMetric('room_temperature') }}</strong></div>
      <div class="risk-count"><AlertTriangle :size="17" /><span>开放风险</span><strong>{{ summary?.open_risks.length ?? 0 }}</strong></div>
    </section>

    <section v-if="activeSection === 'devices'" class="device-management">
      <div class="section-heading"><div><h2>设备清单</h2><p>传感器接入状态与 WoT 执行设备映射</p></div><span>{{ devices.length }} 台</span></div>
      <div class="table-scroll">
        <table><thead><tr><th>设备</th><th>类型</th><th>接入协议</th><th>状态</th><th>最近数据</th><th>能力 / 当前值</th></tr></thead>
          <tbody><tr v-for="device in devices" :key="device.device_id"><td><strong>{{ device.name }}</strong><small>{{ device.device_id }}</small></td><td>{{ device.kind === 'sensor' ? '状态监视器' : '执行设备' }}</td><td>{{ device.transport }}</td><td><span class="device-status" :data-state="device.status"><i />{{ device.status }}</span></td><td>{{ device.last_seen ? formatTime(device.last_seen) : '--' }}</td><td class="state-cell">{{ deviceState(device) }}</td></tr><tr v-if="!devices.length"><td colspan="6" class="empty-row">暂无登记设备</td></tr></tbody></table>
      </div>
    </section>

    <div v-if="activeSection === 'collection'" class="eldercare-control-band">
      <section class="control-section">
        <h2><RadioTower :size="15" />手动传感器输入</h2>
        <div class="reading-form">
          <select v-model="sensorType" aria-label="传感器类型">
            <option v-for="option in sensorOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
          <input v-model="sensorValue" aria-label="传感器值" />
          <input v-model="unit" class="unit-input" aria-label="单位" placeholder="单位" />
          <button type="button" title="提交读数" :disabled="loading || !selectedId" @click="submitReading"><Send :size="15" />提交</button>
        </div>
      </section>
      <section class="control-section">
        <h2>场景模拟</h2>
        <div class="scenario-buttons">
          <button v-for="scenario in scenarios" :key="scenario.value" type="button" :disabled="loading || !selectedId" @click="runScenario(scenario.value)">
            <Play :size="14" />{{ scenario.label }}
          </button>
        </div>
      </section>
    </div>

    <section v-if="activeSection === 'health'" class="health-rules">
      <div class="section-heading"><div><h2>自动检测规则</h2><p>Health Monitor 每 5 秒扫描，新读数写入时立即检测</p></div><span class="monitor-live"><i />运行中</span></div>
      <div class="table-scroll"><table><thead><tr><th>检测项</th><th>判定条件</th><th>新鲜度窗口</th><th>默认处置</th></tr></thead><tbody>
        <tr><td>持续高心率</td><td>连续约 5 分钟 &gt; 100 bpm</td><td>5 分钟</td><td>通知护理员、创建照护任务</td></tr>
        <tr><td>低血氧</td><td>SpO2 &lt; 92%</td><td>5 分钟</td><td>通知护理员、创建照护任务</td></tr>
        <tr><td>疑似跌倒</td><td>fall_detected = true</td><td>1 分钟</td><td>立即通知护理员</td></tr>
        <tr><td>房间过热</td><td>室温 &gt; 28 C</td><td>10 分钟</td><td>通知护理员、Environment Agent 调温</td></tr>
      </tbody></table></div>
    </section>

    <div v-if="activeSection === 'alerts'" class="eldercare-data-grid">
      <section class="data-section">
        <h2>风险事件 <span>{{ risks.length }}</span></h2>
        <div class="table-scroll">
          <table><thead><tr><th>时间</th><th>等级</th><th>事件</th><th>状态</th></tr></thead>
            <tbody><tr v-for="risk in risks" :key="risk.event_id"><td>{{ formatTime(risk.occurred_at) }}</td><td><span class="severity" :data-level="risk.severity">{{ risk.severity }}</span></td><td>{{ risk.message }}</td><td><button v-if="risk.status === 'open'" class="ack-button" type="button" title="确认风险已处理" @click="acknowledge(risk.event_id)"><Check :size="13" />确认处理</button><span v-else class="handled-label">已处理</span></td></tr><tr v-if="!risks.length"><td colspan="4" class="empty-row">暂无风险事件</td></tr></tbody></table>
        </div>
      </section>
      <section class="data-section">
        <h2>Agent 处置动作 <span>{{ actions.length }}</span></h2>
        <div class="table-scroll">
          <table><thead><tr><th>时间</th><th>子智能体</th><th>动作</th><th>结果</th></tr></thead>
            <tbody><tr v-for="action in actions" :key="action.action_id"><td>{{ formatTime(action.created_at) }}</td><td>{{ action.agent_id }}</td><td>{{ action.action }}</td><td>{{ action.result.success ? '成功' : '失败' }}</td></tr><tr v-if="!actions.length"><td colspan="4" class="empty-row">暂无处置动作</td></tr></tbody></table>
        </div>
      </section>
    </div>

  </main>
</template>

<style scoped>
.eldercare-dashboard { height: calc(100dvh - 50px); overflow: auto; padding: 18px 22px 28px; background: #f6f7f9; }
.eldercare-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.eldercare-heading h1 { margin: 0; font-size: 20px; line-height: 1.3; }
.eldercare-heading p { margin: 3px 0 0; color: var(--text-muted); font-size: 12px; }
.eldercare-toolbar, .reading-form, .scenario-buttons { display: flex; align-items: center; gap: 8px; }
select, input { height: 34px; border: 1px solid var(--line-strong); border-radius: 5px; padding: 0 9px; color: var(--text); background: #fff; }
.eldercare-error { margin-top: 10px; padding: 8px 10px; color: var(--red); background: var(--red-soft); border: 1px solid #efcaca; border-radius: 5px; font-size: 12px; }
.module-tabs { display: flex; align-items: center; gap: 2px; margin-top: 14px; border-bottom: 1px solid var(--line); }.module-tabs button { height: 38px; display: flex; align-items: center; gap: 6px; border: 0; border-bottom: 2px solid transparent; padding: 0 14px; color: var(--text-muted); background: transparent; font-size: 11px; font-weight: 650; }.module-tabs button.active { color: var(--teal); border-bottom-color: var(--teal); }
.resident-strip { min-height: 86px; display: grid; grid-template-columns: minmax(210px, 1.5fr) repeat(5, minmax(110px, 1fr)); align-items: stretch; margin-top: 14px; background: #fff; border: 1px solid var(--line); border-radius: 6px; }
.resident-strip > div { min-width: 0; display: grid; grid-template-columns: 24px 1fr; align-content: center; gap: 4px 7px; padding: 14px; border-left: 1px solid var(--line); }
.resident-strip > div:first-child { border-left: 0; }
.resident-strip svg { grid-row: 1 / 3; align-self: center; color: var(--teal); }
.resident-strip span { color: var(--text-muted); font-size: 10px; }
.resident-strip strong { font-size: 15px; overflow-wrap: anywhere; }
.resident-identity { display: flex !important; align-items: center; gap: 10px !important; }
.resident-identity > span { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 50%; color: #fff; background: #176b64; }
.resident-identity svg { color: #fff; }
.resident-identity div { display: flex; flex-direction: column; gap: 3px; }
.resident-identity small { color: var(--text-muted); font-size: 11px; }
.risk-count strong { color: var(--red); }
.eldercare-control-band { display: grid; grid-template-columns: 1fr 1.4fr; gap: 18px; margin-top: 18px; padding: 16px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.control-section h2, .data-section h2, .trace-section h2, .section-heading h2 { margin: 0 0 10px; font-size: 12px; }.control-section h2 { display: flex; align-items: center; gap: 6px; }
.reading-form select { min-width: 112px; }.reading-form input { min-width: 80px; width: 110px; }.reading-form .unit-input { width: 60px; }
.reading-form button, .scenario-buttons button { height: 34px; display: inline-flex; align-items: center; justify-content: center; gap: 5px; border: 1px solid var(--line-strong); border-radius: 5px; padding: 0 10px; color: var(--text); background: #fff; font-size: 11px; }
.reading-form button { color: #fff; border-color: #08766c; background: var(--teal); }
button:disabled { cursor: not-allowed; opacity: .55; }
.eldercare-data-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 18px; }
.data-section { min-width: 0; }.data-section h2 span { color: var(--text-faint); font-weight: 500; }
.table-scroll { overflow: auto; border: 1px solid var(--line); border-radius: 5px; background: #fff; }
table { width: 100%; border-collapse: collapse; font-size: 11px; } th { color: var(--text-muted); background: #f1f3f5; text-align: left; font-weight: 650; } th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; } tbody tr:last-child td { border-bottom: 0; }.empty-row { height: 68px; color: var(--text-faint); text-align: center; }
.device-management, .health-rules { margin-top: 18px; }.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }.section-heading h2 { margin-bottom: 2px; }.section-heading p { margin: 0; color: var(--text-muted); font-size: 10px; }.section-heading > span { color: var(--text-muted); font-size: 10px; }.device-management td strong, .device-management td small { display: block; }.device-management td small { margin-top: 2px; color: var(--text-faint); font-size: 9px; }.state-cell { max-width: 290px; overflow: hidden; text-overflow: ellipsis; }.device-status { display: inline-flex; align-items: center; gap: 5px; color: var(--text-muted); font-size: 9px; }.device-status i, .monitor-live i { width: 6px; height: 6px; border-radius: 50%; background: #9ca7b1; }.device-status[data-state="online"] i, .monitor-live i { background: #20a265; }.device-status[data-state="offline"] i { background: var(--red); }.device-status[data-state="stale"] i { background: var(--amber); }.monitor-live { display: inline-flex; align-items: center; gap: 5px; color: var(--green) !important; }.ack-button { height: 26px; display: inline-flex; align-items: center; gap: 4px; border: 1px solid #b9d9cc; border-radius: 4px; padding: 0 7px; color: var(--green); background: var(--green-soft); font-size: 9px; }.handled-label { color: var(--text-faint); font-size: 9px; }
.severity { display: inline-block; padding: 2px 5px; border-radius: 4px; font-size: 9px; font-weight: 700; background: var(--amber-soft); color: var(--amber); }.severity[data-level="critical"] { color: var(--red); background: var(--red-soft); }.severity[data-level="high"] { color: #a33f19; background: #fff0e9; }
.trace-section { margin-top: 20px; }.trace-section h2 { display: flex; align-items: center; gap: 8px; }.trace-section code { color: var(--text-faint); font-size: 10px; font-weight: 400; }
.trace-lane { min-height: 92px; display: flex; align-items: stretch; gap: 7px; overflow-x: auto; padding: 10px; border: 1px solid var(--line); border-radius: 5px; background: #fff; }
.trace-lane button { position: relative; min-width: 142px; display: grid; grid-template-columns: 20px 1fr; align-content: center; gap: 4px 6px; border: 1px solid var(--line); border-radius: 5px; padding: 9px; text-align: left; background: #f8fafb; }.trace-lane button + button::before { content: ''; position: absolute; left: -8px; top: 50%; width: 7px; border-top: 1px solid var(--line-strong); }.trace-lane button span { grid-row: 1 / 3; align-self: center; color: var(--teal); font-size: 10px; }.trace-lane button strong { font-size: 10px; }.trace-lane button small { color: var(--text-muted); font-size: 9px; overflow-wrap: anywhere; }.trace-lane p { margin: auto; color: var(--text-faint); font-size: 11px; }
@media (max-width: 1000px) { .resident-strip { grid-template-columns: repeat(3, 1fr); }.resident-strip > div { border-bottom: 1px solid var(--line); }.eldercare-control-band, .eldercare-data-grid { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .eldercare-dashboard { height: calc(100dvh - 48px); padding: 12px; }.eldercare-heading { align-items: flex-start; }.eldercare-heading p { display: none; }.eldercare-toolbar select { max-width: 150px; }.module-tabs { overflow-x: auto; }.module-tabs button { flex: 0 0 auto; padding: 0 9px; }.resident-strip { grid-template-columns: repeat(2, 1fr); }.resident-identity { grid-column: 1 / -1; }.reading-form, .scenario-buttons { flex-wrap: wrap; }.reading-form input { flex: 1; }.scenario-buttons button { flex: 1 1 105px; }.eldercare-data-grid { gap: 14px; } }
</style>
