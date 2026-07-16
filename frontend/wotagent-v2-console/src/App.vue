<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  Activity,
  BrainCircuit,
  HeartPulse,
  LogOut,
  MessageSquare,
  PanelLeft,
  RefreshCw,
  SendHorizontal,
  SquareTerminal,
  X,
} from 'lucide-vue-next'
import TaskSidebar from './components/TaskSidebar.vue'
import EldercareDashboard from './components/EldercareDashboard.vue'
import LoginScreen from './components/LoginScreen.vue'
import TaskWorkspace from './components/TaskWorkspace.vue'
import TraceInspector from './components/TraceInspector.vue'
import { useAgentRuntime } from './composables/useAgentRuntime'
import type { LoginAccount } from './types/agent'

const LOGIN_KEY = 'wotagent-v2-console.account'
const accounts: LoginAccount[] = [
  { accountId: 'JH-1', displayName: '王护理员', role: 'operator', residentId: 'elderly-001', residentName: '张爷爷' },
  { accountId: 'JH-2', displayName: '赵观察员', role: 'viewer', residentId: 'elderly-002', residentName: '李奶奶' },
]

function savedAccount(): LoginAccount | null {
  const accountId = localStorage.getItem(LOGIN_KEY)
  return accounts.find((item) => item.accountId === accountId) ?? null
}

const {
  tasks,
  conversations,
  activeConversationId,
  activeConversation,
  activeTaskId,
  activeTask,
  role,
  thinking,
  thinkingSupported,
  backendState,
  submit,
  confirm,
  checkBackend,
  selectConversation,
  newConversation: resetConversation,
  deleteConversation,
  switchIdentity,
  clearIdentity,
} = useAgentRuntime()

const draft = ref('')
const submitting = ref(false)
const submitError = ref('')
const mobileView = ref<'workspace' | 'trace'>('workspace')
const drawerOpen = ref(false)
const sidebarCollapsed = ref(false)
const traceCollapsed = ref(false)
const appView = ref<'agent' | 'eldercare'>('agent')
const currentAccount = ref<LoginAccount | null>(savedAccount())
const composer = ref<HTMLTextAreaElement | null>(null)
const conversationTasks = computed(() => tasks.filter(
  (task) => task.conversationId === activeConversationId.value,
))

async function send(): Promise<void> {
  const message = draft.value.trim()
  if (!message || submitting.value || backendState.value === 'offline') return
  submitting.value = true
  submitError.value = ''
  try {
    await submit(message)
    draft.value = ''
    mobileView.value = 'workspace'
  } catch (error) {
    submitError.value = error instanceof Error ? error.message : String(error)
  } finally {
    submitting.value = false
  }
}

