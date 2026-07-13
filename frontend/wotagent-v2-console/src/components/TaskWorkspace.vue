<script setup lang="ts">
import { computed } from 'vue'
import {
  Bot,
  Check,
  CircleAlert,
  Cpu,
  MessageSquareText,
  ShieldAlert,
  X,
} from 'lucide-vue-next'
import type { RuntimeTask } from '../types/agent'
import MetricStrip from './MetricStrip.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ task: RuntimeTask | null }>()
const emit = defineEmits<{
  confirm: [approved: boolean]
  example: [message: string]
}>()

const currentNode = computed(() => {
  if (!props.task) return ''
  const events = [...props.task.events].reverse()
  const failed = events.find((event) => event.type === 'agent.node.failed')
  if (failed) return `${failed.node} 执行失败`
  const latest = events.find((event) => event.type === 'agent.node.started')
  return latest ? `${latest.node} 正在处理` : '正在建立运行上下文'
})

const isActive = computed(() => props.task && ['accepted', 'received', 'running'].includes(props.task.status))
</script>

<template>
  <main class="task-workspace">
    <template v-if="task">
      <header class="workspace-header">
        <div class="task-identity">
          <span class="task-id">{{ task.id }}</span>
          <StatusBadge :status="task.status" />
        </div>
        <span class="session-id">{{ task.sessionId }}</span>
      </header>
      <MetricStrip :task="task" />

      <div class="conversation-pane">
        <section class="message-block user-message">
          <div class="message-avatar">你</div>
          <div>
            <span class="message-role">User · {{ task.role }}</span>
            <p>{{ task.message }}</p>
          </div>
        </section>

        <section v-if="isActive" class="runtime-band">
          <span class="runtime-spinner" />
          <div>
            <strong>{{ currentNode }}</strong>
            <span>{{ task.events.length }} 个 trace 事件</span>
          </div>
        </section>

        <section v-if="task.confirmation" class="confirmation-band">
          <ShieldAlert :size="21" />
          <div class="confirmation-copy">
            <strong>需要执行确认</strong>
            <p>{{ task.confirmation.summary }}</p>
            <span>{{ task.confirmation.steps.length }} 个设备操作</span>
          </div>
          <div class="confirmation-actions">
            <button class="icon-text-button secondary" type="button" @click="emit('confirm', false)">
              <X :size="16" />拒绝
            </button>
            <button class="icon-text-button primary" type="button" @click="emit('confirm', true)">
              <Check :size="16" />确认执行
            </button>
          </div>
        </section>

        <section v-if="task.response" class="message-block assistant-message">
          <div class="message-avatar"><Bot :size="17" /></div>
          <div>
            <span class="message-role">WoTAgent</span>
            <p>{{ task.response }}</p>
          </div>
        </section>

        <section v-if="task.error" class="error-band">
          <CircleAlert :size="18" />
          <div><strong>任务异常</strong><span>{{ task.error }}</span></div>
        </section>
      </div>
    </template>

    <div v-else class="workspace-empty">
      <div class="empty-mark"><Cpu :size="27" /></div>
      <h1>WoTAgent 2.0</h1>
      <p>输入一条设备查询或控制指令</p>
      <div class="example-grid">
        <button type="button" @click="emit('example', '把客厅灯打开并调到 45% 亮度')">
          <MessageSquareText :size="16" />把客厅灯调到 45%
        </button>
        <button type="button" @click="emit('example', '查询书房风扇当前状态')">
          <MessageSquareText :size="16" />查询书房风扇状态
        </button>
      </div>
    </div>
  </main>
</template>
