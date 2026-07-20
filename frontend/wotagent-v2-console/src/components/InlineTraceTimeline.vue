<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Eye,
  GitBranch,
  LoaderCircle,
  RefreshCcw,
  Route,
  ShieldCheck,
  TriangleAlert,
  Wrench,
} from 'lucide-vue-next'
import type { AgentTaskRecord, RuntimeRun, TraceEvent } from '../types/agent'
import { eventSummary, eventTitle, eventTone, formatTime, nodeLabel } from '../utils/trace'

interface TraceGroup {
  id: string
  label: string
  summary: string
  latestSummary: string
  status: string
  task: AgentTaskRecord | null
  events: TraceEvent[]
  stages: TraceStage[]
  primaryCount: number
  detailCount: number
  hiddenCount: number
}

interface TraceStage {
  id: string
  key: string
  label: string
  events: TraceEvent[]
  primaryEvents: TraceEvent[]
  detailEvents: TraceEvent[]
  hiddenCount: number
}

interface EventDetail {
  label: string
  value: unknown
  tone: 'neutral' | 'input' | 'output' | 'verify' | 'danger'
}

type TraceDetailLevel = 'focus' | 'standard' | 'full'

const props = defineProps<{
  run: RuntimeRun
  selected?: boolean
}>()

const ACTIVE_RUN_STATUSES = new Set([
  'accepted',
  'received',
  'running',
  'waiting_confirmation',
])
const TRACE_REVEAL_INTERVAL_MS = 44
const TRACE_DETAIL_LEVEL_KEY = 'wotagent-v2-console.trace-detail-level'
const TERMINAL_RUN_STATUSES = new Set([
  'needs_more_information',
  'completed',
  'partial_success',
  'failed',
  'blocked',
  'cancelled',
])
const expandedEvents = ref(new Set<number>())
const expandedGroups = ref(new Set<string>())
const expandedStageDetails = ref(new Set<string>())
const expandedTimeline = ref(ACTIVE_RUN_STATUSES.has(props.run.status))
const visibleEventIds = ref(new Set<string>())
const traceEndRef = ref<HTMLElement | null>(null)
const traceDetailLevel = ref<TraceDetailLevel>(readTraceDetailLevel())

let revealTimer: ReturnType<typeof window.setTimeout> | null = null
let scrollFrame = 0

