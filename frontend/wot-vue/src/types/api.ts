export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface Session {
  id: string;
  title: string;
  created_at: number;
}

export type EventType =
  | "assistant_delta"
  | "assistant_final"
  | "thinking"
  | "plan_update"
  | "tool_call"
  | "tool_result"
  | "turn_complete"
  | "status"
  | "error"
  | "provider_error"
  | "session_update";

export interface GGEvent {
  event_type: EventType;
  data: any;
  timestamp: number;
}

// ── WoT-specific event types (SSE events from backend) ──

export type WoTEventType =
  | "wot.session.started"
  | "wot.session.ended"
  | "wot.agent.plan"
  | "wot.agent.thought"
  | "wot.agent.action.started"
  | "wot.agent.action.completed"
  | "wot.agent.action.failed"
  | "wot.agent.response"
  | "wot.agent.error"
  | "wot.perception.rule_triggered";

export interface WoTEvent {
  specversion: string;
  id: string;
  source: string;
  type: WoTEventType;
  subject: string;
  time: string;
  data: any;
  session_id: string;
}

// ── Device types ──

export interface DeviceInfo {
  id: string;
  title: string;
  location: string;
  capabilities: string[];
  actions: string[];
}

export interface DeviceState {
  on?: boolean;
  brightness?: number;
  currentTemperature?: number;
  currentHumidity?: number;
  targetTemp?: number;
  mode?: string;
  speed?: number;
  volume?: number;
  mistLevel?: number;
  gasLevel?: number;
  pm25?: number;
  alarm?: boolean;
  _location?: string;
}

export interface PerceptionRule {
  name: string;
  description: string;
  enabled: boolean;
  condition: {
    device_id: string;
    property: string;
    operator: string;
    threshold: number;
  };
  ready: boolean;
}
