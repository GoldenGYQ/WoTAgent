# WoTAgent API 指南

状态：当前 API 说明

最后按实际路由核对：2026-07-23

基础地址：`http://127.0.0.1:8000`

FastAPI 应用版本为 `3.0.0`。`/api/v2` 是稳定的实验接口命名空间，不等于产品
发布版本号。

## 服务信息

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 服务元数据 |
| GET | `/api/health` | 服务健康状态和当前运行配置 |
| GET | `/api/v2/capabilities` | 深度思考、流式和 Agent 能力 |
| GET | `/api/v2/agents` | 已注册的专业 Agent 元数据 |
| GET | `/api/v2/prompts` | 当前提示词配置元数据 |

## 部署场景

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v2/deployment/profiles` | 查询可用场景/Profile |
| PUT | `/api/v2/deployment/active` | 切换当前 Profile |

Run 会持久化 `profile_id`、`domain_id` 和 `site_id`。一个 Conversation 不能
混用多个 Profile。

## Principal 与会话

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v2/principals` | 查询可见 Principal |
| GET | `/api/v2/principals/{id}/context` | 查询角色、关系、能力和资源 |
| GET | `/api/v2/principals/role-templates` | 查询后端维护的注册模板 |
| GET | `/api/v2/principals/registration-targets` | 查询可绑定的资源目标 |
| POST | `/api/v2/principals/register` | 按模板注册带 PIN 的 Principal |
| POST | `/api/v2/principals/login` | 登录并创建 Bearer Session |
| GET | `/api/v2/principals/session` | 查询当前 Session |

Principal 路由仍保留 `/users` 兼容接口，新客户端应使用模板注册与 Session
接口。种子演示账号支持一键进入；用户注册账号必须提供 Bearer Session。当前仍是
实验级身份系统，不是生产级统一认证。

## 对话

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v2/conversations` | 创建持久化 Conversation |
| GET | `/api/v2/conversations` | 查询 Conversation 列表 |
| GET | `/api/v2/conversations/{id}` | 查询 Conversation 元数据 |
| GET | `/api/v2/conversations/{id}/turns` | 查询已持久化 Turn |
| DELETE | `/api/v2/conversations/{id}` | 删除 Conversation |

## Run 与 Task

一次用户输入创建一个 Run。Supervisor 可以在 Run 下创建多个并行或存在依赖的
Task；每次专业 Agent 执行记录为 AgentRun。

```http
POST /api/v2/runs
Content-Type: application/json

{
  "conversation_id": "conv_123",
  "message": "检查照明，并在需要时调节客厅环境",
  "role": "operator",
  "user_id": "JH-1",
  "thinking": false
}
```

接口返回 `202 Accepted`、`run_id` 和 `turn_id`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v2/runs/{run_id}` | 查询 Run 状态和最终回复 |
| GET | `/api/v2/runs/{run_id}/tasks` | 查询 Run 的 Task DAG |
| GET | `/api/v2/runs/{run_id}/tasks/{task_id}/agent-runs` | 查询 Agent 执行尝试 |
| POST | `/api/v2/runs/{run_id}/tasks/{task_id}/confirm` | 恢复等待确认的 Task |
| GET | `/api/v2/runs/{run_id}/events?after=N` | 按序号回放 RunEvent |
| GET | `/api/v2/runs/{run_id}/trace-artifacts/{artifact_id}` | 按需读取原始 TraceArtifact |

确认请求：

```json
{"approved": true}
```

Run、Task、AgentRun、事件和待确认负载保存到 SQLite。设备子图使用 `task_id`
作为 LangGraph `thread_id`，因此应用重启后仍可从原 checkpoint 恢复确认。

## WebSocket 事件

连接 `ws://127.0.0.1:8000/ws/v2`，然后发送：

```json
{"run_id":"run_123","after":0}
```

服务端按 sequence 回放 Supervisor、Task、Agent、确认和 Run 终态事件。Run 到达
终态后发送 `stream.completed`。接口不会暴露私有 Chain-of-Thought。

## 领域运行接口

通用 Domain Operations API 同时服务养老和别墅/智能家居：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v2/domains` | 查询 DomainManifest |
| GET | `/api/v2/domains/{domain_id}/subjects` | 查询领域主体 |
| GET | `/api/v2/domains/{domain_id}/subjects/{subject_id}/overview` | 查询运行概览 |
| GET | `/api/v2/domains/{domain_id}/subjects/{subject_id}/resources` | 查询资源和设备 |
| GET | `/api/v2/domains/{domain_id}/subjects/{subject_id}/events` | 查询风险/运行事件 |
| GET | `/api/v2/domains/{domain_id}/subjects/{subject_id}/actions` | 查询动作和审计记录 |
| GET | `/api/v2/domains/{domain_id}/monitor/status` | 查询确定性 Monitor 状态 |
| POST | `/api/v2/domains/{domain_id}/telemetry` | 提交领域遥测 |
| POST | `/api/v2/domains/{domain_id}/scenarios` | 触发实验场景 |
| POST | `/api/v2/domains/{domain_id}/events/{event_id}/acknowledge` | 确认事件 |
| POST | `/api/v2/domains/{domain_id}/events/{event_id}/status` | 执行合法状态迁移 |

`/api/v2/eldercare/*` 作为养老兼容和实验接口保留。新的跨领域前端功能应优先使用
`/api/v2/domains/*`。

## 语音转写

```text
POST /api/v2/speech/transcriptions
Content-Type: multipart/form-data
字段：audio
```

接口支持 WebM、M4A/MP4、MP3、OGG 和 WAV，大小上限由配置决定。它只返回转写
文本供输入框使用，不会自动创建 Run。

## 状态值

Run：

```text
accepted | running | waiting_confirmation | needs_more_information
| completed | partial_success | failed | cancelled
```

Task：

```text
pending | ready | running | waiting_confirmation | completed
| partial_success | skipped | needs_input | denied | unavailable
| failed | blocked | cancelled
```

## 持久化配置

```dotenv
WOTAGENT_CONVERSATION_DB=./data/conversations.db
WOTAGENT_RUN_DB=./data/agent_runs.db
WOTAGENT_CHECKPOINT_DB=./data/agent_checkpoints.db
```

`WOTAGENT_RUN_DB` 保存 API 编排状态，`WOTAGENT_CHECKPOINT_DB` 保存 LangGraph
节点状态和原生 interrupt。两者共同支持重启后的确认恢复。

Principal/Session 和领域数据使用各自的 SQLite Store。RunEvent 是持久化数据；
进程内 `asyncio.Condition` 只负责唤醒 WebSocket，因此尚不支持多实例通知。

## 已删除接口

旧 `/api/v2/tasks`、`/api/v2/traces`、Task SSE 和 v1 路由不属于当前
Run/Task 运行时。