const orderedEvents = computed(() => (
  [...props.run.events].sort((a, b) => a.sequence - b.sequence)
))
const visibleEvents = computed(() => (
  orderedEvents.value.filter((event) => visibleEventIds.value.has(event.event_id))
))
const isLive = computed(() => ['accepted', 'received', 'running'].includes(props.run.status))
const traceOverview = computed(() => {
  const groups = traceGroups.value
  const primaryEvents = groups.reduce((sum, group) => sum + group.primaryCount, 0)
  const detailEvents = groups.reduce((sum, group) => sum + group.detailCount, 0)
  const hiddenEvents = groups.reduce((sum, group) => sum + group.hiddenCount, 0)
  const failures = visibleEvents.value.filter((event) => eventTone(event) === 'danger').length
  const modelEvents = visibleEvents.value.filter((event) => event.type.startsWith('model.')).length
  return {
    groupCount: groups.length,
    primaryEvents,
    detailEvents,
    hiddenEvents,
    failures,
    modelEvents,
  }
})
const detailLevelOptions: Array<{ value: TraceDetailLevel; label: string }> = [
  { value: 'focus', label: '精简' },
  { value: 'standard', label: '标准' },
  { value: 'full', label: '全量' },
]
const traceGroups = computed<TraceGroup[]>(() => {
  const groups: TraceGroup[] = []
  const runEvents = visibleEvents.value.filter((event) => !event.task_id)
  if (runEvents.length) {
    const stages = buildStages(runEvents)
    groups.push({
      id: 'run',
      label: 'Run 调度',
      summary: props.run.message,
      latestSummary: groupLatestSummary(runEvents),
      status: props.run.status,
      task: null,
      events: runEvents,
      stages,
      primaryCount: stages.reduce((sum, stage) => sum + stage.primaryEvents.length, 0),
      detailCount: stages.reduce((sum, stage) => sum + stage.detailEvents.length, 0),
      hiddenCount: stages.reduce((sum, stage) => sum + stage.hiddenCount, 0),
    })
  }
  for (const task of props.run.tasks) {
    const taskEvents = visibleEvents.value.filter((event) => event.task_id === task.task_id)
    if (!taskEvents.length) continue
    const stages = buildStages(taskEvents)
    groups.push({
      id: task.task_id,
      label: task.agent_id,
      summary: task.objective,
      latestSummary: groupLatestSummary(taskEvents),
      status: task.status,
      task,
      events: taskEvents,
      stages,
      primaryCount: stages.reduce((sum, stage) => sum + stage.primaryEvents.length, 0),
      detailCount: stages.reduce((sum, stage) => sum + stage.detailEvents.length, 0),
      hiddenCount: stages.reduce((sum, stage) => sum + stage.hiddenCount, 0),
    })
  }
  const knownTasks = new Set(props.run.tasks.map((task) => task.task_id))
  const orphanTaskIds = [
    ...new Set(
      orderedEvents.value
        .map((event) => event.task_id)
        .filter(
          (taskId): taskId is string => (
            typeof taskId === 'string' && !knownTasks.has(taskId)
          ),
        ),
    ),
  ]
  for (const taskId of orphanTaskIds) {
    const taskEvents = visibleEvents.value.filter((event) => event.task_id === taskId)
    if (!taskEvents.length) continue
    const stages = buildStages(taskEvents)
    groups.push({
      id: taskId,
      label: '专业 Agent',
      summary: taskId,
      latestSummary: groupLatestSummary(taskEvents),
      status: 'running',
      task: null,
      events: taskEvents,
      stages,
      primaryCount: stages.reduce((sum, stage) => sum + stage.primaryEvents.length, 0),
      detailCount: stages.reduce((sum, stage) => sum + stage.detailEvents.length, 0),
      hiddenCount: stages.reduce((sum, stage) => sum + stage.hiddenCount, 0),
    })
  }
  return groups
})

watch(
  orderedEvents,
  (events) => {
    syncVisibleEvents(events)
  },
  { immediate: true },
)

watch(
  () => props.run.status,
  (status) => {
    if (ACTIVE_RUN_STATUSES.has(status)) expandedTimeline.value = true
    else if (TERMINAL_RUN_STATUSES.has(status)) expandedTimeline.value = false
  },
  { immediate: true },
)

watch(
  traceGroups,
  (groups) => {
    const next = new Set(expandedGroups.value)
    for (const group of groups) {
      if (
        group.id === 'run'
        || ['pending', 'ready', 'running', 'waiting_confirmation'].includes(group.status)
        || props.selected
      ) {
        next.add(group.id)
      }
    }
    expandedGroups.value = next
  },
  { immediate: true },
)

watch(
  traceDetailLevel,
  (level) => {
    localStorage.setItem(TRACE_DETAIL_LEVEL_KEY, level)
    if (level !== 'standard') expandedStageDetails.value = new Set()
  },
)

watch(
  () => visibleEvents.value.at(-1)?.event_id,
  () => {
    scrollToLatestTrace()
  },
)

watch(
  () => expandedTimeline.value,
  (expanded) => {
    if (expanded) scrollToLatestTrace(false)
  },
)

function toggleEvent(event: TraceEvent): void {
  const next = new Set(expandedEvents.value)
  next.has(event.sequence) ? next.delete(event.sequence) : next.add(event.sequence)
  expandedEvents.value = next
}

function toggleGroup(groupId: string): void {
  const next = new Set(expandedGroups.value)
  next.has(groupId) ? next.delete(groupId) : next.add(groupId)
  expandedGroups.value = next
  if (next.has(groupId)) scrollToLatestTrace(false)
}

function toggleStageDetails(stageId: string): void {
  if (traceDetailLevel.value !== 'standard') return
  const next = new Set(expandedStageDetails.value)
  next.has(stageId) ? next.delete(stageId) : next.add(stageId)
  expandedStageDetails.value = next
}

