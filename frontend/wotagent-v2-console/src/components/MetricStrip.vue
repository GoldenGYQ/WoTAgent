<script setup lang="ts">
import { Coins, Gauge, Repeat2, Timer, Wrench } from 'lucide-vue-next'
import { computed } from 'vue'
import type { RuntimeTask } from '../types/agent'
import { duration } from '../utils/trace'

const props = defineProps<{ task: RuntimeTask }>()

const ttft = computed(() => props.task.usage.ttft_measurements
  ? props.task.usage.ttft_ms_total / props.task.usage.ttft_measurements
  : 0)
</script>

<template>
  <div class="metric-strip" aria-label="任务指标">
    <div class="metric-item" title="总 Token">
      <Gauge :size="15" />
      <span>Token</span>
      <strong>{{ task.usage.total_tokens.toLocaleString('zh-CN') }}</strong>
    </div>
    <div class="metric-item" title="平均首 Token 延迟">
      <Timer :size="15" />
      <span>TTFT</span>
      <strong>{{ duration(ttft) }}</strong>
    </div>
    <div class="metric-item" title="工具调用次数">
      <Wrench :size="15" />
      <span>工具</span>
      <strong>{{ task.usage.tool_calls }}</strong>
    </div>
    <div class="metric-item" title="Agent 循环次数">
      <Repeat2 :size="15" />
      <span>循环</span>
      <strong>{{ task.loopCount }}</strong>
    </div>
    <div class="metric-item" title="预估调用费用">
      <Coins :size="15" />
      <span>费用</span>
      <strong>¥{{ task.usage.estimated_cost.toFixed(4) }}</strong>
    </div>
  </div>
</template>
