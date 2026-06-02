<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue';
import { logger } from '../utils/logger';
import type { LogLevel, LogEntry } from '../utils/logger';

const LEVEL_ICONS: Record<LogLevel, string> = {
  debug: "🔍",
  info: "ℹ️",
  warn: "⚠️",
  error: "❌",
};

const LEVEL_OPTIONS: { value: LogLevel | ''; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'info', label: 'ℹ️ Info+' },
  { value: 'warn', label: '⚠️ Warn+' },
  { value: 'error', label: '❌ Error' },
  { value: 'debug', label: '🔍 Debug' },
];

const scrollRef = ref<HTMLElement | null>(null);
const levelFilter = ref<LogLevel | ''>();
const keyword = ref('');
const filteredLogs = ref<LogEntry[]>([]);
const showPanel = ref(false);
const stats = ref({ debug: 0, info: 0, warn: 0, error: 0 });

function refresh() {
  const kw = keyword.value.trim();
  filteredLogs.value = logger.filter(levelFilter.value || undefined, kw || undefined);
  stats.value = logger.getStats();
  nextTick(() => {
    if (scrollRef.value) {
      scrollRef.value.scrollTop = scrollRef.value.scrollHeight;
    }
  });
}

function handleClear() {
  logger.clear();
  refresh();
}

function handleExportJson() {
  const blob = new Blob([logger.exportJson()], { type: 'application/json' });
  downloadBlob(blob, `wot-logs-${Date.now()}.json`);
}

function handleExportText() {
  const blob = new Blob([logger.exportText()], { type: 'text/plain;charset=utf-8' });
  downloadBlob(blob, `wot-logs-${Date.now()}.txt`);
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

let pollTimer: ReturnType<typeof setInterval> | null = null;
onMounted(() => {
  refresh();
  pollTimer = setInterval(refresh, 1500);
});
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});

watch([levelFilter, keyword], () => refresh());
</script>

<template>
  <div class="event-log-container">
    <div class="toolbar">
      <div class="toolbar-row">
        <select v-model="levelFilter" class="filter-select">
          <option v-for="opt in LEVEL_OPTIONS" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
        <input v-model="keyword" placeholder="搜索日志..." class="search-input" />
      </div>
      <div class="toolbar-actions">
        <span class="log-count">共 {{ filteredLogs.length }} 条</span>
        <button class="tool-btn" @click="showPanel = !showPanel" title="管理">⚙️</button>
      </div>
    </div>

    <div v-if="showPanel" class="admin-panel">
      <div class="admin-stats">
        <span class="stat stat-debug">🔍 {{ stats.debug }}</span>
        <span class="stat stat-info">ℹ️ {{ stats.info }}</span>
        <span class="stat stat-warn">⚠️ {{ stats.warn }}</span>
        <span class="stat stat-error">❌ {{ stats.error }}</span>
      </div>
      <div class="admin-actions">
        <button class="action-btn" @click="handleClear">🗑️ 清空</button>
        <button class="action-btn" @click="handleExportJson">📄 导出 JSON</button>
        <button class="action-btn" @click="handleExportText">📝 导出 TXT</button>
      </div>
    </div>

    <div class="event-log" ref="scrollRef">
      <div v-if="filteredLogs.length === 0" class="empty">
        {{ keyword ? '无匹配结果' : '暂无日志' }}
      </div>
      <div v-for="entry in filteredLogs" :key="entry.id" class="event-entry" :class="'level-' + entry.level">
        <span class="entry-level">{{ LEVEL_ICONS[entry.level] || '' }}</span>
        <span class="entry-time">[{{ entry.time }}]</span>
        <span class="entry-label">{{ entry.label }}:</span>
        <span class="entry-text">{{ entry.text }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.event-log-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  font-size: 11px;
  color: #64748b;
  font-family: 'Cascadia Code', 'Consolas', monospace;
}

.toolbar {
  padding: 6px 10px;
  border-bottom: 1px solid #1e293b;
  flex-shrink: 0;
}

.toolbar-row {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;
}

.filter-select {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 4px;
  color: #94a3b8;
  font-size: 10px;
  padding: 2px 4px;
  outline: none;
  cursor: pointer;
}

.search-input {
  flex: 1;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 4px;
  color: #94a3b8;
  font-size: 10px;
  padding: 2px 6px;
  outline: none;
  min-width: 0;
}

.search-input:focus {
  border-color: #3b82f6;
}

.toolbar-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.log-count {
  font-size: 10px;
  color: #475569;
}

.tool-btn {
  background: none;
  border: none;
  color: #64748b;
  font-size: 12px;
  cursor: pointer;
  padding: 0 2px;
}

.tool-btn:hover {
  color: #e2e8f0;
}

.admin-panel {
  padding: 8px 10px;
  background: #1e293b;
  border-bottom: 1px solid #334155;
  flex-shrink: 0;
}

.admin-stats {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.stat {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
}

.stat-debug { color: #94a3b8; }
.stat-info  { color: #38bdf8; }
.stat-warn  { color: #fbbf24; }
.stat-error { color: #ef4444; }

.admin-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.action-btn {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 4px;
  color: #94a3b8;
  font-size: 10px;
  padding: 3px 8px;
  cursor: pointer;
}

.action-btn:hover {
  background: #334155;
  color: #e2e8f0;
}

.event-log {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 8px;
}

.event-entry {
  padding: 3px 0;
  border-bottom: 1px solid #1e293b;
  line-height: 1.5;
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.event-entry:hover {
  color: #94a3b8;
}

.event-entry.level-warn {
  color: #fbbf24;
}

.event-entry.level-error {
  color: #ef4444;
  font-weight: 500;
}

.entry-level {
  flex-shrink: 0;
}

.entry-time {
  flex-shrink: 0;
  color: #475569;
}

.entry-label {
  flex-shrink: 0;
  font-weight: 600;
  color: #94a3b8;
}

.event-entry.level-warn .entry-label,
.event-entry.level-warn .entry-time {
  color: #fbbf24;
}

.event-entry.level-error .entry-label,
.event-entry.level-error .entry-time {
  color: #ef4444;
}

.entry-text {
  word-break: break-all;
}

.empty {
  color: #475569;
  text-align: center;
  padding: 20px 0;
  font-size: 12px;
}
</style>