function iconFor(event: TraceEvent) {
  if (event.type === 'agent.goal.created' || event.type === 'agent.goal.evaluated') return GitBranch
  if (event.type === 'agent.finding.completed' || event.type === 'agent.resource.completed') return Eye
  if (event.type.includes('fallback') || event.type.includes('recovery')) return RefreshCcw
  const tone = eventTone(event)
  if (tone === 'model') return Bot
  if (tone === 'policy') return ShieldCheck
  if (tone === 'tool') return Wrench
  if (tone === 'verify' || tone === 'success') return CheckCircle2
  if (tone === 'danger') return TriangleAlert
  return CircleDot
}

function groupIsLive(group: TraceGroup): boolean {
  return ['pending', 'ready', 'running'].includes(group.status)
    || (group.id === 'run' && isLive.value)
}

function eventIsLive(event: TraceEvent, group: TraceGroup): boolean {
  return groupIsLive(group)
    && group.events.at(-1)?.sequence === event.sequence
    && (
      event.type.endsWith('.started')
      || event.type === 'task.started'
      || event.type === 'task.created'
    )
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    accepted: '已接收',
    received: '已接收',
    pending: '等待中',
    ready: '就绪',
    running: '执行中',
    waiting_confirmation: '待确认',
    needs_more_information: '待补充信息',
    completed: '已完成',
    partial_success: '部分完成',
    failed: '失败',
    blocked: '受阻',
    cancelled: '已取消',
  }
  return labels[status] ?? status
}

function eventDetails(event: TraceEvent): EventDetail[] {
  const data = event.data
  const details: EventDetail[] = []
  if (hasValue(data.prompt_snapshot)) details.push({ label: 'Prompt', value: data.prompt_snapshot, tone: 'neutral' })
  if (hasValue(data.raw_plan)) details.push({ label: '原始 Plan', value: data.raw_plan, tone: 'output' })
  if (hasValue(data.violations)) details.push({ label: '校验错误', value: data.violations, tone: 'danger' })
  if (hasValue(data.repair_reason)) details.push({ label: '修复原因', value: data.repair_reason, tone: 'danger' })
  if (hasValue(data.input)) details.push({ label: '输入', value: data.input, tone: 'input' })
  if (hasValue(data.output)) details.push({ label: '输出', value: data.output, tone: 'output' })
  if (hasValue(data.verification)) details.push({ label: '验证', value: data.verification, tone: 'verify' })
  if (hasValue(data.observed_state)) details.push({ label: '观测状态', value: data.observed_state, tone: 'verify' })
  if (hasValue(data.decisions)) details.push({ label: '策略结果', value: data.decisions, tone: 'verify' })
  if (hasValue(data.plan)) details.push({ label: '执行计划', value: data.plan, tone: 'neutral' })
  if (hasValue(data.finding)) details.push({ label: 'Agent Finding', value: data.finding, tone: 'output' })
  if (hasValue(data.error)) details.push({ label: '错误', value: data.error, tone: 'danger' })
  const represented = new Set([
    'prompt_snapshot', 'raw_plan', 'violations', 'repair_reason',
    'input', 'output', 'verification', 'observed_state', 'decisions', 'plan', 'finding', 'error',
  ])
  const metadata = Object.fromEntries(
    Object.entries(data).filter(([key]) => !represented.has(key)),
  )
  if (Object.keys(metadata).length) {
    details.push({ label: details.length ? '执行元数据' : '事件数据', value: metadata, tone: 'neutral' })
  }
  return details
}

function buildStages(events: TraceEvent[]): TraceStage[] {
  const stages: TraceStage[] = []
  for (const event of events) {
    const descriptor = stageFor(event)
    const previous = stages.at(-1)
    if (previous?.key === descriptor.key) {
      previous.events.push(event)
      continue
    }
    const stage: TraceStage = {
      id: `${descriptor.key}-${event.sequence}`,
      key: descriptor.key,
      label: descriptor.label,
      events: [event],
      primaryEvents: [],
      detailEvents: [],
      hiddenCount: 0,
    }
    stages.push(stage)
  }
  for (const stage of stages) {
    const split = splitStageEvents(stage.events)
    const primaryEvents = [...split.primaryEvents]
    const detailEvents = [...split.detailEvents]
    if (!primaryEvents.length && (detailEvents.length || split.hiddenCount)) {
      const fallback = stage.events[0]
      if (fallback) {
        primaryEvents.push(fallback)
        const fallbackIndex = detailEvents.findIndex((event) => event.event_id === fallback.event_id)
        if (fallbackIndex >= 0) detailEvents.splice(fallbackIndex, 1)
      }
    }
    stage.primaryEvents = primaryEvents
    stage.detailEvents = detailEvents
    stage.hiddenCount = split.hiddenCount
  }
  return stages
}

