import axios from 'axios';
import type { Session } from '../types/api';

const BASE_URL = '';
const WS_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`;

export class WoTAPI {
  private static instance: WoTAPI;
  private ws: WebSocket | null = null;
  private runtimeHandlers: ((event: any) => void)[] = [];
  private pendingCommands = new Map<
    string,
    { resolve: (data: any) => void; reject: (err: Error) => void; timer: ReturnType<typeof setTimeout> }
  >();
  private wsConnectWaiters: { resolve: () => void; reject: (err: Error) => void }[] = [];
  private connected = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  private constructor() {}

  public static getInstance(): WoTAPI {
    if (!WoTAPI.instance) {
      WoTAPI.instance = new WoTAPI();
    }
    return WoTAPI.instance;
  }

  // ── Session REST endpoints ──

  async getSessions(): Promise<any> {
    const response = await axios.get(`${BASE_URL}/api/sessions`);
    return { sessions: response.data.sessions || [] };
  }

  async getSession(sessionId: string): Promise<any> {
    return { messages: [] };
  }

  async createSession(title?: string): Promise<Session> {
    const response = await axios.post(`${BASE_URL}/api/session`, { role: 'operator' });
    return {
      id: response.data.session_id,
      title: title || '新对话',
      created_at: Date.now(),
    };
  }

  async deleteSession(sessionId: string): Promise<void> {
    await axios.delete(`${BASE_URL}/api/session/${sessionId}`);
  }

  async getSessionMessages(sessionId: string): Promise<any> {
    const response = await axios.get(`${BASE_URL}/api/session/${sessionId}/messages`);
    return response.data;
  }

  // ── WebSocket connection ──

  connectWebSocket() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING)) return;

    // Close stale connection
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        this.ws.close();
      }
    }

    this.ws = new WebSocket(WS_URL);

    this.ws.onopen = () => {
      this.connected = true;
      // Resolve any waiters
      const waiters = [...this.wsConnectWaiters];
      this.wsConnectWaiters = [];
      waiters.forEach((w) => w.resolve());
    };

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data);

        if (msg.type === 'response' && msg.id) {
          // Command response — resolve the pending promise
          const pending = this.pendingCommands.get(msg.id);
          if (pending) {
            clearTimeout(pending.timer);
            this.pendingCommands.delete(msg.id);
            if (msg.success !== false) {
              pending.resolve(msg.data || {});
            } else {
              pending.reject(new Error(msg.message || '命令执行失败'));
            }
          }
        } else if (msg.type === 'event' && msg.event_type) {
          // Real-time agent events — map to runtime format
          const runtimeEvent = this.mapEvent(msg);
          if (runtimeEvent) {
            this.dispatchRuntimeEvent(runtimeEvent);
          }
        }
        // "pong" type is ignored
      } catch (err) {
        console.error('WebSocket message parse error:', err);
      }
    };

    this.ws.onclose = () => {
      this.connected = false;
      // Reject all pending commands
      this.pendingCommands.forEach((pending) => {
        clearTimeout(pending.timer);
        pending.reject(new Error('WebSocket connection closed'));
      });
      this.pendingCommands.clear();
      // Auto-reconnect
      this.reconnectTimer = setTimeout(() => this.connectWebSocket(), 3000);
    };

    this.ws.onerror = () => {
      // onclose will fire after onerror, so no duplicate handling needed
    };
  }

  disconnectWebSocket() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }
    this.connected = false;
    this.pendingCommands.forEach((pending) => {
      clearTimeout(pending.timer);
      pending.reject(new Error('WebSocket disconnected'));
    });
    this.pendingCommands.clear();
  }

  // ── WebSocket command sending ──

  private async waitForOpen(timeoutMs: number = 5000): Promise<void> {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      this.connectWebSocket();
    }
    // Check again after connect attempt
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.wsConnectWaiters = this.wsConnectWaiters.filter((w) => w.resolve !== wrappedResolve);
        reject(new Error('WebSocket connection timeout'));
      }, timeoutMs);

      const wrappedResolve = () => {
        clearTimeout(timer);
        resolve();
      };
      const wrappedReject = (err: Error) => {
        clearTimeout(timer);
        reject(err);
      };

      this.wsConnectWaiters.push({ resolve: wrappedResolve, reject: wrappedReject });
    });
  }

  async sendWSCommand(type: string, payload: any, timeoutMs: number = 15000): Promise<any> {
    await this.waitForOpen();
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket not connected');
    }

    const id = this.genId();
    this.ws.send(JSON.stringify({ type, id, data: payload }));

    return new Promise<any>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingCommands.delete(id);
        reject(new Error(`命令超时: ${type}`));
      }, timeoutMs);
      this.pendingCommands.set(id, { resolve, reject, timer });
    });
  }

  // ── Chat (via WebSocket) ──

  async sendMessage(content: string, sessionId?: string): Promise<any> {
    return this.sendWSCommand('chat', {
      message: content,
      session_id: sessionId || 'web_ui',
    });
  }

  // ── Event subscription ──

  onRuntimeEvent(handler: (event: any) => void): () => void {
    this.runtimeHandlers.push(handler);
    return () => {
      const idx = this.runtimeHandlers.indexOf(handler);
      if (idx >= 0) this.runtimeHandlers.splice(idx, 1);
    };
  }

  // ── Device REST endpoints ──

  async getDevices(): Promise<any> {
    const response = await axios.get(`${BASE_URL}/api/devices`);
    return response.data;
  }

  async getDeviceStates(): Promise<any> {
    const response = await axios.get(`${BASE_URL}/api/perception/state`);
    return response.data;
  }

  async getPerceptionRules(): Promise<any> {
    const response = await axios.get(`${BASE_URL}/api/perception/rules`);
    return response.data;
  }

  async triggerPoll(): Promise<any> {
    const response = await axios.post(`${BASE_URL}/api/perception/poll`);
    return response.data;
  }

  // ── Internal helpers ──

  private genId(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  /** Map a raw WebSocket event to runtime event format */
  private mapEvent(msg: any): any {
    const et = msg.event_type;
    const d = msg.data || {};
    const timestamp = msg.timestamp || Date.now();

    switch (et) {
      case 'wot.session.started':
        return {
          event_type: 'session_started',
          data: { session_id: msg.session_id },
          timestamp,
        };

      case 'wot.session.ended':
        return { event_type: 'turn_complete', data: { session_id: msg.session_id }, timestamp };

      case 'wot.agent.plan':
        // Backend format: {"plan": {"intent": "...", "rationale": "...", "steps": [{"step": "...", "tool": "..."}]}}
        const planObj = d.plan || {};
        const rawSteps = planObj.steps || [];
        const planSteps = rawSteps.map((s: any) => ({
          completed: false,
          text: s.step || s.text || '',
          tool: s.tool || '',
        }));
        return {
          event_type: 'plan_update',
          data: { plan: planSteps },
          timestamp,
        };

      case 'wot.agent.thought':
        return {
          event_type: 'thinking',
          data: { thinking: d.content || '' },
          timestamp,
        };

      case 'wot.agent.token':
        return {
          event_type: 'assistant_stream',
          data: { content: d.content || '' },
          timestamp,
        };

      case 'wot.agent.action.started':
        return {
          event_type: 'tool_call',
          data: {
            id: `${d._tool_name || d.action || 'tool'}-${Date.now()}`,
            name: d._tool_name || d.action || 'device_action',
            arguments: { device: d.device || d.device_id, parameters: d.parameters },
            status: 'calling',
          },
          timestamp,
        };

      case 'wot.agent.action.completed':
        return {
          event_type: 'tool_result',
          data: {
            id: `${d._tool_name || d.action || 'tool'}-${Date.now()}`,
            name: d._tool_name || d.action || 'device_action',
            status: 'done',
            content: d.result || '执行成功',
          },
          timestamp,
        };

      case 'wot.agent.action.failed':
        return {
          event_type: 'tool_result',
          data: {
            name: d._tool_name || d.action || 'device_action',
            status: 'error',
            error: d.error || '执行失败',
          },
          timestamp,
        };

      case 'wot.agent.response':
        return {
          event_type: 'assistant_final',
          data: { content: d.response || '' },
          timestamp,
        };

      case 'wot.agent.error':
        return {
          event_type: 'error',
          data: { error: d.error || '未知错误' },
          timestamp,
        };

      case 'wot.perception.rule_triggered':
        return {
          event_type: 'status',
          data: { message: `⚡ ${d.description || '规则触发'}`, rule: d.rule },
          timestamp,
        };

      default:
        return null;
    }
  }

  private dispatchRuntimeEvent(event: any) {
    this.runtimeHandlers.forEach((handler) => handler(event));
  }
}

export const api = WoTAPI.getInstance();
