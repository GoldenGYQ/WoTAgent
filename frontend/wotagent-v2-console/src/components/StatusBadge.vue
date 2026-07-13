<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const label = computed(() => ({
  accepted: '已接收',
  received: '已接收',
  running: '运行中',
  waiting_confirmation: '待确认',
  succeeded: '已完成',
  responded: '已回复',
  failed: '失败',
  denied: '已拒绝',
  partial_success: '部分完成',
}[props.status] ?? props.status))
</script>

<template>
  <span class="status-badge" :data-status="status">
    <span class="status-dot" />
    {{ label }}
  </span>
</template>