function splitStageEvents(events: TraceEvent[]): {
  primaryEvents: TraceEvent[]
  detailEvents: TraceEvent[]
  hiddenCount: number
} {
  if (traceDetailLevel.value === 'full') {
    return { primaryEvents: [...events], detailEvents: [], hiddenCount: 0 }
  }
  if (traceDetailLevel.value === 'focus') {
    const primaryEvents = events.filter((event) => isFocusEvent(event))
    return {
      primaryEvents,
      detailEvents: [],
      hiddenCount: Math.max(0, events.length - primaryEvents.length),
    }
  }
  return {
    primaryEvents: events.filter((event) => isPrimaryEvent(event)),
    detailEvents: events.filter((event) => !isPrimaryEvent(event)),
    hiddenCount: 0,
  }
}

function isPrimaryEvent(event: TraceEvent): boolean {
  if (eventTone(event) === 'danger') return true
  if (event.type === 'plan.normalized' || event.type === 'plan.validation.failed') return true
  if (event.type.startsWith('run.') || event.type.startsWith('task.')) return true
  if (event.type === 'supervisor.decision.completed') return true
  if (event.type === 'policy.evaluated') return true
  if (event.type === 'agent.finding.completed') return true
  if (event.type === 'agent.goal.created' || event.type === 'agent.goal.evaluated') return true
  if (event.type.startsWith('agent.recovery') || event.type.startsWith('agent.fallback')) return true
  if (event.type.startsWith('agent.tool') || event.type.startsWith('agent.resource')) return true
  if (event.type.startsWith('verification.')) return true
  if (event.type === 'agent.node.completed' || event.type === 'agent.node.failed') return true
  if (event.type === 'agent.fast_path.selected') return true
  return false
}

function isFocusEvent(event: TraceEvent): boolean {
  if (eventTone(event) === 'danger') return true
  if (event.type === 'plan.normalized' || event.type === 'plan.validation.failed') return true
  if (event.type.startsWith('run.') || event.type.startsWith('task.')) return true
  if (event.type === 'supervisor.decision.completed') return true
  if (event.type === 'policy.evaluated') return true
  if (event.type === 'agent.finding.completed') return true
  if (event.type === 'agent.goal.created' || event.type === 'agent.goal.evaluated') return true
  if (event.type.startsWith('agent.tool') || event.type.startsWith('agent.resource')) return true
  if (event.type.startsWith('verification.')) return true
  if (event.type.startsWith('agent.recovery') || event.type.startsWith('agent.fallback')) return true
  return false
}

function groupLatestSummary(events: TraceEvent[]): string {
  const focus = [...events].reverse().find((event) => {
    const summary = eventSummary(event)
    return isPrimaryEvent(event) && Boolean(summary)
  })
  return focus ? eventSummary(focus) : eventSummary(events.at(-1) as TraceEvent)
}

function stageSummary(stage: TraceStage): string {
  if (stage.hiddenCount) return `${stage.primaryEvents.length} 个关键事件 · 已过滤 ${stage.hiddenCount} 项`
  if (!stage.detailEvents.length) return `${stage.primaryEvents.length} 个关键事件`
  return `${stage.primaryEvents.length} 个关键事件 · ${stage.detailEvents.length} 个技术细节`
}

function eventNodeLabel(event: TraceEvent): string {
  return nodeLabel(event.node)
}

function readTraceDetailLevel(): TraceDetailLevel {
  const value = localStorage.getItem(TRACE_DETAIL_LEVEL_KEY)
  return value === 'focus' || value === 'full' ? value : 'standard'
}

