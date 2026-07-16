<script setup lang="ts">
import {
  Bot,
  BrainCircuit,
  Check,
  CircleAlert,
  Cpu,
  MessageSquareText,
  PanelLeft,
  Route,
  ShieldAlert,
  X,
} from 'lucide-vue-next'
import type { ConversationTurn, RuntimeConversation, RuntimeTask } from '../types/agent'
import MetricStrip from './MetricStrip.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{
  conversation: RuntimeConversation | null
  tasks: RuntimeTask[]
  activeTask: RuntimeTask | null
  currentAccountId: string | null
  currentAccountName: string | null
  sidebarCollapsed: boolean
  traceCollapsed: boolean
}>()
const emit = defineEmits<{
  confirm: [taskId: string, approved: boolean]
  example: [message: string]
  selectTask: [taskId: string]
  toggleSidebar: []
  toggleTrace: []
}>()

function taskForTurn(turn: ConversationTurn): RuntimeTask | undefined {
  return props.tasks.find((task) => task.id === turn.task_id)
}

function isActive(task: RuntimeTask | undefined): boolean {
  return Boolean(task && ['accepted', 'received', 'running'].includes(task.status))
}

function currentNode(task: RuntimeTask): string {
  const events = [...task.events].reverse()
  const failed = events.find((event) => event.type === 'agent.node.failed')
  if (failed) return `${failed.node} 执行失败`
  const latest = events.find((event) => event.type === 'agent.node.started')
  return latest ? `${latest.node} 正在处理` : '正在构建会话上下文'
}
</script>

<template>
  <main class="task-workspace">
    <header class="workspace-header">
      <button
        class="icon-button workspace-toggle"
        type="button"
        :title="sidebarCollapsed ? '展开会话列表' : '折叠会话列表'"
        :aria-pressed="!sidebarCollapsed"
        @click="emit('toggleSidebar')"
      >
        <PanelLeft :size="15" />
      </button>
      <div class="task-identity workspace-title">
        <strong class="conversation-title">{{ conversation?.title ?? `欢迎${currentAccountName ?? '某某'}` }}</strong>
        <StatusBadge v-if="activeTask" :status="activeTask.status" />
        <span v-if="activeTask?.thinking" class="thinking-mode"><BrainCircuit :size="13" />深度思考</span>
      </div>
      <button
        class="icon-button workspace-toggle"
        type="button"
        :title="traceCollapsed ? '展开 Trace 面板' : '折叠 Trace 面板'"
        :aria-pressed="!traceCollapsed"
        @click="emit('toggleTrace')"
      >
        <Route :size="15" />
      </button>
    </header>

    <template v-if="conversation">
      <MetricStrip v-if="activeTask" :task="activeTask" />

      <div class="conversation-pane">
        <div
          v-for="turn in conversation.turns"
          :key="turn.turn_id"
          class="turn-group"
          :class="{ selected: activeTask?.id === turn.task_id }"
        >
          <section class="message-block user-message">
            <div class="message-avatar">你</div>
            <div class="message-content">
              <span class="message-role">
                User
                <button
                  v-if="taskForTurn(turn)"
                  type="button"
                  class="turn-task-link"
                  @click="emit('selectTask', turn.task_id)"
                >
                  {{ turn.task_id }}
                </button>
              </span>
              <p>{{ turn.user_message }}</p>
            </div>
          </section>

          <section v-if="isActive(taskForTurn(turn))" class="runtime-band">
            <span class="runtime-spinner" />
            <div>
              <strong>{{ currentNode(taskForTurn(turn)!) }}</strong>
              <span>{{ taskForTurn(turn)?.events.length ?? 0 }} 个 trace 事件</span>
            </div>
          </section>

          <section v-if="taskForTurn(turn)?.confirmation" class="confirmation-band">
            <ShieldAlert :size="21" />
            <div class="confirmation-copy">
              <strong>需要执行确认</strong>
              <p>{{ taskForTurn(turn)?.confirmation?.summary }}</p>
              <span>{{ taskForTurn(turn)?.confirmation?.steps.length }} 个设备操作</span>
            </div>
            <div class="confirmation-actions">
              <button class="icon-text-button secondary" type="button" @click="emit('confirm', turn.task_id, false)">
                <X :size="16" />拒绝
              </button>
              <button class="icon-text-button primary" type="button" @click="emit('confirm', turn.task_id, true)">
                <Check :size="16" />确认执行
              </button>
            </div>
          </section>

          <section v-if="turn.assistant_message" class="message-block assistant-message">
            <div class="message-avatar"><Bot :size="17" /></div>
            <div class="message-content">
              <span class="message-role">WoTAgent</span>
              <p>{{ turn.assistant_message }}</p>
            </div>
          </section>

          <section v-if="taskForTurn(turn)?.error" class="error-band">
            <CircleAlert :size="18" />
            <div><strong>任务异常</strong><span>{{ taskForTurn(turn)?.error }}</span></div>
          </section>
        </div>
        <div v-if="conversation.loading" class="conversation-loading">正在恢复会话</div>
      </div>
    </template>

    <div v-else class="workspace-empty">
      <div class="empty-mark"><Cpu :size="27" /></div>
      <div class="empty-identity">
        <strong>{{ currentAccountId ?? 'JH-1' }}</strong>
        <span>欢迎{{ currentAccountName ?? '某某' }}</span>
      </div>
      <div class="example-grid">
        <button type="button" @click="emit('example', '把客厅灯打开并调到 45% 亮度')">
          <MessageSquareText :size="16" />把客厅灯调到 45%
        </button>
        <button type="button" @click="emit('example', '查询书房风扇当前状态')">
          <MessageSquareText :size="16" />查询书房风扇状态
        </button>
        <button type="button" @click="emit('example', '查看张爷爷今天的健康状态')">
          <MessageSquareText :size="16" />查看张爷爷健康状态
        </button>
        <button type="button" @click="emit('example', '把张爷爷房间温度调到 24 度')">
          <MessageSquareText :size="16" />调节张爷爷房间温度
        </button>
      </div>
    </div>
  </main>
</template>

<style scoped>
.empty-identity {
  display: grid;
  place-items: center;
  gap: 8px;
  margin: 16px 0 4px;
  text-align: center;
}

.empty-identity strong {
  font-size: 42px;
  line-height: 1;
  letter-spacing: 0.08em;
  color: var(--teal);
}

.empty-identity span {
  font-size: 18px;
  font-weight: 600;
  color: var(--text);
}

.workspace-header {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.workspace-title {
  justify-content: center;
  min-width: 0;
}

.workspace-toggle {
  flex: 0 0 auto;
}

.workspace-title .conversation-title {
  text-align: center;
}
</style>
