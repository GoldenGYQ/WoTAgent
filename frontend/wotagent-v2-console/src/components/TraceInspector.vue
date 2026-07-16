<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  Bot,
  Braces,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  FileJson2,
  ListChecks,
  Route,
  ShieldCheck,
  TriangleAlert,
  Wrench,
} from 'lucide-vue-next'
import type { RuntimeTask, TraceEvent } from '../types/agent'
import { eventSummary, eventTitle, eventTone, formatTime } from '../utils/trace'

const props = defineProps<{ task: RuntimeTask | null; collapsed?: boolean }>()
const tab = ref<'trace' | 'plan' | 'raw'>('trace')
const expanded = ref(new Set<number>())

const orderedEvents = computed(() => [...(props.task?.events ?? [])].sort((a, b) => a.sequence - b.sequence))

function toggle(event: TraceEvent): void {
  const next = new Set(expanded.value)
  next.has(event.sequence) ? next.delete(event.sequence) : next.add(event.sequence)
  expanded.value = next
}

function iconFor(event: TraceEvent) {
  const tone = eventTone(event)
  if (tone === 'model') return Bot
  if (tone === 'policy') return ShieldCheck
  if (tone === 'tool') return Wrench
  if (tone === 'verify' || tone === 'success') return CheckCircle2
  if (tone === 'danger') return TriangleAlert
  return CircleDot
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2)
}
</script>

<template>
  <aside class="trace-inspector" :class="{ collapsed: collapsed ?? false }">
    <div class="inspector-header">
      <div>
        <span class="eyebrow">Inspector</span>
        <h2>运行轨迹</h2>
      </div>
      <span v-if="task" class="stream-indicator" :data-state="task.streamState">
        <span />{{ task.streamState === 'live' ? '实时' : task.streamState === 'polling' ? '轮询' : '已同步' }}
      </span>
    </div>

    <div v-if="!collapsed" class="segmented-tabs" role="tablist">
      <button type="button" :class="{ active: tab === 'trace' }" @click="tab = 'trace'">
        <Route :size="15" />Trace
      </button>
      <button type="button" :class="{ active: tab === 'plan' }" @click="tab = 'plan'">
        <ListChecks :size="15" />Plan
      </button>
      <button type="button" :class="{ active: tab === 'raw' }" @click="tab = 'raw'">
        <FileJson2 :size="15" />Raw
      </button>
    </div>

    <div v-if="collapsed" class="inspector-empty compact collapsed-placeholder">
      <Route :size="20" />
      <span>Trace</span>
    </div>

    <template v-else>
      <div v-if="!task" class="inspector-empty">
        <Route :size="24" />
        <span>等待任务</span>
      </div>

      <div v-else-if="tab === 'trace'" class="trace-list">
        <button
          v-for="event in orderedEvents"
          :key="event.event_id"
          class="trace-event"
          :data-tone="eventTone(event)"
          type="button"
          @click="toggle(event)"
        >
          <span class="trace-rail">
            <span class="trace-icon"><component :is="iconFor(event)" :size="14" /></span>
          </span>
          <span class="trace-content">
            <span class="trace-title-row">
              <strong>{{ eventTitle(event) }}</strong>
              <time>{{ formatTime(event.timestamp) }}</time>
            </span>
            <span class="trace-summary">{{ eventSummary(event) }}</span>
            <code v-if="expanded.has(event.sequence)" class="trace-payload">{{ pretty(event.data) }}</code>
          </span>
          <ChevronDown v-if="expanded.has(event.sequence)" :size="14" class="trace-chevron" />
          <ChevronRight v-else :size="14" class="trace-chevron" />
        </button>
        <div v-if="orderedEvents.length === 0" class="inspector-empty compact">
          <CircleDot :size="18" />
          <span>等待第一个事件</span>
        </div>
      </div>

      <div v-else-if="tab === 'plan'" class="plan-inspector">
        <template v-if="task.plan">
          <div class="plan-summary-band">
            <span>{{ task.plan.intent }}</span>
            <strong>{{ task.plan.summary || '已生成执行计划' }}</strong>
          </div>
          <ol class="plan-step-list">
            <li v-for="(step, index) in task.plan.steps" :key="step.id">
              <span class="step-number">{{ index + 1 }}</span>
              <div class="step-main">
                <strong>{{ step.action }}</strong>
                <span>{{ step.device_id }}</span>
                <code v-if="Object.keys(step.input).length">{{ pretty(step.input) }}</code>
                <div v-if="step.postconditions.length" class="condition-list">
                  <span v-for="condition in step.postconditions" :key="condition.property">
                    {{ condition.property }} {{ condition.operator }} {{ condition.value }}
                  </span>
                </div>
              </div>
            </li>
          </ol>
        </template>
        <div v-else class="inspector-empty compact"><ListChecks :size="18" /><span>计划尚未生成</span></div>
      </div>

      <div v-else class="raw-inspector">
        <div class="raw-heading"><Braces :size="15" />Task snapshot</div>
        <pre>{{ pretty(task) }}</pre>
      </div>
    </template>
  </aside>
</template>
