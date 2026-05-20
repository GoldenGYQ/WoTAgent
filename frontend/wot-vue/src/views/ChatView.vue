<script setup lang="ts">
import { onMounted, onUnmounted, ref, nextTick, watch, computed } from 'vue';
import { chatStore } from '../stores/chat';
import { api } from '../api/client';
import MessageItem from '../components/MessageItem.vue';
import DeviceDashboard from '../components/DeviceDashboard.vue';
import EventLog from '../components/EventLog.vue';

const inputMessage = ref('');
const chatScroll = ref<HTMLElement | null>(null);
const statusBadge = ref('● 在线');

// Streaming state
let _currentMsgId: string | null = null;
let _currentThinking = '';
let _activeRun = false;
let _safetyTimer: ReturnType<typeof setTimeout> | null = null;

function genId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function scrollToBottom() {
  nextTick(() => {
    if (chatScroll.value) {
      chatScroll.value.scrollTop = chatScroll.value.scrollHeight;
    }
  });
}

// ── SSE Event handling ──

function handleRuntimeEvent(event: any) {
  if (!event || !event.event_type) return;
  const { event_type, data } = event;

  if (event_type === 'session_started') {
    // Save session_id from server to persist across messages
    if (data.session_id) {
      chatStore.currentSessionId = data.session_id;
    }

  } else if (event_type === 'thinking') {
    let lastMsg = getLastAssistant();
    if (!lastMsg) {
      createAssistantMessage();
      lastMsg = getLastAssistant();
    }
    if (lastMsg) {
      _currentThinking += data.thinking || '';
      lastMsg.thinking = _currentThinking;
    }
    chatStore.addEventLog('🤔 思考', (data.thinking || '').slice(0, 100));

  } else if (event_type === 'assistant_stream') {
    let lastMsg = getLastAssistant();
    if (!lastMsg) {
      createAssistantMessage();
      lastMsg = getLastAssistant();
    }
    if (lastMsg && data.content) {
      lastMsg.content = (lastMsg.content || '') + data.content;
      lastMsg.status = 'pending';
    }

  } else if (event_type === 'plan_update') {
    let lastMsg = getLastAssistant();
    if (!lastMsg) {
      createAssistantMessage();
      lastMsg = getLastAssistant();
    }
    if (lastMsg && data.plan) {
      lastMsg.plan = data.plan;
    }

  } else if (event_type === 'tool_call') {
    let lastMsg = getLastAssistant();
    if (!lastMsg) {
      createAssistantMessage();
      lastMsg = getLastAssistant();
    }
    if (lastMsg) {
      if (!lastMsg.tools) lastMsg.tools = [];
      const existing = lastMsg.tools.find(t => data.id && t.id === data.id);
      if (existing) {
        Object.assign(existing, data);
      } else {
        lastMsg.tools.push({
          id: data.id,
          name: data.name,
          args: data.arguments || {},
          status: 'calling',
        });
      }
    }
    chatStore.addEventLog('🔧 工具', `${data.name} → ${data.arguments?.device || ''}`);

  } else if (event_type === 'tool_result') {
    const lastMsg = chatStore.messages[chatStore.messages.length - 1];
    if (lastMsg?.role === 'assistant' && lastMsg.tools) {
      const tool = lastMsg.tools.find(t => t.name === data.name && t.status === 'calling')
        || lastMsg.tools[lastMsg.tools.length - 1];
      if (tool) {
        tool.status = data.error ? 'error' : 'done';
        tool.result = data.content || data.error || '已完成';
      }
    }

  } else if (event_type === 'assistant_final') {
    let lastMsg = getLastAssistant();
    if (!lastMsg) {
      createAssistantMessage();
      lastMsg = getLastAssistant();
    }
    if (lastMsg) {
      if (data.content) {
        lastMsg.content = data.content;
      }
      lastMsg.status = 'done';
    }
    chatStore.addEventLog('✅ 回复', (data.content || '').slice(0, 150));

  } else if (event_type === 'status') {
    chatStore.addEventLog('📡 状态', data.message || '');

  } else if (event_type === 'turn_complete') {
    _activeRun = false;
    chatStore.isTyping = false;
    if (_safetyTimer) { clearTimeout(_safetyTimer); _safetyTimer = null; }
    setTimeout(() => {
      statusBadge.value = '● 在线';
    }, 500);

  } else if (event_type === 'error') {
    chatStore.addMessage({
      id: genId(),
      role: 'assistant',
      content: `❌ 错误: ${data.error || data.message || '未知错误'}`,
      status: 'error',
    });
    chatStore.isTyping = false;
    _activeRun = false;
    chatStore.addEventLog('❌ 错误', data.error || '');

  } else if (event_type === 'session_update') {
    chatStore.fetchSessions();
  }

  scrollToBottom();
}

