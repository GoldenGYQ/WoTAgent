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
  RefreshCcw,
  Route,
  ShieldCheck,
  TriangleAlert,
  Wrench,
} from 'lucide-vue-next'
import type { RuntimeTask, TraceEvent } from '../types/agent'
import { eventSummary, eventTitle, eventTone, formatTime } from '../utils/trace'

const props = defineProps<{
  task: RuntimeTask
  selected?: boolean
}>()

const expandedEvents = ref(new Set<number>())
const expandedTimeline = ref(
  props.selected || ['accepted', 'received', 'running', 'waiting_confirmation'].includes(props.task.status),
)

const orderedEvents = computed(() => (
  [...props.task.events].sort((a, b) => a.sequence - b.sequence)
))
const isLive = computed(() => ['accepted', 'received', 'running'].includes(props.task.status))

watch(
  () => [props.selected, props.task.status] as const,
  ([selected, status]) => {
    if (selected || ['accepted', 'received', 'running', 'waiting_confirmation'].includes(status)) {
      expandedTimeline.value = true
    }
  },
)

function toggleEvent(event: TraceEvent): void {
  const next = new Set(expandedEvents.value)
  next.has(event.sequence) ? next.delete(event.sequence) : next.add(event.sequence)
  expandedEvents.value = next
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

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2)
}
</script>

<template>
  <section class="inline-trace" :data-live="isLive">
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
        {{ isLive ? '实时更新' : task.streamState === 'polling' ? '轮询同步' : '执行记录' }}
        <ChevronDown v-if="expandedTimeline" :size="15" />
        <ChevronRight v-else :size="15" />
      </span>
    </button>

    <div v-if="expandedTimeline" class="inline-trace-body">
      <button
        v-for="event in orderedEvents"
        :key="event.event_id"
        class="inline-trace-event"
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
            <strong>{{ eventTitle(event) }}</strong>
            <span v-if="event.iteration" class="iteration-label">第 {{ event.iteration }} 轮</span>
            <time>{{ formatTime(event.timestamp) }}</time>
          </span>
          <span v-if="eventSummary(event)" class="inline-trace-summary">
            {{ eventSummary(event) }}
          </span>
          <code v-if="expandedEvents.has(event.sequence)" class="inline-trace-payload">
            {{ pretty(event.data) }}
          </code>
        </span>
        <ChevronDown v-if="expandedEvents.has(event.sequence)" :size="14" />
        <ChevronRight v-else :size="14" />
      </button>

      <div v-if="orderedEvents.length === 0" class="inline-trace-empty">
        <span class="runtime-spinner" />
        <span>等待第一个运行事件</span>
      </div>
    </div>
  </section>
</template>
