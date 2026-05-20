<script setup lang="ts">
import { computed, ref } from 'vue';

const isExpanded = ref(false);
const props = defineProps<{
  thinking: string;
}>();

const previewText = computed(() => {
  const text = (props.thinking || '').trim();
  if (text.length <= 120) return text;
  return `${text.slice(0, 120)}...`;
});
</script>

<template>
  <div class="thinking-box" :class="{ expanded: isExpanded }" @click="isExpanded = !isExpanded">
    <div class="header">
      <div class="title">
        <span class="icon">🤔</span>
        <span>思考过程</span>
      </div>
      <div class="arrow" :class="{ rotated: isExpanded }">▼</div>
    </div>
    <div v-if="isExpanded" class="content">
      <pre>{{ props.thinking }}</pre>
    </div>
    <div v-else class="preview">{{ previewText }}</div>
  </div>
</template>

<style scoped>
.thinking-box {
  background: #1a2332;
  border: 1px solid #2d3a50;
  border-radius: 8px;
  padding: 8px 12px;
  margin: 4px 0;
  font-size: 13px;
  color: #94a3b8;
  border-left: 3px solid #475569;
  cursor: pointer;
  transition: all 0.2s ease;
  max-width: 100%;
  overflow: hidden;
}

.thinking-box:hover {
  border-color: #3b82f6;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  user-select: none;
}

.title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
  color: #e2e8f0;
}

.arrow {
  font-size: 10px;
  transition: transform 0.2s;
  color: #64748b;
}

.arrow.rotated {
  transform: rotate(180deg);
}

.content {
  margin-top: 8px;
  min-width: 0;
}

.content pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  line-height: 1.5;
  font-family: inherit;
  font-size: 12px;
  color: #94a3b8;
  max-width: 100%;
}

.preview {
  margin-top: 4px;
  font-style: italic;
  color: #64748b;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  font-size: 12px;
}

.expanded {
  background: #0f172a;
  border-color: #3b82f6;
}
</style>
