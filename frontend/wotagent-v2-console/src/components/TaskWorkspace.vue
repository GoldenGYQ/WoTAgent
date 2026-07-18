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
import type { ConversationTurn, RuntimeConversation, RuntimeRun } from '../types/agent'
import InlineTraceTimeline from './InlineTraceTimeline.vue'
import MetricStrip from './MetricStrip.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{
  conversation: RuntimeConversation | null
  runs: RuntimeRun[]
  activeRun: RuntimeRun | null
  currentAccountId: string | null
  currentAccountName: string | null
  sidebarCollapsed: boolean
}>()
const emit = defineEmits<{
  confirm: [runId: string, taskId: string, approved: boolean]
  example: [message: string]
  selectRun: [runId: string]
  toggleSidebar: []
}>()

function runForTurn(turn: ConversationTurn): RuntimeRun | undefined {
  return props.runs.find((run) => run.id === turn.run_id)
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
        <StatusBadge v-if="activeRun" :status="activeRun.status" />
        <span v-if="activeRun?.thinking" class="thinking-mode"><BrainCircuit :size="13" />深度思考</span>
      </div>
      <span v-if="activeRun" class="workspace-trace-status" :data-live="activeRun.streamState === 'live'">
        <i />
        <Route :size="14" />
        {{ activeRun.tasks.length }} 个任务 · {{ activeRun.events.length }} 个事件
      </span>
      <Route v-else :size="15" class="workspace-header-mark" />
    </header>

    <template v-if="conversation">
      <MetricStrip v-if="activeRun" :run="activeRun" />

      <div class="conversation-pane">
        <div
          v-for="turn in conversation.turns"
          :key="turn.turn_id"
          class="turn-group"
          :class="{ selected: activeRun?.id === turn.run_id }"
        >
          <section class="message-block user-message">
            <div class="message-avatar">你</div>
            <div class="message-content">
              <span class="message-role">
                User
                <button
                  v-if="runForTurn(turn)"
                  type="button"
                  class="turn-task-link"
                  @click="emit('selectRun', turn.run_id)"
                >
                  {{ turn.run_id }}
                </button>
              </span>
              <p>{{ turn.user_message }}</p>
            </div>
          </section>

          <InlineTraceTimeline
            v-if="runForTurn(turn)"
            :run="runForTurn(turn)!"
            :selected="activeRun?.id === turn.run_id"
          />

          <section v-if="runForTurn(turn)?.confirmation" class="confirmation-band">
            <ShieldAlert :size="21" />
            <div class="confirmation-copy">
              <strong>需要执行确认</strong>
              <p>{{ runForTurn(turn)?.confirmation?.summary }}</p>
              <span>{{ runForTurn(turn)?.confirmation?.taskId }}</span>
            </div>
            <div class="confirmation-actions">
              <button
                class="icon-text-button secondary"
                type="button"
                @click="emit('confirm', turn.run_id, runForTurn(turn)!.confirmation!.taskId, false)"
              >
                <X :size="16" />拒绝
              </button>
              <button
                class="icon-text-button primary"
                type="button"
                @click="emit('confirm', turn.run_id, runForTurn(turn)!.confirmation!.taskId, true)"
              >
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

          <section v-if="runForTurn(turn)?.error" class="error-band">
            <CircleAlert :size="18" />
            <div><strong>运行异常</strong><span>{{ runForTurn(turn)?.error }}</span></div>
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

.workspace-trace-status {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
  color: var(--text-muted);
  font-size: 10px;
  white-space: nowrap;
}

.workspace-trace-status i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #9da7b1;
}

.workspace-trace-status[data-live="true"] i {
  background: #20a265;
  animation: pulse 1.8s ease-in-out infinite;
}

.workspace-header-mark {
  justify-self: end;
  color: var(--text-faint);
}

@media (max-width: 820px) {
  .workspace-header {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .workspace-title {
    justify-content: flex-start;
  }

  .workspace-title .conversation-title {
    text-align: left;
  }
}
</style>
