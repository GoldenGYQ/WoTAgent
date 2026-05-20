<script setup lang="ts">
import { computed } from 'vue';
import { chatStore } from '../stores/chat';

const logs = computed(() => chatStore.eventLogs);
</script>

<template>
  <div class="event-log">
    <div v-if="logs.length === 0" class="empty">暂无事件</div>
    <div
      v-for="(entry, idx) in logs"
      :key="idx"
      class="event-entry"
    >
      [{{ entry.time }}] {{ entry.label }}: {{ entry.text }}
    </div>
  </div>
</template>

<style scoped>
.event-log {
  font-size: 11px;
  color: #64748b;
  font-family: 'Cascadia Code', 'Consolas', monospace;
  padding: 0 14px 8px;
  overflow-y: auto;
  height: 100%;
}

.event-entry {
  padding: 3px 0;
  border-bottom: 1px solid #1e293b;
  line-height: 1.5;
}

.event-entry:hover {
  color: #94a3b8;
}

.empty {
  color: #475569;
  text-align: center;
  padding: 20px 0;
  font-size: 12px;
}
</style>
