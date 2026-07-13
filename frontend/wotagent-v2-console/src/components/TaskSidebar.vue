<script setup lang="ts">
import { CirclePlus, ListTree } from 'lucide-vue-next'
import StatusBadge from './StatusBadge.vue'
import type { RuntimeTask } from '../types/agent'

defineProps<{
  tasks: RuntimeTask[]
  activeTaskId: string | null
}>()

const emit = defineEmits<{
  select: [taskId: string]
  create: []
}>()

function relativeTime(value: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))
}
</script>

<template>
  <aside class="task-sidebar">
    <div class="sidebar-action">
      <button class="new-task-button" type="button" @click="emit('create')">
        <CirclePlus :size="17" />
        新任务
      </button>
    </div>
    <div class="sidebar-section-label">
      <ListTree :size="14" />
      最近任务
      <span>{{ tasks.length }}</span>
    </div>
    <nav class="task-list" aria-label="最近任务">
      <button
        v-for="task in tasks"
        :key="task.id"
        class="task-list-item"
        :class="{ active: task.id === activeTaskId }"
        type="button"
        @click="emit('select', task.id)"
      >
        <span class="task-list-title">{{ task.message }}</span>
        <span class="task-list-meta">
          <StatusBadge :status="task.status" />
          <time>{{ relativeTime(task.updatedAt) }}</time>
        </span>
      </button>
      <div v-if="tasks.length === 0" class="task-list-empty">暂无任务</div>
    </nav>
  </aside>
</template>
