<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  AudioLines,
  Boxes,
  BrainCircuit,
  Keyboard,
  LoaderCircle,
  LogOut,
  MessageSquare,
  Mic,
  PanelLeft,
  RefreshCw,
  SendHorizontal,
  ShieldCheck,
  SquareTerminal,
  MapPinned,
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
  deploymentProfiles,
  activeProfileId,
  submit,
  confirm,
  checkBackend,
  selectConversation,
  newConversation: resetConversation,
  deleteConversation,
  switchIdentity,
  clearIdentity,
  inspectRun,
  switchDeploymentProfile,
} = useAgentRuntime()
const activeDeployment = computed(() => deploymentProfiles.value.find(
  (item) => item.profile_id === activeProfileId.value,
) ?? null)
const principalResourceLabel = computed(() => (
  activeDeployment.value?.domain_id === 'eldercare'
    ? currentAccount.value?.subjectName ?? ''
    : activeDeployment.value?.title ?? ''
))

const draft = ref('')
const submitting = ref(false)
const submitError = ref('')
const drawerOpen = ref(false)
const sidebarCollapsed = ref(false)
const appView = ref<'agent' | 'domains'>('agent')
const currentAccount = ref<LoginAccount | null>(savedAccount())
const composer = ref<HTMLTextAreaElement | null>(null)
const inputMode = ref<'text' | 'voice'>('text')
const voiceGestureActive = ref(false)
const voiceCancelIntent = ref(false)
let voiceGestureStartY = 0
let pendingVoiceOutcome: 'stop' | 'cancel' | null = null
const voiceInput = useVoiceInput(
  () => speechSupported.value,
  () => speechMaxSeconds.value,
  (transcript) => {
    draft.value = [draft.value.trim(), transcript].filter(Boolean).join(' ')
    inputMode.value = 'text'
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
  inputMode.value = 'text'
  void nextTick(() => composer.value?.focus())
}

function setInputMode(mode: 'text' | 'voice'): void {
  if (voiceInput.recording.value) voiceInput.cancelRecording()
  voiceGestureActive.value = false
  voiceCancelIntent.value = false
  pendingVoiceOutcome = null
  inputMode.value = mode
  if (mode === 'text') void nextTick(() => composer.value?.focus())
}

async function beginVoiceGesture(event: PointerEvent): Promise<void> {
  if (
    (event.pointerType === 'mouse' && event.button !== 0)
    || voiceInput.transcribing.value
    || submitting.value
  ) return
  event.preventDefault()
  voiceGestureActive.value = true
  voiceCancelIntent.value = false
  voiceGestureStartY = event.clientY
  pendingVoiceOutcome = null
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  await voiceInput.startRecording()
  if (pendingVoiceOutcome === 'cancel') voiceInput.cancelRecording()
  else if (pendingVoiceOutcome === 'stop') voiceInput.stopRecording()
  pendingVoiceOutcome = null
}

function moveVoiceGesture(event: PointerEvent): void {
  if (!voiceGestureActive.value) return
  voiceCancelIntent.value = voiceGestureStartY - event.clientY >= 56
}

function endVoiceGesture(): void {
  if (!voiceGestureActive.value) return
  voiceGestureActive.value = false
  const outcome = voiceCancelIntent.value ? 'cancel' : 'stop'
  if (voiceInput.recording.value) {
    if (outcome === 'cancel') voiceInput.cancelRecording()
    else voiceInput.stopRecording()
  } else {
    pendingVoiceOutcome = outcome
  }
  voiceCancelIntent.value = false
}

function cancelVoiceGesture(): void {
  if (!voiceGestureActive.value && !voiceInput.recording.value) return
  voiceGestureActive.value = false
  voiceCancelIntent.value = false
  if (voiceInput.recording.value) voiceInput.cancelRecording()
  else pendingVoiceOutcome = 'cancel'
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

async function changeDeployment(event: Event): Promise<void> {
  const profileId = (event.target as HTMLSelectElement).value
  submitError.value = ''
  try {
    await switchDeploymentProfile(profileId)
    appView.value = 'agent'
  } catch (error) {
    submitError.value = error instanceof Error ? error.message : String(error)
  }
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
        <span class="version-tag">3.0</span>
      </div>
      <nav class="app-view-switch" aria-label="工作视图">
        <button type="button" :class="{ active: appView === 'agent' }" @click="appView = 'agent'"><MessageSquare :size="14" />Agent 会话</button>
        <button type="button" :class="{ active: appView === 'domains' }" @click="appView = 'domains'"><Boxes :size="14" />领域运行</button>
      </nav>
      <div class="header-status">
        <label class="deployment-selector" title="当前设备与空间场景">
          <MapPinned :size="14" />
          <select :value="activeProfileId" @change="changeDeployment">
            <option v-for="profile in deploymentProfiles" :key="profile.profile_id" :value="profile.profile_id">
              {{ profile.title }}
            </option>
          </select>
        </label>
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
          :current-subject-name="principalResourceLabel"
          :current-domain-id="activeDeployment?.domain_id ?? null"
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
          <div
            class="composer-shell"
            :data-mode="inputMode"
            :data-recording="voiceInput.recording.value"
            :data-canceling="voiceCancelIntent"
          >
            <div class="composer-input-area">
              <textarea
                v-if="inputMode === 'text'"
                ref="composer"
                v-model="draft"
                rows="1"
                maxlength="2000"
                placeholder="输入任务，Enter 发送，Shift + Enter 换行"
                aria-label="任务指令"
                @keydown="keyboardSubmit"
              />
              <button
                v-else
                class="hold-to-talk"
                :class="{
                  recording: voiceInput.recording.value,
                  canceling: voiceCancelIntent,
                  transcribing: voiceInput.transcribing.value,
                }"
                type="button"
                :disabled="voiceInput.transcribing.value || submitting"
                @contextmenu.prevent
                @pointerdown="beginVoiceGesture"
                @pointermove="moveVoiceGesture"
                @pointerup="endVoiceGesture"
                @pointercancel="cancelVoiceGesture"
              >
                <LoaderCircle
                  v-if="voiceInput.transcribing.value"
                  :size="20"
                  class="spin"
                />
                <AudioLines v-else :size="21" />
                <span v-if="voiceInput.transcribing.value">正在转成文字</span>
                <span v-else-if="voiceInput.recording.value && voiceCancelIntent">
                  松开取消
                </span>
                <span v-else-if="voiceInput.recording.value">松开转文字</span>
                <span v-else>按住 说话</span>
                <small v-if="voiceInput.recording.value">
                  <i class="voice-live-dot" />
                  {{ voiceInput.elapsedSeconds.value }}s
                  · {{ voiceCancelIntent ? '已进入取消区域' : '上滑取消' }}
                </small>
                <small v-else-if="voiceInput.transcribing.value">
                  正在识别中英混合语音
                </small>
                <small v-else>支持中英混合，松开后可编辑再发送</small>
                <span v-if="voiceInput.recording.value" class="voice-wave" aria-hidden="true">
                  <i v-for="index in 7" :key="index" />
                </span>
              </button>
            </div>
            <div class="composer-controls">
              <span
                class="principal-context"
                :title="`权限主体 ${currentAccount.accountId}；角色 ${currentAccount.role}；当前范围 ${principalResourceLabel}`"
              >
                <ShieldCheck :size="14" />
                <strong>{{ currentAccount.accountId }}</strong>
                <code>{{ currentAccount.role }}</code>
                <span>{{ principalResourceLabel }}</span>
              </span>
              <div class="composer-actions">
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
                <button
                  v-if="voiceInput.available.value"
                  class="input-mode-button"
                  type="button"
                  :title="inputMode === 'text' ? '切换到按住说话' : '切换到键盘输入'"
                  :aria-label="inputMode === 'text' ? '切换到语音输入' : '切换到文字输入'"
                  :disabled="voiceInput.transcribing.value || submitting"
                  @click="setInputMode(inputMode === 'text' ? 'voice' : 'text')"
                >
                  <Mic v-if="inputMode === 'text'" :size="18" />
                  <Keyboard v-else :size="18" />
                </button>
                <button
                  v-if="inputMode === 'text'"
                  class="send-button"
                  type="button"
                  title="发送任务"
                  :disabled="!draft.trim() || submitting || backendState === 'offline'"
                  @click="send"
                >
                  <LoaderCircle v-if="submitting" :size="17" class="spin" />
                  <SendHorizontal v-else :size="17" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

    </div>

    <DomainOperationsCenter
      v-else
      :key="activeDeployment?.domain_id ?? 'domain'"
      :initial-domain-id="activeDeployment?.domain_id"
      :initial-subject-id="activeDeployment?.domain_id === 'eldercare' ? currentAccount.subjectId : undefined"
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
