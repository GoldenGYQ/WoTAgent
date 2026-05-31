# 事件系统

## CloudEvents 格式

所有 Agent 事件遵循 [CloudEvents 1.0](https://cloudevents.io/) 规范：

```json
{
  "specversion": "1.0",
  "id": "a1b2c3d4-e5f6-...",
  "source": "wotagent/core/pipeline",
  "type": "wot.agent.plan",
  "subject": "session/sess_abc123",
  "time": "2026-05-19T10:00:00Z",
  "session_id": "sess_abc123",
  "data": { "plan": {"intent": "control", ...} }
}
```

## 事件类型

| 类型 | 生产者 | 何时触发 |
|------|--------|---------|
| `wot.session.started` | `invoke_agent_stream` / `execute_plan_directly` | Agent 调用开始 |
| `wot.session.ended` | `invoke_agent_stream` / `execute_plan_directly` | Agent 调用结束 |
| `wot.agent.plan` | 意图分类器 | 产生结构化意图 + 路由计划 |
| `wot.agent.thought` | LLM | DeepSeek reasoning_content（思考模式） |
| `wot.agent.token` | 流水线 | 响应文本片段（流式） |
| `wot.agent.observation` | (保留) | 可选的验证/观察事件 |
| `wot.agent.action.started` | 执行器 | 工具调用发起 |
| `wot.agent.action.completed` | 执行器 | 工具调用成功 |
| `wot.agent.action.failed` | 执行器 | 工具调用出错 |
| `wot.agent.response` | 流水线 | 最终响应文本 |
| `wot.agent.error` | 流水线 | Agent 调用出错 |
| `wot.device.state_changed` | IoT 工具 | 设备状态变化（模拟） |
| `wot.device.discovered` | WoT | 发现新设备 |
| `wot.perception.state` | 感知引擎 | (保留) 定期环境快照 |
| `wot.perception.rule_triggered` | 感知引擎 | 规则条件匹配并触发 |
| `wot.system.log` | 各类来源 | 转发到总线的日志消息 |

## 事件总线

`EventBus` 是一个基于内存的发布/订阅系统：

```python
bus = get_bus()

# 订阅
unsub = bus.subscribe(my_callback, event_type="wot.agent.plan")

# 发送
await bus.emit(Event(source="...", type="wot.agent.plan", data={...}))

# 取消订阅
unsub()

# 历史回放
for evt in bus.history(start_cursor=42):
    print(evt)
```

- `max_history`（默认 1000）限制保留的事件数量
- 每个事件获得一个游标，用于历史回放

## 消费者：日志

`install_event_logger()` 订阅所有 `wot.*` 事件并写入 `wotagent.log`。仅记录高级语义事件（无 `on_chain_start` 内部噪声）。

## 消费者：SessionTranscript

每次 Agent 调用可生成双 JSONL 转录文件：

| 文件 | 内容 |
|------|------|
| `data/memory/{session_id}.full.jsonl` | 所有流水线事件 + LLM I/O |
| `data/memory/{session_id}.chat.jsonl` | 仅用户/助手轮次 |

详见 [PIPELINE.md](PIPELINE.zh-CN.md#会话转录)。

## 消费者：WebSocket（主要）

FastAPI 端点 `ws://localhost:8000/ws` 双向流式传输事件。
服务器将所有 `EventBus` 事件转发到 WebSocket 作为 JSON 消息。

### WebSocket 协议

**客户端 → 服务器命令：**

| 类型 | 数据 | 描述 |
|------|------|------|
| `chat` | `{"message": "...", "session_id": "..."}` | 向 Agent 发送聊天消息 |
| `ping` | — | 保活，服务器回复 `pong` |

**服务器 → 客户端消息：**

| 类型 | 字段 | 描述 |
|------|------|------|
| `event` | `event_type`, `data`, `session_id`, `timestamp` | 从 EventBus 转发的实时 Agent 事件 |
| `response` | `id`, `success`, `data` | 确认 `chat` 命令，提供 `session_id` |
| `pong` | `id` | 对 `ping` 的响应 |
| `error` | `id`, `message` | 命令的错误响应 |

**示例聊天流程：**
```json
→ {"type": "chat", "id": "cmd-001", "data": {"message": "开灯", "session_id": "sess_xxx"}}
← {"type": "response", "id": "cmd-001", "success": true, "data": {"session_id": "sess_xxx"}}
← {"type": "event", "event_type": "wot.agent.plan", "data": {...}, "session_id": "sess_xxx", "timestamp": ...}
← {"type": "event", "event_type": "wot.agent.action.started", "data": {...}, ...}
← {"type": "event", "event_type": "wot.agent.action.completed", "data": {...}, ...}
← {"type": "event", "event_type": "wot.agent.response", "data": {...}, ...}
← {"type": "event", "event_type": "wot.session.ended", "data": {}, ...}
```

WebSocket 是 Vue 前端的**主要**传输方式。

## 事件流图

### 用户触发路径

```
用户消息
     │
     ▼
invoke_agent_stream()
     │
     ├── emit wot.session.started
     │
     ├── emit wot.agent.plan        ──┐
     │                                │
     ├── executor.astream_events()    ├── WebSocket → 前端
     │   ├── emit action.started     │
     │   ├── emit action.completed   │
     │   └── emit action.failed      │
     │                               ├── 日志 → wotagent.log
     ├── emit wot.agent.response     │
     │                              ├── 转录 → .jsonl
     ├── emit wot.session.ended     ──┘
     │
     └── yield {"type": "done", ...}
```

### 感知触发路径

```
环境变化 (模拟漂移)
     │
     ▼
PerceptionEngine.poll_once()
     │
     ├── DeviceStateStore.drift()
     │
     ├── _evaluate_rules()
     │   └── Condition.evaluate() → True
     │
     ├── emit wot.perception.rule_triggered     │
     │    data: {rule, plan, value, threshold}  │
     ▼                                           │
execute_plan_directly()                        │
     │                                          │
     ├── emit wot.session.started               │
     ├── executor.astream_events()             │
     │   ├── action.started                    │
     │   ├── action.completed                  │
     │   └── action.failed                     │
     ├── observer.ainvoke()                    │
     ├── emit wot.agent.response               │
     ├── emit wot.session.ended               ──┘
     └── return final_text
```
