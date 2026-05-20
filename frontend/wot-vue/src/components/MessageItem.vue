<script setup lang="ts">
import { computed } from 'vue';
import type { Message } from '../stores/chat';
import ThinkingBox from './ThinkingBox.vue';
import PlanProgress from './PlanProgress.vue';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.css';

const props = defineProps<{
  message: Message;
}>();

const md = new MarkdownIt({
  html: true,
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs" style="background:#1e293b;color:#e2e8f0;padding:12px;border-radius:8px;overflow-x:auto"><code>' +
          hljs.highlight(str, { language: lang }).value + '</code></pre>';
      } catch (__) {}
    }
    return '<pre class="hljs" style="background:#1e293b;color:#e2e8f0;padding:12px;border-radius:8px;overflow-x:auto"><code>' +
      md.utils.escapeHtml(str) + '</code></pre>';
  }
});

const isAssistant = computed(() => props.message.role === 'assistant');
const isUser = computed(() => props.message.role === 'user');

const formattedContent = computed(() => {
  if (!props.message.content) return '';
  const cleaned = props.message.content
    .replace(/<thinking>[\s\S]*?<\/thinking>/gi, '')
    .replace(/<plan>[\s\S]*?<\/plan>/gi, '')
    .trim();
  return md.render(cleaned);
});
</script>

<template>
  <div class="message-container" :class="{ 'user-msg': isUser, 'assistant-msg': isAssistant }">
    <div v-if="isAssistant" class="avatar">🤖</div>

    <div class="message-content">
      <!-- Thinking (collapsible) -->
      <div v-if="isAssistant && message.thinking" class="thinking-wrapper">
        <ThinkingBox :thinking="message.thinking" />
      </div>

      <!-- Plan progress -->
      <div v-if="isAssistant && message.plan && message.plan.length > 0" class="plan-wrapper">
        <PlanProgress :plan="message.plan" />
      </div>

      <!-- Tool calls -->
      <div v-if="message.tools && message.tools.length > 0" class="tools-wrapper">
        <div v-for="(tool, idx) in message.tools" :key="idx" class="tool-item">
          <div class="tool-header">
            <span class="tool-icon">🛠️</span>
            <span class="tool-name">{{ tool.name }}</span>
            <span class="tool-status" :class="tool.status">
              {{ tool.requires_permission ? '⏳ 等待授权' : (tool.status === 'calling' ? '⏳ 执行中...' : (tool.status === 'done' ? '✅ 完成' : '❌ 失败')) }}
            </span>
          </div>
          <div v-if="tool.args || tool.raw_arguments" class="tool-args">
            <code>{{ tool.args ? JSON.stringify(tool.args) : tool.raw_arguments }}</code>
          </div>
          <div v-if="tool.result" class="tool-result">
            <pre>{{ typeof tool.result === 'string' ? tool.result : JSON.stringify(tool.result, null, 2) }}</pre>
          </div>
        </div>
      </div>

      <!-- Bubble text -->
      <div class="bubble">
        <div v-if="message.content" class="text" v-html="formattedContent"></div>
        <div v-else-if="message.status === 'pending'" class="typing-indicator">
          <span></span><span></span><span></span>
        </div>
      </div>
    </div>

    <div v-if="isUser" class="avatar">👤</div>
  </div>
</template>

<style scoped>
.message-container {
  display: flex;
  margin-bottom: 0;
  max-width: 100%;
  gap: 10px;
}

.user-msg {
  flex-direction: row-reverse;
}

.assistant-msg {
  flex-direction: row;
}

.avatar {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
  background: #1e293b;
  border: 1px solid #334155;
}

.message-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: calc(100% - 42px);
  min-width: 0;
}

.bubble {
  padding: 2px 0;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  color: #e2e8f0;
}

.bubble .text :deep(p) {
  margin-bottom: 8px;
  color: #e2e8f0;
}

.bubble .text :deep(p:last-child) {
  margin-bottom: 0;
}

.bubble .text :deep(strong) {
  color: #f1f5f9;
}

.bubble .text :deep(a) {
  color: #60a5fa;
}

.bubble .text :deep(ul),
.bubble .text :deep(ol) {
  padding-left: 20px;
  margin: 8px 0;
  color: #e2e8f0;
}

.bubble .text :deep(li) {
  margin-bottom: 4px;
}

.bubble .text :deep(blockquote) {
  border-left: 3px solid #475569;
  padding-left: 12px;
  color: #94a3b8;
  margin: 8px 0;
}

.bubble .text :deep(h1),
.bubble .text :deep(h2),
.bubble .text :deep(h3),
.bubble .text :deep(h4) {
  color: #f1f5f9;
  margin: 12px 0 6px;
}

.bubble .text :deep(code) {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 0.9em;
  background: #1e293b;
  color: #e2e8f0;
  padding: 2px 6px;
  border-radius: 4px;
}

.bubble .text :deep(pre) {
  margin: 8px 0;
  border: 1px solid #334155;
}

.bubble .text :deep(pre code) {
  background: none;
  padding: 0;
}

.bubble .text :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 13px;
}

.bubble .text :deep(th),
.bubble .text :deep(td) {
  border: 1px solid #334155;
  padding: 6px 10px;
  text-align: left;
}

.bubble .text :deep(thead th) {
  background: #1e293b;
  font-weight: 600;
  color: #f1f5f9;
}

.bubble .text :deep(tbody tr:nth-child(even)) {
  background: #151e2f;
}

.bubble .text :deep(tbody td) {
  color: #e2e8f0;
}

.thinking-wrapper,
.plan-wrapper {
  margin-bottom: 4px;
}

.tools-wrapper {
  margin-top: 4px;
}

.tool-item {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 6px;
  font-size: 13px;
}

.tool-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tool-name {
  font-weight: 600;
  color: #e2e8f0;
}

.tool-status {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}

.tool-status.calling { background: #1e3a5f; color: #60a5fa; }
.tool-status.done { background: #064e3b; color: #34d399; }
.tool-status.error { background: #450a0a; color: #f87171; }

.tool-args,
.tool-result {
  margin-top: 6px;
  background: #0f172a;
  padding: 8px;
  border-radius: 6px;
  overflow-x: auto;
  border: 1px solid #1e293b;
}

.tool-args code,
.tool-result code {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 11px;
  color: #94a3b8;
  word-break: break-all;
}

.tool-result pre {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 11px;
  color: #94a3b8;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.typing-indicator {
  display: flex;
  gap: 5px;
  padding: 8px 0;
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  background: #475569;
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out both;
}

.typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
.typing-indicator span:nth-child(2) { animation-delay: -0.16s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1.0); }
}
</style>