function getLastAssistant() {
  const last = chatStore.messages[chatStore.messages.length - 1];
  return last?.role === 'assistant' ? last : null;
}

function createAssistantMessage() {
  const id = genId();
  _currentMsgId = id;
  chatStore.addMessage({
    id,
    role: 'assistant',
    content: '',
    status: 'pending',
  });
}

// ── WebSocket connection ──

let unsubRuntime: (() => void) | null = null;

onMounted(async () => {
  await chatStore.fetchSessions();

  // Connect WebSocket for real-time events
  api.connectWebSocket();
  unsubRuntime = api.onRuntimeEvent(handleRuntimeEvent);

  chatStore.addEventLog('🚀 系统', 'WebSocket 已连接，等待事件...');
});

onUnmounted(() => {
  if (unsubRuntime) unsubRuntime();
  api.disconnectWebSocket();
});

// ── Chat send ──

function createUserMessage(content: string) {
  chatStore.addMessage({
    id: genId(),
    role: 'user',
    content,
  });
}

async function sendMessage() {
  const msg = inputMessage.value.trim();
  if (!msg || _activeRun) return;
  inputMessage.value = '';

  createUserMessage(msg);
  chatStore.addEventLog('👤 用户', msg);

  _activeRun = true;
  _currentThinking = '';
  _currentMsgId = null;
  chatStore.isTyping = true;
  createAssistantMessage();
  chatStore.addEventLog('🚀 会话开始', '正在处理...');

  // Safety timeout: reset typing after 120s
  _safetyTimer = setTimeout(() => {
    _activeRun = false;
    chatStore.isTyping = false;
    chatStore.addEventLog('⏰ 超时', '处理时间过长，已自动重置');
  }, 120000);

  try {
    const res = await api.sendMessage(msg, chatStore.currentSessionId || undefined);
    // Persist session_id from server response
    if (res?.session_id) {
      chatStore.currentSessionId = res.session_id;
    }
    chatStore.addEventLog('📤 已提交', `session=${res?.session_id || 'unknown'}`);
  } catch (err: any) {
    console.error('Send message failed:', err);
    const lastMsg = getLastAssistant();
    if (lastMsg) {
      lastMsg.content = `❌ 发送失败: ${err.message}`;
      lastMsg.status = 'error';
    }
    chatStore.isTyping = false;
    _activeRun = false;
    chatStore.addEventLog('❌ 提交失败', err.message);
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

watch(() => chatStore.messages.length, scrollToBottom);
</script>

<template>
  <div class="app-layout">
    <!-- Sidebar: Sessions -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="logo-area">
          <span class="logo-icon">🏠</span>
          <span class="logo-text">WoTAgent</span>
        </div>
        <button class="new-chat-btn" @click="chatStore.createNewSession">
          <span>＋ 新对话</span>
        </button>
      </div>

      <nav class="session-list">
        <div
          v-for="session in chatStore.sessions"
          :key="session.id"
          class="session-item"
          :class="{ active: session.id === chatStore.currentSessionId }"
          @click="chatStore.selectSession(session.id)"
        >
          <span class="session-icon">💬</span>
          <span class="session-title">{{ session.title || '新对话' }}</span>
          <button
            class="delete-btn"
            @click.stop="chatStore.deleteSession(session.id)"
            title="删除对话"
          >×</button>
        </div>
        <div v-if="chatStore.sessions.length === 0" class="empty-sessions">
          暂无对话
        </div>
      </nav>

      <div class="sidebar-footer">
        <div class="status-badge" :class="{ online: statusBadge === '● 在线' }">
          {{ statusBadge }}
        </div>
      </div>
    </aside>

    <!-- Main Content -->
    <div class="main-content">
      <!-- Header -->
      <header class="main-header">
        <h1>🏠 WoTAgent · 智能家居面板</h1>
        <span class="header-badge" :class="{ online: statusBadge === '● 在线' }">
          {{ statusBadge }}
        </span>
      </header>

      <div class="content-area">
        <!-- Left: Floor Plan Dashboard -->
        <div class="dashboard-panel">
          <DeviceDashboard />
        </div>

        <!-- Right: Chat + Event Log -->
        <div class="right-panel">
          <!-- Chat History -->
          <div class="right-section" style="flex: 3;">
            <h3 class="section-title">💬 对话记录</h3>
            <div class="scroll-area" ref="chatScroll">
              <div v-if="chatStore.messages.length === 0" class="welcome">
                <p>输入指令控制智能家居设备</p>
                <p class="hint">例如：把客厅灯打开、空调调到26度</p>
              </div>
              <div
                v-for="msg in chatStore.messages"
                :key="msg.id"
                class="msg-row"
              >
                <MessageItem :message="msg" />
              </div>
            </div>
          </div>

          <!-- Event Log -->
          <div class="right-section" style="flex: 1.5; border-top: 1px solid #334155;">
            <h3 class="section-title">📋 事件日志</h3>
            <div class="scroll-area">
              <EventLog />
            </div>
          </div>
        </div>
      </div>

      <!-- Chat Input Bar -->
      <footer class="input-bar">
        <input
          v-model="inputMessage"
          placeholder="输入指令，如：把客厅灯打开、空调调到26度…"
          @keydown="handleKeydown"
          :disabled="chatStore.isTyping"
        />
        <button @click="sendMessage" :disabled="!inputMessage.trim() || chatStore.isTyping">
          发送
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
/* ── Layout ── */
.app-layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  background: #0f172a;
  color: #e2e8f0;
  overflow: hidden;
}

/* ── Sidebar ── */
.sidebar {
  width: 220px;
  background: #1e293b;
  border-right: 1px solid #334155;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid #334155;
}

.logo-area {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.logo-icon {
  font-size: 20px;
}

.logo-text {
  font-size: 16px;
  font-weight: 700;
  color: #e2e8f0;
}

.new-chat-btn {
  width: 100%;
  padding: 10px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: background 0.2s;
}

.new-chat-btn:hover {
  background: #2563eb;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  color: #94a3b8;
  transition: all 0.2s;
  margin-bottom: 2px;
}

.session-item:hover {
  background: #0f172a;
  color: #e2e8f0;
}

.session-item.active {
  background: #0f172a;
  color: #22c55e;
  border: 1px solid #334155;
}

.session-icon {
  font-size: 14px;
}

.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.delete-btn {
  opacity: 0;
  background: none;
  border: none;
  color: #64748b;
  font-size: 16px;
  cursor: pointer;
  padding: 0 4px;
}

.session-item:hover .delete-btn {
  opacity: 1;
}

.delete-btn:hover {
  color: #ef4444;
}

.empty-sessions {
  color: #475569;
  text-align: center;
  padding: 20px;
  font-size: 12px;
}

.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid #334155;
}

.status-badge {
  font-size: 12px;
  color: #64748b;
  text-align: center;
}

.status-badge.online {
  color: #22c55e;
}

/* ── Main Content ── */
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.main-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background: linear-gradient(135deg, #1e293b, #0f172a);
  border-bottom: 1px solid #334155;
  flex-shrink: 0;
}

.main-header h1 {
  font-size: 16px;
  font-weight: 600;
}

.header-badge {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 12px;
  background: #334155;
  color: #64748b;
}

.header-badge.online {
  background: #22c55e;
  color: #052e16;
}

/* ── Content Area ── */
.content-area {
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}

.dashboard-panel {
  flex: 1.4;
  overflow-y: auto;
  border-right: 1px solid #334155;
}

.right-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 320px;
}

