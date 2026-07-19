<script setup lang="ts">
import { Coins, Gauge, Repeat2, Timer, Wrench } from 'lucide-vue-next'
import { computed } from 'vue'
import type { RuntimeRun } from '../types/agent'
import { duration } from '../utils/trace'

const props = defineProps<{ run: RuntimeRun }>()

const totalDuration = computed(() => (
  props.run.durationMs == null ? '--' : duration(props.run.durationMs)
))

const estimatedCost = computed(() => {
  if (!props.run.usage.pricing_available) return '未配置'
  const value = props.run.usage.estimated_cost
  if (value > 0 && value < 0.0001) return '<¥0.0001'
  return `¥${value.toFixed(4)}`
})
</script>

<template>
  <div class="metric-strip" aria-label="运行指标">
    <div class="metric-item" title="总 Token">
      <Gauge :size="15" />
      <span>Token</span>
      <strong>{{ run.usage.total_tokens.toLocaleString('zh-CN') }}</strong>
    </div>
    <div class="metric-item" title="从 Run 创建到最终结果完成的端到端耗时">
      <Timer :size="15" />
      <span>总耗时</span>
      <strong>{{ totalDuration }}</strong>
    </div>
    <div class="metric-item" title="工具调用次数">
      <Wrench :size="15" />
      <span>工具</span>
      <strong>{{ run.usage.tool_calls }}</strong>
    </div>
    <div class="metric-item" title="Supervisor 结构化决策轮次">
      <Repeat2 :size="15" />
      <span>决策</span>
      <strong>{{ run.supervisorIterations }}</strong>
    </div>
    <div class="metric-item" title="预估调用费用">
      <Coins :size="15" />
      <span>费用</span>
      <strong>{{ estimatedCost }}</strong>
    </div>
  </div>
</template>
