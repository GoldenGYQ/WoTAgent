# WoTAgent 架构

## 概述

WoTAgent 是一个基于 LangChain 1.3+/LangGraph 构建的**事件驱动型 IoT 智能家居 Agent**。它采用**意图分类优先的多 Agent 流水线**（Intent Classifier + Responder/Executor/Observer）来解耦路由、设备控制和响应格式化，并配有**基于规则的感知引擎**用于自动化环境监控。

```
用户消息
  |
  v
意图分类器 (工具调用, temp=0)
  |
  +--> chat -----------> 响应器 (无工具)
  |
  +--> control/query --> 执行器 (工具 + MCP) --> 观察器

感知引擎 (规则触发)
  |
  +--> 预结构化计划 -----> 执行器 --> 观察器

所有输出 -> EventBus -> WebSocket (主要) -> 前端
             -> 日志 / 转录
```

两种触发路径：
- **用户消息** → 意图分类器 → chat 走响应器，control/query 走执行器 → 观察器
- **规则触发** → 直接执行器 → 观察器 — 跳过意图分类步骤

### 意图连续性

系统通过 `ConversationState` 跨轮追踪对话上下文，以正确处理追问。详见 [PIPELINE.md](PIPELINE.zh-CN.md#意图连续性)。

## 模块

```
src/wotagent/
├── core/
│   ├── __init__.py    # 公共 API 导出
│   ├── agent.py       # 多 Agent 流水线 (Intent Classifier → Executor → Observer + Responder)
│   ├── session.py     # 会话与记忆生命周期
│   └── state.py       # ConversationState — 跨轮意图连续性
├── events/
│   ├── schema.py      # CloudEvents 1.0 格式
│   └── bus.py         # 发布/订阅事件总线，支持历史回放
├── memory/
│   └── manager.py     # ConversationMemory (窗口化 + 磁盘持久化)
│                       # SessionTranscript (双 JSONL 转录日志)
├── perception/
│   ├── rules.py       # Condition/EnvironmentRule 模型 + 默认规则
│   └── engine.py      # PerceptionEngine (轮询、规则求值、冷却)
├── prompts/
│   └── templates.py   # 5 个 Agent 角色的提示词模板
├── tools/
│   ├── iot.py         # IoT 设备工具 (列出/控制/查询)
│   ├── registry.py    # 系统工具 (psutil, 文件操作)
│   └── mcp_client.py  # MCP 协议客户端 (FastMCP stdio/http)
├── wot/
│   └── td.py          # Thing Description 加载器与语义发现
├── auth/
│   └── rbac.py        # 基于角色的访问控制 (admin/operator/viewer)
├── rag/
│   └── retriever.py   # ChromaDB 向量检索 (用于 TD 文档)
├── api/
│   └── app.py         # 带 WebSocket 端点的 FastAPI 服务
├── cli.py             # CLI 入口 (repl, api, log 子命令)
└── logging/
    └── logger.py      # 结构化日志，集成了事件总线
```

## 事件传输

所有 Agent 动作在 **EventBus** 上产生 CloudEvents，由它供给两个传输通道：

| 传输方式 | 端点 | 状态 |
|---------|------|------|
| WebSocket | `ws://host/ws` | **主要** — Vue 前端 |

WebSocket 接收命令消息 (`chat`, `ping`) 并通过同一连接返回响应，提供单一的双向通道。

## 流水线流程

### Control/Query 路径（完整流水线）

```
用户: "把客厅灯调暗到30%"

Step 0 ─── 意图分类器 (工具调用, temperature=0) ───────────
  模型: ChatOpenAI 绑定 classify_intent 工具
  输入: "把客厅灯调暗到30%" + [方案A: 最近对话上下文] + [方案B: 延续提示]
  输出: tool_call("classify_intent", {"intent": "control", "rationale": "..."})

Step 1 ─── 意图分类器 (内部) ──────────────────────────────
  模型: ChatOpenAI 绑定 classify_intent 工具 (temperature=0)
  输入: 用户消息 + 方案A 上下文 + 方案B 延续提示
  输出: {"intent": "control", "rationale": "..."}

Step 2 ─── 执行器 ────────────────────────────────────────
  提示词: EXECUTOR_PROMPT (工具执行)
  输入:  来自 Step 1 的计划
  工具:  全部 (list_devices, control_device, query_device, rag_retrieve 等)
  动作:  control_device("light-001", "setBrightness", {"level": 30})
  结果:  {"success": true, "device": "Parlor Light", ...}

Step 3 ─── 观察器 ────────────────────────────────────────
  提示词: OBSERVER_PROMPT (响应格式化)
  输入:  计划 + 执行结果 + 用户消息
  工具:  无
  输出:  "客厅灯已经调暗到30%了"
```

### Chat 路径（快捷方式）

```
用户: "你好"

Step 0 ─── 意图分类器 ─────────────────────────────────────
  输出: {"intent": "chat", "rationale": "用户打招呼"}

Step 1 ─── 响应器 ─────────────────────────────────────────
  提示词: RESPONDER_PROMPT (直接聊天)
  工具:  无
  输出: "你好！有什么可以帮你的？"
```

响应器路径比完整的 ReAct Agent 节省一次 LLM 往返（无需工具调用的推理）。

### Query 路径

```
用户: "客厅温度多少？"

Step 0 ─── 意图分类器 → {"intent": "query", "rationale": "..."}
Step 1 ─── 执行器 → query_device("temp-001") → "25°C"
Step 2 ─── 观察器 → "客厅当前温度25°C"
```

### 感知路径（规则触发，跳过意图分类）

```
环境: ac-001 温度漂移至 33.5°C

Step 1 ─── 感知引擎 (纯 Python, 0 LLM token)
  规则: high_temperature (条件: currentTemperature > 32)
  输出: {"intent": "control",
         "rationale": "高温自动开启空调制冷",
         "steps": [{"action": "control_device", "target": "ac-001", ...}]}

         计划通过 EventBus 发射 (wot.perception.rule_triggered)

Step 2 ─── 执行器 (通过 execute_plan_directly 调用)
  动作: control_device("ac-001", "setMode", {"mode": "cool"})
        control_device("ac-001", "setTemperature", {"temp": 26})

Step 3 ─── 观察器 (响应格式化)
  输出: "空调已自动开启制冷，目标温度26°C"
```

## 条件路由

路由由工具调用分类器的 `intent` 字段决定：

| intent | 路径 | 工具 | 场景 |
|--------|------|------|------|
| `chat` | 意图分类器 → 响应器 | 无 | 问候、日常聊天 |
| `query` | 意图分类器 → 执行器 → 观察器 | 全部（只读） | 设备信息、状态查询 |
| `control` | 意图分类器 → 执行器 → 观察器 | 全部 | 设备操作 |
| *(规则触发)* | 直接执行器 → 观察器 | 全部 | 自动化环境响应 |

## 意图连续性

`ConversationState` 类（位于 [`core/state.py`](../src/wotagent/core/state.py)）解决了一个常见问题：当用户用简短回答跟进设备控制意图时，后续消息会被误分类为 `chat`。

```
用户: "我很热"       → intent = control, Agent 问 "你在哪个房间？"
用户: "我在客厅呢"   → 无 state: intent = chat (误分类)
                   → 有 state: intent = control (延续)
```

两种机制协同工作：

**方案A — 上下文注入：** 意图分类前，将最近的消息对作为"最近对话上下文"注入分类器提示词。LLM 看到完整的对话历史，能正确理解简短追问。

**方案B — 延续提示：** 如果上一个非 chat 意图仍活跃且 Agent 问了问题，注入提示："⚠️ 提示：上一步的操作意图是「control」..."。这使分类器偏向于延续活跃意图。

两种注入均在 `classify_intent` 工具调用之前完成，不消耗额外的 LLM token（它们属于同一调用的上下文）。

## 会话转录

每次 Agent 调用可选地生成双 JSONL 转录文件，用于微调和审计：

| 文件 | 内容 | 用途 |
|------|------|------|
| `data/memory/{session_id}.full.jsonl` | 所有流水线事件：计划、工具调用、LLM I/O、时间戳、会话元数据 | 微调、详细调试 |
| `data/memory/{session_id}.chat.jsonl` | 仅用户/助手对话轮次 | 聊天记录查看 |

当 `session_id` 传入 `invoke_agent_stream()` 时自动创建。事件包括：`session_start`, `system_prompt`, `user_message`, `llm_input`（含发给模型的完整消息列表）、`plan`、`tool_call`、`assistant_response`、`session_end`。不可序列化的对象（LangChain 消息类型）通过 `_sanitize()` 自动清理。

## 状态流转

```
                    AgentContext (所有阶段共享)
                    ┌──────────────────────────────┐
                    │ session_id                   │
                    │ user_role                    │
                    │ user_message                 │
                    │ plan_json     ← 由设置          │ 意图分类器 / 感知引擎
                    │ execution_results ← 由设置     │ 执行器
                    │ perception_context ← 由设置    │ 感知引擎
                    └──────────────────────────────┘
```

每个阶段接收仅包含其所需字段的上下文。`dynamic_prompt` 中间件从 `AgentContext` 读取数据以填充模板变量。

## 感知引擎

详见 [PERCEPTION.md](PERCEPTION.zh-CN.md)。

关键要点：
- **零 LLM token**：规则求值是纯 Python 比较
- **后台轮询**：漂移模拟设备状态 + 评估规则
- **注入上下文**：将环境状态注入意图分类器提示词，提升后续路由准确性
- **冷却机制**：防止快速重复触发（默认 10 分钟）

## 单 Agent vs 多 Agent

| 方面 | 单 ReAct (旧) | 多 Agent 流水线 |
|------|--------------|-----------------|
| 决策方式 | LLM 在一个循环中决定一切 | 意图分类器先路由 |
| 工具访问 | 全部工具，LLM 自行选择 | 响应器: 无；执行器: 全部 |
| 聊天效率 | LLM 仍跑工具调用循环 | 响应器快捷方式跳过工具 |
| 可观测性 | 单一黑盒流 | 每阶段发射类型化事件 |
| 故障恢复 | LLM 可能重试也可能不 | 观察器验证并报告 |
| 计划可审计性 | 无（隐含在 LLM 推理中） | 计划是结构化 JSON，可审查 |
| 环境感知 | 无（每次必须查询） | 感知引擎主动注入上下文 |
