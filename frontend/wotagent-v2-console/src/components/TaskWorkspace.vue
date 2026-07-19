<script setup lang="ts">
import { computed } from 'vue'
import {
  Activity,
  Bot,
  BrainCircuit,
  Check,
  CircleAlert,
  Cpu,
  MessageSquareText,
  PanelLeft,
  Route,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from 'lucide-vue-next'
import type {
  AgentRole,
  ConversationTurn,
  RuntimeConversation,
  RuntimeRun,
} from '../types/agent'
import InlineTraceTimeline from './InlineTraceTimeline.vue'
import MarkdownContent from './MarkdownContent.vue'
import MetricStrip from './MetricStrip.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{
  conversation: RuntimeConversation | null
  runs: RuntimeRun[]
  activeRun: RuntimeRun | null
  currentAccountId: string | null
  currentAccountName: string | null
  currentAccountRole: AgentRole
  currentSubjectName: string
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

const guidance = computed(() => {
  const subject = props.currentSubjectName
  if (props.currentAccountRole === 'viewer') {
    return [
      {
        label: '健康状态读取',
        description: `查看与当前主体关联的 ${subject} 健康数据`,
        icon: Activity,
        examples: [
          `查看${subject}今天的健康状态`,
          `查询${subject}最近的生命体征和数据更新时间`,
        ],
      },
      {
        label: '设备与环境查询',
        description: '读取设备状态，不执行控制动作',
        icon: SlidersHorizontal,
        examples: [
          `查询${subject}房间当前温度和湿度`,
          '检查所有灯和空调的当前状态',
        ],
      },
      {
        label: '会话分析',
        description: '基于当前会话整理事实与执行结果',
        icon: MessageSquareText,
        examples: [
          '总结一下我们刚才聊过的内容',
          '把刚才查询到的状态整理成一份简短报告',
        ],
      },
    ]
  }
  return [
    {
      label: '健康与照护协同',
      description: `查询 ${subject} 状态并协调护理任务`,
      icon: Activity,
      examples: [
        `查看${subject}今天的健康状态，如果数据异常就通知护理员`,
        `查询${subject}最近的血氧趋势，并给出需要人工复核的事项`,
      ],
    },
    {
      label: '环境与设备控制',
      description: '执行设备动作，并在操作后验证真实状态',
      icon: SlidersHorizontal,
      examples: [
        `把${subject}房间温度调到 24 度，并确认是否生效`,
        '打开客厅灯并调到 45% 亮度',
      ],
    },
    {
      label: '条件联动任务',
      description: '让 Agent 观察状态、计算条件并选择后续动作',
      icon: ShieldCheck,
      examples: [
        '检查开着的灯数量，奇数就打开空调，偶数就打开加湿器',
        `同时检查${subject}的健康状态和房间环境，有风险时创建护理任务`,
      ],
    },
  ]
})

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
              <MarkdownContent :content="turn.assistant_message" />
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
      <div class="new-conversation-guide">
        <header class="new-conversation-header">
          <span class="empty-mark"><Cpu :size="25" /></span>
          <div>
            <span>新会话</span>
            <h1>向 WoTAgent 下达一个任务</h1>
            <p>选择示例填入输入框，发送后系统会创建 Run 和专业 Task。</p>
          </div>
          <span class="guide-principal">
            <strong>{{ currentAccountId ?? 'JH-1' }}</strong>
            <code>{{ currentAccountRole }}</code>
            <span>关联 {{ currentSubjectName }}</span>
          </span>
        </header>

        <div class="role-scope-note">
          <ShieldCheck :size="16" />
          <span v-if="currentAccountRole === 'viewer'">
            当前是只读观察角色：可以查询健康、环境和设备状态，控制请求会由后端策略拒绝。
          </span>
          <span v-else>
            当前是操作角色：可以查询数据、控制授权设备并创建照护协同任务，高风险动作仍需确认。
          </span>
        </div>

        <div class="guidance-groups">
          <section v-for="group in guidance" :key="group.label" class="guidance-group">
            <header>
              <component :is="group.icon" :size="16" />
              <div>
                <strong>{{ group.label }}</strong>
                <span>{{ group.description }}</span>
              </div>
            </header>
            <div class="guidance-examples">
              <button
                v-for="message in group.examples"
                :key="message"
                type="button"
                @click="emit('example', message)"
              >
                <MessageSquareText :size="14" />
                <span>{{ message }}</span>
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  </main>
</template>

<style scoped>
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