function stageFor(event: TraceEvent): { key: string; label: string } {
  if (
    event.type.startsWith('model.')
    || event.type === 'supervisor.decision.completed'
    || event.type === 'model.reasoning.summary'
  ) {
    return event.task_id
      ? { key: 'planning', label: '规划与模型决策' }
      : { key: 'supervision', label: 'Supervisor 决策' }
  }
  if (
    event.node === 'planner'
    || event.type === 'agent.goal.created'
    || event.type === 'agent.goal.evaluated'
  ) return { key: 'planning', label: '规划与模型决策' }
  if (event.type.startsWith('policy.')) return { key: 'policy', label: '权限与策略检查' }
  if (
    event.type.startsWith('agent.tool')
    || event.type.startsWith('agent.resource')
    || event.node === 'executor'
  ) return { key: 'execution', label: '工具执行' }
  if (
    event.type.startsWith('verification.')
    || event.node === 'verifier'
  ) return { key: 'execution', label: '工具执行与状态验证' }
  if (
    event.type.includes('recovery')
    || event.type.includes('fallback')
  ) return { key: 'recovery', label: '恢复与替代方案' }
  if (
    event.node === 'responder'
    || event.type === 'agent.finding.completed'
    || event.type.endsWith('.completed')
    || event.type.endsWith('.failed')
  ) return { key: 'result', label: '结果与收尾' }
  if (
    event.type === 'task.created'
    || event.type.startsWith('scheduler.')
  ) return { key: 'dispatch', label: 'Task 创建与调度' }
  return event.task_id
    ? { key: 'entry', label: 'Task 入口' }
    : { key: 'lifecycle', label: 'Run 生命周期' }
}

function hasValue(value: unknown): boolean {
  return value !== undefined && value !== null && value !== ''
}

