<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  Boxes,
  BrainCircuit,
  LoaderCircle,
  LogOut,
  MessageSquare,
  Mic,
  PanelLeft,
  RefreshCw,
  SendHorizontal,
  ShieldCheck,
  Square,
  SquareTerminal,
  X,
} from 'lucide-vue-next'
import TaskSidebar from './components/TaskSidebar.vue'
import DomainOperationsCenter from './components/DomainOperationsCenter.vue'
import LoginScreen from './components/LoginScreen.vue'
import TaskWorkspace from './components/TaskWorkspace.vue'
import { useAgentRuntime } from './composables/useAgentRuntime'
import { useVoiceInput } from './composables/useVoiceInput'
import type { LoginAccount } from './types/agent'

const LOGIN_KEY = 'wotagent-v2-console.account'
const accounts: LoginAccount[] = [
  { accountId: 'JH-1', displayName: '王护理员', role: 'operator', subjectId: 'elderly-001', subjectName: '张爷爷' },
  { accountId: 'JH-2', displayName: '赵观察员', role: 'viewer', subjectId: 'elderly-002', subjectName: '李奶奶' },
]

function savedAccount(): LoginAccount | null {
  const accountId = localStorage.getItem(LOGIN_KEY)
  return accounts.find((item) => item.accountId === accountId) ?? null
}

const {
  runs,
  conversations,
  activeConversationId,
  activeConversation,
  activeRunId,
  activeRun,
  thinking,
  thinkingSupported,
  speechSupported,
  speechMaxSeconds,
  backendState,
  submit,
  confirm,
  checkBackend,
  selectConversation,
  newConversation: resetConversation,
  deleteConversation,
  switchIdentity,
  clearIdentity,
  inspectRun,
} = useAgentRuntime()

const draft = ref('')
const submitting = ref(false)
const submitError = ref('')
const drawerOpen = ref(false)
const sidebarCollapsed = ref(false)
const appView = ref<'agent' | 'domains'>('agent')
const currentAccount = ref<LoginAccount | null>(savedAccount())
const composer = ref<HTMLTextAreaElement | null>(null)
const voiceInput = useVoiceInput(
  () => speechSupported.value,
  () => speechMaxSeconds.value,
  (transcript) => {
    draft.value = [draft.value.trim(), transcript].filter(Boolean).join(' ')
    void nextTick(() => composer.value?.focus())
  },
)
const conversationRuns = computed(() => runs.filter(
  (run) => run.conversationId === activeConversationId.value,
))

async function send(): Promise<void> {
  const message = draft.value.trim()
  if (!message || submitting.value || backendState.value === 'offline') return
  submitting.value = true
  submitError.value = ''
  try {
    await submit(message)
    draft.value = ''
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

function chooseRun(runId: string): void {
  activeRunId.value = runId
  drawerOpen.value = false
}

async function chooseConversation(conversationId: string): Promise<void> {
  await selectConversation(conversationId)
  drawerOpen.value = false
}

function newTask(): void {
  resetConversation()
  drawerOpen.value = false
  void nextTick(() => composer.value?.focus())
}

function toggleSidebar(): void {
  sidebarCollapsed.value = !sidebarCollapsed.value
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

async function inspectDomainRun(runId: string): Promise<void> {
  await inspectRun(runId)
  appView.value = 'agent'
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
        <button type="button" :class="{ active: appView === 'domains' }" @click="appView = 'domains'"><Boxes :size="14" />领域运行</button>
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
      :class="{ 'sidebar-collapsed': sidebarCollapsed }"
    >
      <TaskSidebar
        :collapsed="sidebarCollapsed"
        :conversations="conversations"
        :active-conversation-id="activeConversationId"
        @select="chooseConversation"
        @create="newTask"
        @delete="removeConversation"
      />

      <div class="workspace-column">
        <TaskWorkspace
          :conversation="activeConversation"
          :runs="conversationRuns"
          :active-run="activeRun"
          :current-account-id="currentAccount.accountId"
          :current-account-name="currentAccount.displayName"
          :current-account-role="currentAccount.role"
          :current-subject-name="currentAccount.subjectName"
          :sidebar-collapsed="sidebarCollapsed"
          @select-run="chooseRun"
          @confirm="confirm"
          @example="useExample"
          @toggle-sidebar="toggleSidebar"
        />
        <div class="composer-region">
          <div v-if="submitError || voiceInput.error.value" class="composer-error">
            {{ submitError || voiceInput.error.value }}
          </div>
          <div class="composer-shell" :data-recording="voiceInput.recording.value">
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
              <span
                class="principal-context"
                :title="`权限主体 ${currentAccount.accountId}；角色 ${currentAccount.role}；关联资源 ${currentAccount.subjectName}`"
              >
                <ShieldCheck :size="14" />
                <strong>{{ currentAccount.accountId }}</strong>
                <code>{{ currentAccount.role }}</code>
                <span>{{ currentAccount.subjectName }}</span>
              </span>
              <button
                v-if="thinkingSupported"
                class="thinking-toggle"
                type="button"
                role="switch"
                :aria-checked="thinking"
                :class="{ active: thinking }"
                title="切换深度思考"
                @click="thinking = !thinking"
              >
                <BrainCircuit :size="15" />
                <span>深度思考</span>
                <i aria-hidden="true" />
              </button>
              <span v-if="voiceInput.recording.value" class="recording-status">
                <i />{{ voiceInput.elapsedSeconds.value }}s
              </span>
              <button
                v-if="voiceInput.available.value"
                class="voice-button"
                :class="{ recording: voiceInput.recording.value }"
                type="button"
                :title="voiceInput.recording.value ? '停止并转写' : '语音输入'"
                :aria-label="voiceInput.recording.value ? '停止录音并转写' : '开始语音输入'"
                :disabled="voiceInput.transcribing.value || submitting"
                @click="voiceInput.toggleRecording"
              >
                <LoaderCircle v-if="voiceInput.transcribing.value" :size="17" class="spin" />
                <Square v-else-if="voiceInput.recording.value" :size="14" />
                <Mic v-else :size="17" />
              </button>
              <button class="send-button" type="button" title="发送任务" :disabled="!draft.trim() || submitting || backendState === 'offline'" @click="send">
                <LoaderCircle v-if="submitting" :size="17" class="spin" />
                <SendHorizontal v-else :size="17" />
              </button>
            </div>
          </div>
        </div>
      </div>

    </div>

    <DomainOperationsCenter
      v-else
      initial-domain-id="eldercare"
      :initial-subject-id="currentAccount.subjectId"
      :principal-id="currentAccount.accountId"
      @inspect-run="inspectDomainRun"
    />

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