.right-section {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.section-title {
  font-size: 12px;
  font-weight: 500;
  color: #94a3b8;
  padding: 8px 14px 4px;
  flex-shrink: 0;
  margin: 0;
}

.scroll-area {
  flex: 1;
  overflow-y: auto;
  padding: 0 14px 8px;
}

.welcome {
  text-align: center;
  padding: 40px 20px;
  color: #64748b;
}

.welcome .hint {
  font-size: 12px;
  color: #475569;
  margin-top: 8px;
}

.msg-row {
  padding: 8px 0;
  border-bottom: 1px solid #1e293b;
}

.msg-row:first-child {
  border-top: 1px solid #1e293b;
}

/* ── Scrollbar ── */
:deep(::-webkit-scrollbar) {
  width: 4px;
}
:deep(::-webkit-scrollbar-track) {
  background: transparent;
}
:deep(::-webkit-scrollbar-thumb) {
  background: #334155;
  border-radius: 2px;
}

/* ── Input Bar ── */
.input-bar {
  display: flex;
  gap: 8px;
  padding: 10px 20px;
  background: #1e293b;
  border-top: 1px solid #334155;
  flex-shrink: 0;
}

.input-bar input {
  flex: 1;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 14px;
  color: #e2e8f0;
  font-size: 13px;
  outline: none;
}

.input-bar input:focus {
  border-color: #3b82f6;
}

.input-bar input:disabled {
  opacity: 0.6;
}

.input-bar button {
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 8px;
  padding: 10px 20px;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.2s;
}

.input-bar button:hover {
  background: #2563eb;
}

.input-bar button:disabled {
  background: #334155;
  color: #64748b;
  cursor: not-allowed;
}
</style>