function pretty(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function syncVisibleEvents(events: TraceEvent[]): void {
  const nextIds = new Set(events.map((event) => event.event_id))
  const retained = new Set(
    [...visibleEventIds.value].filter((eventId) => nextIds.has(eventId)),
  )
  visibleEventIds.value = retained
  clearRevealTimer()
  const queue = events.filter((event) => !retained.has(event.event_id))
  if (!queue.length) return

  const revealNext = () => {
    const next = queue.shift()
    if (!next) {
      revealTimer = null
      return
    }
    const visible = new Set(visibleEventIds.value)
    visible.add(next.event_id)
    visibleEventIds.value = visible
    if (!queue.length) {
      revealTimer = null
      return
    }
    revealTimer = window.setTimeout(revealNext, TRACE_REVEAL_INTERVAL_MS)
  }

  revealNext()
}

function clearRevealTimer(): void {
  if (revealTimer !== null) {
    window.clearTimeout(revealTimer)
    revealTimer = null
  }
}

function scrollToLatestTrace(smooth = true): void {
  if (!expandedTimeline.value || (!props.selected && !isLive.value)) return
  if (!visibleEvents.value.length) return
  void nextTick(() => {
    if (scrollFrame) window.cancelAnimationFrame(scrollFrame)
    scrollFrame = window.requestAnimationFrame(() => {
      traceEndRef.value?.scrollIntoView({
        behavior: smooth ? 'smooth' : 'auto',
        block: 'end',
        inline: 'nearest',
      })
      scrollFrame = 0
    })
  })
}

onBeforeUnmount(() => {
  clearRevealTimer()
  if (scrollFrame) window.cancelAnimationFrame(scrollFrame)
})
</script>

<template>
  <section class="inline-trace" :data-live="isLive" aria-live="polite">
    <button
      class="inline-trace-header"
      type="button"
      :aria-expanded="expandedTimeline"
      @click="expandedTimeline = !expandedTimeline"
    >
      <span class="inline-trace-heading">
        <Route :size="15" />
        <strong>运行时间线</strong>
        <span>{{ orderedEvents.length }} 个事件</span>
      </span>
      <span class="inline-trace-state">
        <i v-if="isLive" />
        {{ isLive ? '实时更新' : run.streamState === 'polling' ? '轮询同步' : '执行记录' }}
        <ChevronDown v-if="expandedTimeline" :size="15" />
        <ChevronRight v-else :size="15" />
      </span>
    </button>

    <Transition name="trace-expand">
      <div v-if="expandedTimeline" class="inline-trace-body">
        <div class="trace-toolbar">
          <span class="trace-toolbar-label">细节级别</span>
          <div class="trace-detail-switch" role="tablist" aria-label="trace-detail-level">
            <button
              v-for="option in detailLevelOptions"
              :key="option.value"
              class="trace-detail-switch-button"
              type="button"
              :data-active="traceDetailLevel === option.value"
              @click="traceDetailLevel = option.value"
            >
              {{ option.label }}
            </button>
          </div>
        </div>
        <div class="trace-overview">
          <span class="trace-overview-pill">分组 {{ traceOverview.groupCount }}</span>
          <span class="trace-overview-pill trace-overview-pill-primary">关键 {{ traceOverview.primaryEvents }}</span>
          <span class="trace-overview-pill">细节 {{ traceOverview.detailEvents }}</span>
          <span
            v-if="traceOverview.hiddenEvents"
            class="trace-overview-pill"
          >
            已过滤 {{ traceOverview.hiddenEvents }}
          </span>
          <span
            v-if="traceOverview.failures"
            class="trace-overview-pill trace-overview-pill-danger"
          >
            异常 {{ traceOverview.failures }}
          </span>
          <span class="trace-overview-pill">模型 {{ traceOverview.modelEvents }}</span>
        </div>
        <TransitionGroup name="trace-group" tag="div" class="trace-groups">
          <section
            v-for="(group, groupIndex) in traceGroups"
            :key="group.id"
            class="trace-group"
            :data-status="group.status"
            :data-live="groupIsLive(group)"
          >
            <button
              class="trace-group-header"
              type="button"
              :aria-expanded="expandedGroups.has(group.id)"
              @click="toggleGroup(group.id)"
            >
              <span class="trace-group-order">{{ groupIndex + 1 }}</span>
              <span class="trace-group-status">
                <LoaderCircle v-if="groupIsLive(group)" :size="15" class="trace-running-icon" />
                <CheckCircle2 v-else-if="group.status === 'completed'" :size="15" />
                <TriangleAlert v-else-if="['failed', 'blocked', 'needs_more_information'].includes(group.status)" :size="15" />
                <Route v-else :size="15" />
              </span>
              <span class="trace-group-copy">
                <span>
                  <strong>{{ group.label }}</strong>
                  <em :data-status="group.status">{{ statusLabel(group.status) }}</em>
                </span>
                <small>{{ group.summary }}</small>
                <small v-if="group.latestSummary" class="trace-group-latest">{{ group.latestSummary }}</small>
              </span>
              <span class="trace-group-count">
                <b>{{ group.primaryCount }}</b> 关键
                <span v-if="group.detailCount">· {{ group.detailCount }} 细节</span>
              </span>
              <ChevronDown v-if="expandedGroups.has(group.id)" :size="15" />
              <ChevronRight v-else :size="15" />
            </button>

            <Transition name="trace-expand">
              <div v-if="expandedGroups.has(group.id)" class="trace-group-events">
                <section
                  v-for="stage in group.stages"
                  :key="stage.id"
                  class="trace-stage"
                  :data-stage="stage.key"
                >
                  <header class="trace-stage-header">
                    <span class="trace-stage-branch" />
                    <strong>{{ stage.label }}</strong>
                    <span>{{ stageSummary(stage) }}</span>
                  </header>
                  <TransitionGroup name="trace-event" tag="div" class="trace-stage-events">
                    <button
                      v-for="event in stage.primaryEvents"
                      :key="event.event_id"
                      class="inline-trace-event"
                      :class="{ active: eventIsLive(event, group) }"
                      :data-tone="eventTone(event)"
                      type="button"
                      @click="toggleEvent(event)"
                    >
                      <span class="inline-trace-rail">
                        <span class="inline-trace-icon">
                          <LoaderCircle
                            v-if="eventIsLive(event, group)"
                            :size="13"
                            class="trace-running-icon"
                          />
                          <component :is="iconFor(event)" v-else :size="13" />
                        </span>
                      </span>
                      <span class="inline-trace-copy">
                        <span class="inline-trace-title">
                          <span class="trace-sequence">#{{ event.sequence }}</span>
                          <strong>{{ eventTitle(event) }}</strong>
                          <span v-if="event.node" class="trace-node-label">{{ eventNodeLabel(event) }}</span>
                          <span v-if="event.iteration" class="iteration-label">
                            第 {{ event.iteration }} 轮
                          </span>
                          <time>{{ formatTime(event.timestamp) }}</time>
                        </span>
                        <span v-if="eventSummary(event)" class="inline-trace-summary">
                          {{ eventSummary(event) }}
                        </span>
                        <Transition name="trace-detail">
                          <span
                            v-if="expandedEvents.has(event.sequence)"
                            class="trace-detail-stack"
                          >
                            <span
                              v-for="detail in eventDetails(event)"
                              :key="detail.label"
                              class="trace-detail"
                              :data-tone="detail.tone"
                            >
                              <b>{{ detail.label }}</b>
                              <code>{{ pretty(detail.value) }}</code>
                            </span>
                          </span>
                        </Transition>
                      </span>
                      <ChevronDown v-if="expandedEvents.has(event.sequence)" :size="14" />
                      <ChevronRight v-else :size="14" />
                    </button>
                  </TransitionGroup>
                  <div
                    v-if="traceDetailLevel === 'standard' && stage.detailEvents.length"
                    class="trace-stage-detail-panel"
                  >
                    <button
                      class="trace-stage-detail-toggle"
                      type="button"
                      :aria-expanded="expandedStageDetails.has(stage.id)"
                      @click="toggleStageDetails(stage.id)"
                    >
                      <span>技术细节 {{ stage.detailEvents.length }} 项</span>
                      <ChevronDown v-if="expandedStageDetails.has(stage.id)" :size="14" />
                      <ChevronRight v-else :size="14" />
                    </button>
                    <Transition name="trace-expand">
                      <div
                        v-if="expandedStageDetails.has(stage.id)"
                        class="trace-stage-events trace-stage-events-detail"
                      >
                        <button
                          v-for="event in stage.detailEvents"
                          :key="event.event_id"
                          class="inline-trace-event inline-trace-event-secondary"
                          :data-tone="eventTone(event)"
                          type="button"
                          @click="toggleEvent(event)"
                        >
                          <span class="inline-trace-rail">
                            <span class="inline-trace-icon">
                              <component :is="iconFor(event)" :size="13" />
                            </span>
                          </span>
                          <span class="inline-trace-copy">
                            <span class="inline-trace-title">
                              <span class="trace-sequence">#{{ event.sequence }}</span>
                              <strong>{{ eventTitle(event) }}</strong>
                              <span v-if="event.node" class="trace-node-label">{{ eventNodeLabel(event) }}</span>
                              <time>{{ formatTime(event.timestamp) }}</time>
                            </span>
                            <span v-if="eventSummary(event)" class="inline-trace-summary">
                              {{ eventSummary(event) }}
                            </span>
                            <Transition name="trace-detail">
                              <span
                                v-if="expandedEvents.has(event.sequence)"
                                class="trace-detail-stack"
                              >
                                <span
                                  v-for="detail in eventDetails(event)"
                                  :key="detail.label"
                                  class="trace-detail"
                                  :data-tone="detail.tone"
                                >
                                  <b>{{ detail.label }}</b>
                                  <code>{{ pretty(detail.value) }}</code>
                                </span>
                              </span>
                            </Transition>
                          </span>
                          <ChevronDown v-if="expandedEvents.has(event.sequence)" :size="14" />
                          <ChevronRight v-else :size="14" />
                        </button>
                      </div>
                    </Transition>
                  </div>
                </section>

                <div v-if="groupIsLive(group)" class="trace-waiting">
                  <span class="runtime-spinner" />
                  <span>等待下一项执行事件<span class="waiting-dots" aria-hidden="true">...</span></span>
                </div>
              </div>
            </Transition>
          </section>
        </TransitionGroup>

        <div v-if="traceGroups.length === 0" class="inline-trace-empty">
          <span class="runtime-spinner" />
          <span>等待第一个运行事件</span>
        </div>
        <div ref="traceEndRef" class="trace-scroll-anchor" aria-hidden="true" />
      </div>
    </Transition>
  </section>
</template>