function keyboardSubmit(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

function useExample(message: string): void {
  draft.value = message
  void nextTick(() => composer.value?.focus())
}

function chooseTask(taskId: string): void {
  activeTaskId.value = taskId
  drawerOpen.value = false
}

async function chooseConversation(conversationId: string): Promise<void> {
  await selectConversation(conversationId)
  drawerOpen.value = false
}

function newTask(): void {
  resetConversation()
  drawerOpen.value = false
  mobileView.value = 'workspace'
  void nextTick(() => composer.value?.focus())
}

function toggleSidebar(): void {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

function toggleTrace(): void {
  traceCollapsed.value = !traceCollapsed.value
}

async function login(account: LoginAccount): Promise<void> {
  currentAccount.value = account
  localStorage.setItem(LOGIN_KEY, account.accountId)
  await switchIdentity(account.accountId, account.role)
}

function logout(): void {
  clearIdentity()
  currentAccount.value = null
  localStorage.removeItem(LOGIN_KEY)
}

async function removeConversation(conversationId: string): Promise<void> {
  if (!window.confirm('删除该会话及其历史消息？')) return
  await deleteConversation(conversationId)
}

onMounted(async () => {
  await checkBackend()
  if (currentAccount.value) {
    await switchIdentity(currentAccount.value.accountId, currentAccount.value.role)
  }
})
</script>

<template>
  <LoginScreen v-if="!currentAccount" :accounts="accounts" @login="login" />
  <div v-else class="app-shell">
    <header class="app-header">
      <div class="brand-block">
        <button v-if="appView === 'agent'" class="mobile-icon-button" type="button" title="任务列表" @click="drawerOpen = true">
          <PanelLeft :size="18" />
        </button>
        <span class="brand-mark"><SquareTerminal :size="18" /></span>
        <strong>WoTAgent</strong>
        <span class="version-tag">2.0</span>
      </div>
      <nav class="app-view-switch" aria-label="工作视图">
        <button type="button" :class="{ active: appView === 'agent' }" @click="appView = 'agent'"><MessageSquare :size="14" />Agent 会话</button>
        <button type="button" :class="{ active: appView === 'eldercare' }" @click="appView = 'eldercare'"><HeartPulse :size="14" />养老监测</button>
      </nav>
      <div class="header-status">
        <span class="backend-status" :data-state="backendState">
          <span />{{ backendState === 'online' ? '后端在线' : backendState === 'offline' ? '后端离线' : '检测中' }}
        </span>
        <button v-if="backendState === 'offline'" class="icon-button" type="button" title="重新检测" @click="checkBackend">
          <RefreshCw :size="16" />
        </button>
        <span class="account-badge"><strong>{{ currentAccount.accountId }}</strong>{{ currentAccount.displayName }}</span>
        <button class="icon-button" type="button" title="退出登录" @click="logout"><LogOut :size="15" /></button>
      </div>
    </header>

    <div
      v-if="appView === 'agent'"
      class="desktop-layout"
      :class="{ 'sidebar-collapsed': sidebarCollapsed, 'trace-collapsed': traceCollapsed }"
    >
      <TaskSidebar
        :collapsed="sidebarCollapsed"
        :conversations="conversations"
        :active-conversation-id="activeConversationId"
        @select="chooseConversation"
        @create="newTask"
        @delete="removeConversation"
      />

      <div class="workspace-column" :class="{ 'mobile-hidden': mobileView !== 'workspace' }">
        <TaskWorkspace
          :conversation="activeConversation"
          :tasks="conversationTasks"
          :active-task="activeTask"
          :current-account-id="currentAccount.accountId"
          :current-account-name="currentAccount.displayName"
          :sidebar-collapsed="sidebarCollapsed"
          :trace-collapsed="traceCollapsed"
          @confirm="confirm"
          @select-task="chooseTask"
          @example="useExample"
          @toggle-sidebar="toggleSidebar"
          @toggle-trace="toggleTrace"
        />
        <div class="composer-region">
          <div v-if="submitError" class="composer-error">{{ submitError }}</div>
          <div class="composer-shell">
            <textarea
              ref="composer"
              v-model="draft"
              rows="1"
              maxlength="2000"
              placeholder="向 WoTAgent 下达任务"
              aria-label="任务指令"
              @keydown="keyboardSubmit"
            />
            <div class="composer-controls">
              <div class="role-control" aria-label="执行角色">
                <button v-for="item in ['viewer', 'operator', 'admin'] as const" :key="item" type="button" :class="{ active: role === item }" disabled title="角色由登录账号决定">
                  {{ item }}
                </button>
              </div>
              <button
                class="thinking-toggle"
                type="button"
                role="switch"
                :aria-checked="thinking"
                :class="{ active: thinking }"
                :disabled="!thinkingSupported"
                :title="thinkingSupported ? '切换深度思考' : '后端未启用深度思考能力'"
                @click="thinking = !thinking"
              >
                <BrainCircuit :size="15" />
                <span>深度思考</span>
                <i aria-hidden="true" />
              </button>
              <button class="send-button" type="button" title="发送任务" :disabled="!draft.trim() || submitting || backendState === 'offline'" @click="send">
                <Activity v-if="submitting" :size="17" class="spin" />
                <SendHorizontal v-else :size="17" />
              </button>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!traceCollapsed" class="inspector-column" :class="{ 'mobile-hidden': mobileView !== 'trace' }">
        <TraceInspector :task="activeTask" />
      </div>
    </div>

    <EldercareDashboard v-else :initial-resident-id="currentAccount.residentId" />

    <nav v-if="appView === 'agent'" class="mobile-nav" aria-label="移动端视图">
      <button type="button" :class="{ active: mobileView === 'workspace' }" @click="mobileView = 'workspace'">任务</button>
      <button type="button" :class="{ active: mobileView === 'trace' }" @click="mobileView = 'trace'">Trace</button>
    </nav>

    <div v-if="drawerOpen && appView === 'agent'" class="drawer-backdrop" @click="drawerOpen = false">
      <div class="mobile-drawer" @click.stop>
        <div class="drawer-header">
          <strong>任务</strong>
          <button class="icon-button" type="button" title="关闭" @click="drawerOpen = false"><X :size="17" /></button>
        </div>
        <TaskSidebar
          :conversations="conversations"
          :active-conversation-id="activeConversationId"
          @select="chooseConversation"
          @create="newTask"
          @delete="removeConversation"
        />
      </div>
    </div>
  </div>
</template>
