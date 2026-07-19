<script setup lang="ts">
import { computed, ref, watch } from 'vue'
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
import { eventSummary, eventTitle, eventTone, formatTime } from '../utils/trace'

interface TraceGroup {
  id: string
  label: string
  summary: string
  status: string
  task: AgentTaskRecord | null
  events: TraceEvent[]
  stages: TraceStage[]
}

interface TraceStage {
  id: string
  key: string
  label: string
  events: TraceEvent[]
}

interface EventDetail {
  label: string
  value: unknown
  tone: 'neutral' | 'input' | 'output' | 'verify' | 'danger'
}

const props = defineProps<{
  run: RuntimeRun
  selected?: boolean
}>()

const expandedEvents = ref(new Set<number>())
const expandedGroups = ref(new Set<string>())
const expandedTimeline = ref(
  props.selected || ['accepted', 'received', 'running', 'waiting_confirmation'].includes(props.run.status),
)

const orderedEvents = computed(() => (
  [...props.run.events].sort((a, b) => a.sequence - b.sequence)
))
const isLive = computed(() => ['accepted', 'received', 'running'].includes(props.run.status))
const traceGroups = computed<TraceGroup[]>(() => {
  const groups: TraceGroup[] = []
  const runEvents = orderedEvents.value.filter((event) => !event.task_id)
  if (runEvents.length) {
    groups.push({
      id: 'run',
      label: 'Run 调度',
      summary: props.run.message,
      status: props.run.status,
      task: null,
      events: runEvents,
      stages: buildStages(runEvents),
    })
  }
  for (const task of props.run.tasks) {
    const taskEvents = orderedEvents.value.filter((event) => event.task_id === task.task_id)
    groups.push({
      id: task.task_id,
      label: task.agent_id,
      summary: task.objective,
      status: task.status,
      task,
      events: taskEvents,
      stages: buildStages(taskEvents),
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
    const taskEvents = orderedEvents.value.filter((event) => event.task_id === taskId)
    groups.push({
      id: taskId,
      label: '专业 Agent',
      summary: taskId,
      status: 'running',
      task: null,
      events: taskEvents,
      stages: buildStages(taskEvents),
    })
  }
  return groups
})

watch(
  () => [props.selected, props.run.status] as const,
  ([selected, status]) => {
    if (selected || ['accepted', 'received', 'running', 'waiting_confirmation'].includes(status)) {
      expandedTimeline.value = true
    }
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

function toggleEvent(event: TraceEvent): void {
  const next = new Set(expandedEvents.value)
  next.has(event.sequence) ? next.delete(event.sequence) : next.add(event.sequence)
  expandedEvents.value = next
}

function toggleGroup(groupId: string): void {
  const next = new Set(expandedGroups.value)
  next.has(groupId) ? next.delete(groupId) : next.add(groupId)
  expandedGroups.value = next
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
  if (hasValue(data.input)) details.push({ label: '输入', value: data.input, tone: 'input' })
  if (hasValue(data.output)) details.push({ label: '输出', value: data.output, tone: 'output' })
  if (hasValue(data.verification)) details.push({ label: '验证', value: data.verification, tone: 'verify' })
  if (hasValue(data.observed_state)) details.push({ label: '观测状态', value: data.observed_state, tone: 'verify' })
  if (hasValue(data.decisions)) details.push({ label: '策略结果', value: data.decisions, tone: 'verify' })
  if (hasValue(data.plan)) details.push({ label: '执行计划', value: data.plan, tone: 'neutral' })
  if (hasValue(data.finding)) details.push({ label: 'Agent Finding', value: data.finding, tone: 'output' })
  if (hasValue(data.error)) details.push({ label: '错误', value: data.error, tone: 'danger' })
  const represented = new Set([
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
    stages.push({
      id: `${descriptor.key}-${event.sequence}`,
      key: descriptor.key,
      label: descriptor.label,
      events: [event],
    })
  }
  return stages
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
        <TransitionGroup name="trace-group" tag="div" class="trace-groups">
          <section
            v-for="group in traceGroups"
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
              <span class="trace-group-status">
                <LoaderCircle v-if="groupIsLive(group)" :size="15" class="trace-running-icon" />
                <CheckCircle2 v-else-if="group.status === 'completed'" :size="15" />
                <TriangleAlert v-else-if="['failed', 'blocked'].includes(group.status)" :size="15" />
                <Route v-else :size="15" />
              </span>
              <span class="trace-group-copy">
                <span>
                  <strong>{{ group.label }}</strong>
                  <em :data-status="group.status">{{ statusLabel(group.status) }}</em>
                </span>
                <small>{{ group.summary }}</small>
              </span>
              <span class="trace-group-count">{{ group.events.length }} 项</span>
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
                    <span>{{ stage.events.length }} 项</span>
                  </header>
                  <TransitionGroup name="trace-event" tag="div" class="trace-stage-events">
                    <button
                      v-for="event in stage.events"
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
                          <strong>{{ eventTitle(event) }}</strong>
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
      </div>
    </Transition>
  </section>
</template>
