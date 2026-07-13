<script setup lang="ts">
import { MessageSquare, MessageSquarePlus, Trash2 } from 'lucide-vue-next'
import type { RuntimeConversation } from '../types/agent'

defineProps<{
  conversations: RuntimeConversation[]
  activeConversationId: string | null
}>()

const emit = defineEmits<{
  select: [conversationId: string]
  create: []
  delete: [conversationId: string]
}>()

function relativeTime(value: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))
}
</script>

<template>
  <aside class="task-sidebar">
    <div class="sidebar-action">
      <button class="new-task-button" type="button" @click="emit('create')">
        <MessageSquarePlus :size="17" />
        新会话
      </button>
    </div>
    <div class="sidebar-section-label">
      <MessageSquare :size="14" />
      最近会话
      <span>{{ conversations.length }}</span>
    </div>
    <nav class="task-list" aria-label="最近会话">
      <div
        v-for="conversation in conversations"
        :key="conversation.conversation_id"
        class="conversation-list-row"
        :class="{ active: conversation.conversation_id === activeConversationId }"
      >
        <button
          class="task-list-item"
          type="button"
          @click="emit('select', conversation.conversation_id)"
        >
          <span class="task-list-title">{{ conversation.title }}</span>
          <span class="task-list-meta">
            <span>{{ conversation.turn_count }} 轮</span>
            <time>{{ relativeTime(conversation.updated_at) }}</time>
          </span>
        </button>
        <button
          class="conversation-delete"
          type="button"
          title="删除会话"
          @click="emit('delete', conversation.conversation_id)"
        >
          <Trash2 :size="14" />
        </button>
      </div>
      <div v-if="conversations.length === 0" class="task-list-empty">暂无会话</div>
    </nav>
  </aside>
</template>
