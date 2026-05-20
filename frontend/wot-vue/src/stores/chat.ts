import { reactive } from 'vue';
import type { Session } from '../types/api';
import { api } from '../api/client';

export interface ToolCall {
  id?: string;
  name: string;
  args: any;
  raw_arguments?: string;
  result?: any;
  status: 'calling' | 'done' | 'error';
  requires_permission?: boolean;
  request_id?: string;
  session_id?: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;
  plan?: { completed: boolean; text: string; tool?: string }[];
  tools?: ToolCall[];
  status?: 'pending' | 'done' | 'error';
}

export const chatStore = reactive({
  sessions: [] as Session[],
  currentSessionId: null as string | null,
  messages: [] as Message[],
  isTyping: false,
  eventLogs: [] as { time: string; label: string; text: string }[],

  async fetchSessions() {
    try {
      const data = await api.getSessions();
      // Normalise backend fields (session_id → id) so the frontend Session type is satisfied
      this.sessions = (data.sessions || []).map((s: any) => ({
        id: s.session_id || s.id,
        title: s.title || s.session_id?.slice(0, 20) || '新对话',
        created_at: s.created_at ? new Date(s.created_at).getTime() : Date.now(),
      }));
      // Auto-select the most recent session so page-refresh doesn't lose continuity
      if (!this.currentSessionId && this.sessions.length > 0) {
        const first = this.sessions[0];
        if (first) await this.selectSession(first.id);
      }
    } catch (err) {
      console.error('Fetch sessions failed:', err);
    }
  },

  async selectSession(sessionId: string) {
    this.currentSessionId = sessionId;
    this.messages = [];
    // Load persisted chat transcript from backend
    try {
      const data = await api.getSessionMessages(sessionId);
      const entries = data.messages || [];
      for (const entry of entries) {
        if (entry.event === 'user_message' || entry.role === 'user') {
          this.messages.push({
            id: `msg-${entry.ts || Date.now()}-${this.messages.length}`,
            role: 'user',
            content: entry.content || '',
            status: 'done',
          });
        } else if (entry.event === 'assistant_response' || entry.role === 'assistant') {
          this.messages.push({
            id: `msg-${entry.ts || Date.now()}-${this.messages.length}`,
            role: 'assistant',
            content: entry.content || '',
            status: 'done',
          });
        }
      }
    } catch (err) {
      console.error('Load session messages failed:', err);
    }
  },

  async createNewSession() {
    try {
      const session = await api.createSession();
      this.sessions.unshift(session);
      this.currentSessionId = session.id;
      this.messages = [];
    } catch (err) {
      console.error('Create session failed:', err);
    }
  },

  async deleteSession(sessionId: string) {
    try {
      await api.deleteSession(sessionId);
      this.sessions = this.sessions.filter(s => s.id !== sessionId);
      if (this.currentSessionId === sessionId) {
        if (this.sessions.length > 0) {
          const first = this.sessions[0];
          if (first) {
            this.selectSession(first.id);
          }
        } else {
          this.currentSessionId = null;
          this.messages = [];
        }
      }
    } catch (err) {
      console.error('Delete session failed:', err);
    }
  },

  addMessage(message: Message) {
    this.messages.push(message);
  },

  updateLastMessage(updates: Partial<Message>) {
    if (this.messages.length > 0) {
      const last = this.messages[this.messages.length - 1];
      if (last) {
        Object.assign(last, updates);
      }
    }
  },

  addEventLog(label: string, text: string) {
    this.eventLogs.push({
      time: new Date().toLocaleTimeString(),
      label,
      text: String(text).slice(0, 300),
    });
    if (this.eventLogs.length > 200) {
      this.eventLogs.shift();
    }
  },
});
