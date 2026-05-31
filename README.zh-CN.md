<p align="center">
  <img src="https://img.shields.io/badge/LangChain-1.3%2B-2ea44f" alt="LangChain" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Vue.js-3-4FC08D" alt="Vue.js" />
  <img src="https://img.shields.io/badge/MCP-1.0-8B5CF6" alt="MCP" />
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="License" />
</p>

# WoTAgent

[English](README.md) | [简体中文](README.zh-CN.md)

**事件驱动的智能家居 IoT 智能体系统**：支持自然语言理解，通过 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 控制设备，将实时事件推送到 Vue 控制台，并提供自动化环境感知能力。

系统基于 **LangChain 1.3+** 三 Agent 流水线（意图分类 → 执行器 → 观察器 + 响应器快捷通道），**FastAPI** WebSocket 服务端，**Vue 3** 前端。

---

## 总体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        WoTAgent 单仓库                               │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  wotagent-prototype  (Agent 后端)                              │   │
│  │  端口 :8000, WebSocket, LLM 流水线                             │   │
│  │                                                                │   │
│  │  用户 → 意图分类器 → 执行器 → 观察器 → 回复                     │   │
│  │        (聊天意图) → 响应器快捷通道                               │   │
│  │                     │                                          │   │
│  │               EventBus → WebSocket → Vue 前端                   │   │
│  │               EventBus → 日志 / 会话转录                        │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │ MCP (stdio 或 HTTP)                    │
│  ┌──────────────────────────▼───────────────────────────────────┐   │
│  │  wot-device-simulator  (IoT 仿真平台)                          │   │
│  │  端口 :18080, FastMCP + REST + 面板                            │   │
│  │                                                                │   │
│  │  ┌─────────────┐  ┌──────────────────┐  ┌────────────────┐   │   │
│  │  │ MCP 工具     │  │ REST 端点         │  │ 控制面板        │   │   │
│  │  │ (通过 /mcp)  │  │ (/api/*)         │  │ (/dashboard)   │   │   │
│  │  └──────┬──────┘  └──────────────────┘  └────────────────┘   │   │
│  │         │                                                     │   │
│  │  ┌──────▼─────────────────────────────────────────────────┐   │   │
│  │  │  DeviceStateStore (SQLite) + SimulatorEngine (漂移)     │   │   │
│  │  │              （通过控制数据库的方式模拟控制设备）           │   │   │
│  │  └────────────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌──────────────────────────────────────────────┐                   │
│  │  frontend/wot-vue  (Vue 3 控制台)             │                   │
│  │  端口 :5173, WebSocket → Agent                │                   │
│  └──────────────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────┘
```

### 两种部署模式

| 模式 | Agent MCP 配置 | 仿真平台 | 使用场景 |
|------|---------------|---------|---------|
| **stdio** (默认) | `MCP_TRANSPORT_MODE=stdio` | **无需手动启动** — Agent 自动拉起 `mcp_server.py` 为子进程 | 开发：同一台机器 |
| **HTTP** | `MCP_TRANSPORT_MODE=http` + `MCP_SERVER_URL=http://<ip>:18080/mcp` | 必须手动运行 `wot-sim-api` | 生产：不同机器 |

> **💡 stdio 模式详解（开发推荐）：**
>
> stdio 模式下你**只需要一个终端**：启动 Agent 后端。
>
> ```bash
> # 只需要这个，不需要手动跑仿真平台！
> wotagent api
> ```
>
> Agent 启动时会**自动将 `mcp_server.py` 拉起为子进程**（通过 FastMCP stdio 管道），所有设备控制指令通过 stdin/stdout 直接通信，整个过程对你完全透明。你打开前端 `http://localhost:5173` 就能直接控制设备了。
>
> **所以我到底要不要跑 `wot-sim-api`？**
>
> | 你的需求 | stdio 模式要做什么 |
> |----------|------------------|
> | 只想用 Agent 控制设备 | ✅ `wotagent api` 就够了 |
> | 想看设备状态面板 | 可选：额外启动 `wot-sim-api` 打开 `http://localhost:18080/dashboard` |
> | 生产环境部署 | 切到 HTTP 模式，仿真平台跑在另一台机器 |
>
> ⚠️ **一个常见的误解：** 有些用户觉得"仿真平台是独立的，所以必须先跑起来"。**实际上 stdio 模式下仿真平台不是必需的**——Agent 代替你做了这件事。只有当你需要 HTTP 模式（跨机器）或想查看仿真面板时，才需要手动运行 `wot-sim-api`。

---

## 项目结构

```
WoTAgent/
│
├── scripts/                            # 一键启动脚本
│   ├── start-agent.{bat,sh}            # 启动 Agent 后端 (:8000)
│   ├── start-frontend.{bat,sh}         # 启动 Vue 前端 (:5173)
│   └── start-simulator.{bat,sh}        # 启动仿真平台 (:18080)
│
├── wotagent-prototype/                  # Agent 后端 (Python)
│   ├── src/wotagent/
│   │   ├── cli.py                      # CLI 入口: repl, api, log
│   │   ├── api/app.py                  # FastAPI + WebSocket 服务 (:8000)
│   │   ├── core/
│   │   │   ├── agent.py                # 多 Agent 流水线 (意图分类 → 执行器 → 观察器 + 响应器)
│   │   │   ├── session.py              # 会话生命周期 & 记忆绑定
│   │   │   └── state.py                # ConversationState — 跨轮意图连续性
│   │   ├── events/
│   │   │   ├── schema.py               # CloudEvents 1.0 格式
│   │   │   └── bus.py                  # 发布/订阅事件总线，历史回放
│   │   ├── prompts/
│   │   │   └── templates.py            # LangChain 提示词模板 (5 角色)
│   │   ├── tools/
│   │   │   ├── iot.py                  # IoT 工具：control_device, query_device, list_devices (走 MCP)
│   │   │   ├── mcp_client.py           # MCP 客户端（stdio 自动拉起 / HTTP 远程）
│   │   │   └── registry.py             # 系统工具 (psutil, 文件操作)
│   │   ├── perception/
│   │   │   ├── engine.py               # PerceptionEngine — 通过 MCP 获取状态、规则求值（无本地 SQLite）
│   │   │   └── rules.py                # Condition, EnvironmentRule 模型 + 默认规则
│   │   ├── memory/
│   │   │   └── manager.py              # ConversationMemory + SessionTranscript (双 JSONL)
│   │   ├── wot/td.py                   # Thing Description 加载器 & 语义发现
│   │   ├── rag/retriever.py            # ChromaDB 向量检索
│   │   ├── auth/rbac.py                # 基于角色的访问控制 (admin/operator/viewer)
│   │   └── logging/logger.py           # 结构化日志，集成 EventBus
│   ├── doc/                            # 详尽架构文档
│   │   ├── ARCHITECTURE.{md,zh-CN.md}
│   │   ├── PIPELINE.{md,zh-CN.md}
│   │   ├── EVENTS.{md,zh-CN.md}
│   │   └── PERCEPTION.{md,zh-CN.md}
│   ├── tests/
│   │   ├── test_core.py
│   │   └── test_perception.py
│   └── data/memory/                   # 持久化的对话转录
│
├── wot-device-simulator/               # IoT 仿真平台 (Python)
│   ├── src/wot_device_simulator/
│   │   ├── service.py                 # FastMCP HTTP 服务 (:18080) — MCP + REST + 面板，合一
│   │   ├── mcp_server.py              # MCP 工具定义（service.py 和 stdio 模式都用）
│   │   ├── simulator.py               # DeviceStateStore (SQLite) + SimulatorEngine (漂移、规则)
│   │   └── dashboard.html             # 仿真平台控制面板
│   ├── data/td/                       # Thing Description JSON 文件
│   └── README.{md,zh-CN.md}
│
├── frontend/wot-vue/                   # Vue 3 + Vite 前端
│   ├── src/
│   │   ├── views/ChatView.vue         # 主聊天 + 控制台布局
│   │   ├── components/                # DeviceDashboard, MessageItem, EventLog
│   │   ├── stores/chat.ts             # Pinia 状态管理
│   │   └── api/client.ts              # WebSocket 客户端 + REST 封装
│   └── README.md
│
├── CLAUDE.md                          # 给 Claude Code 的项目说明
├── README.md                          # 本文档（英文）
└── README.zh-CN.md                    # 中文版本
```

---

## 快速开始

### 一键启动

依次打开三个终端：

```bash
# 终端 1：仿真平台
scripts/start-simulator.sh     # macOS / Linux
scripts\start-simulator.bat    # Windows

# 终端 2：Agent 后端
scripts/start-agent.sh         # macOS / Linux
scripts\start-agent.bat        # Windows

# 终端 3：前端
scripts/start-frontend.sh      # macOS / Linux
scripts\start-frontend.bat     # Windows
```

启动后访问：
- **前端界面：** `http://localhost:5173`
- **Agent API：** `http://127.0.0.1:8000`
- **仿真面板：** `http://127.0.0.1:18080/dashboard`
- **API 文档：** `http://127.0.0.1:8000/docs`

### 手动启动

```bash
# 1. 初始化两个项目
cd wot-device-simulator && uv venv && uv sync
cd ../wotagent-prototype && uv venv && uv sync
cp .env.example .env   # 配置 DeepSeek/OpenAI API Key

# 2. 启动仿真平台
cd wot-device-simulator
source .venv/bin/activate        # Windows: .venv\Scripts\activate
wot-sim-api                      # → http://127.0.0.1:18080

# 3. 启动 Agent（新终端）
cd wotagent-prototype
source .venv/bin/activate
wotagent api                     # → http://127.0.0.1:8000

# 4. 启动前端（新终端）
cd frontend/wot-vue
npm install && npm run dev       # → http://localhost:5173

# 5. 或者试试 REPL
cd wotagent-prototype
source .venv/bin/activate
wotagent repl
```

> **注意：** 开发模式（`MCP_TRANSPORT_MODE=stdio`，默认）下，Agent 自动拉起
> 仿真平台的 MCP 子进程。仿真平台终端非必需但建议使用（可查看面板）。

---

## Agent 流水线工作原理

### 意图分类

```
用户: "把客厅灯调暗到30%"

意图分类器 (temp=0, 工具调用)
  └→ classify_intent() → {"intent": "control", "rationale": "用户想要调暗客厅灯"}

基于意图的路由：
  chat     → 响应器（无工具，直接回复） — 节省 LLM 往返
  control  → 执行器 + 观察器（全部工具）
  query    → 执行器 + 观察器（只读工具）
```

### 完整流水线（控制意图）

```
用户: "把客厅灯调暗到30%"

Step 1 — 意图分类器 (temperature=0, 不思考)
  └→ {"intent": "control", "rationale": "用户想调暗客厅灯"}

Step 2 — 执行器（全部工具可用）
  └→ MCP 调用: control_device("light-001", "setBrightness", {"level": 30})
     │
     ├─ stdio 模式: 自动启动 mcp_server.py 子进程
     │                → 写入 DeviceStateStore (SQLite)
     │                → 返回新状态
     │
     └─ HTTP 模式:   POST 到仿真平台 /mcp 端点
                      → 仿真平台本地处理
                      → 返回新状态
  └→ {"success": true, "device": "Parlor Light", "state": {...}}

Step 3 — 观察器（回复格式化，无工具）
  └→ "客厅灯已经调暗到30%了"

所有步骤 → CloudEvents → EventBus → WebSocket → Vue 前端
```

### 规则引擎自动感知

**PerceptionEngine** 后台循环：
1. 调用仿真平台的 `drift` MCP 工具（模拟温度/湿度变化）
2. 调用 `query_all_states` MCP 工具获取全量设备状态
3. 本地规则匹配（纯 Python，零 LLM token）
4. 命中规则时发出 `wot.perception.rule_triggered` 事件

```
PerceptionEngine (后台，每 60 秒)
  │
  ├─ MCP 调用: drift() → 仿真平台随机化传感器值
  ├─ MCP 调用: query_all_states() → 获取所有设备状态
  │
  └─ 本地规则匹配 (Python)
       └─ 温度 > 32°C → 发出 rule_triggered 事件
            └─ 如果 ENABLE_PERCEPTION_AUTO_EXEC=true → 执行 执行器 → 观察器
```

---

## MCP 传输：stdio vs HTTP

### stdio 模式（默认，开发）

```
Agent 进程                    仿真平台
┌─────────────────┐           ┌─────────────────────┐
│ MCPToolClient   │──stdio──→│ mcp_server.py        │
│ (get_mcp_client)│   管道    │ (自动拉起子进程)       │
│                 │←─────────│ → DeviceStateStore   │
└─────────────────┘           └─────────────────────┘
```

- **零配置** — Agent 自动启动仿真脚本作为子进程
- **无网络开销** — stdin/stdout 管道通信
- `wot-sim-api` 仍可用于查看面板

### HTTP 模式（生产环境）

```
Agent 机器                    仿真平台机器
┌─────────────────┐  HTTP    ┌─────────────────────────┐
│ MCPToolClient   │─────────→│ wot-sim-api (:18080)     │
│ (get_mcp_client)│  POST    │ ┌─────────────────────┐  │
│                 │←─────────│ │ FastMCP HTTP 服务   │  │
└─────────────────┘          │ │ MCP + REST + 面板   │  │
                              │ └─────────────────────┘  │
                              └─────────────────────────┘
```

- **远程** — Agent 和仿真平台可在不同机器
- **独立服务** — 各自独立运行
- **可见性** — 仿真平台日志显示每次 MCP 调用

---

## 仿真平台：单一端口架构

仿真平台在 `:18080` 运行 **单个 FastMCP HTTP 服务**，一体提供：

| 路径 | 协议 | 用途 |
|------|------|------|
| `/mcp` | Streamable HTTP (MCP) | Agent 设备控制/查询 |
| `/api/state` | REST | 当前设备状态 |
| `/api/control` | REST | 手动设备控制 |
| `/api/events` | REST | 事件历史 |
| `/api/weather/*` | REST | 天气获取与应用 |
| `/dashboard` | HTML | 仿真平台控制面板 |

> **注意：** `wot-sim-mcp` 是内部工具，仅用于 Agent stdio 模式自动拉起。
> 用户无需手动运行。

---

## 环境变量

### Agent（wotagent-prototype/.env）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY` | — | DeepSeek / OpenAI API Key |
| `OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | API 地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |
| `ENABLE_THINKING` | `true` | DeepSeek 思考链 |
| `ENABLE_STREAMING` | `true` | REPL 流式输出 |
| `ENABLE_PERCEPTION` | `true` | 启动感知引擎 |
| `PERCEPTION_INTERVAL` | `60` | 轮询间隔（秒） |
| `ENABLE_PERCEPTION_AUTO_EXEC` | `false` | 触发规则后自动执行（花费 LLM token） |
| `MCP_TRANSPORT_MODE` | `stdio` | `stdio`（开发，自动拉起）或 `http`（生产，远程） |
| `MCP_SERVER_URL` | `http://localhost:18080/mcp` | 仿真平台 MCP 端点（HTTP 模式） |

### 仿真平台

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WOT_SIM_TD_ROOT` | `data/td/` | 本地 TD 目录 |
| `WOT_SIM_TD_REGISTRY` | — | 远程 TD 注册中心地址 |

---

## 文档导航

| 文档 | EN | CN | 内容 |
|------|----|----|------|
| 架构 | [EN](wotagent-prototype/doc/ARCHITECTURE.md) | [中文](wotagent-prototype/doc/ARCHITECTURE.zh-CN.md) | 系统设计、模块职责、事件传输 |
| 流水线 | [EN](wotagent-prototype/doc/PIPELINE.md) | [中文](wotagent-prototype/doc/PIPELINE.zh-CN.md) | 意图分类、角色流转、流式输出 |
| 事件 | [EN](wotagent-prototype/doc/EVENTS.md) | [中文](wotagent-prototype/doc/EVENTS.zh-CN.md) | CloudEvents 类型与 WebSocket 协议 |
| 感知 | [EN](wotagent-prototype/doc/PERCEPTION.md) | [中文](wotagent-prototype/doc/PERCEPTION.zh-CN.md) | 规则引擎、轮询机制、冷却策略 |
| 仿真平台 | [EN](wot-device-simulator/README.md) | [中文](wot-device-simulator/README.zh-CN.md) | 仿真平台设置、API、面板 |

---

## 主要特性

| 特性 | 说明 |
|------|------|
| **🧠 多 Agent 流水线** | 意图分类 + 执行器 + 观察器，职责清晰，每步产生类型化事件 |
| **💬 自然语言控制** | "把客厅灯调到30%" → 自动分类 → 设备控制 → 确认回复 |
| **🔄 意图连续性** | "我在客厅呢" 继承上一步控制意图（方案A 上下文注入 + 方案B 延续提示） |
| **📡 实时事件流** | Agent 动作通过 WebSocket 推送到 Vue 前端 |
| **📋 会话转录** | 双 JSONL（full/chat）用于审计和调试 |
| **🔍 RAG 增强发现** | ChromaDB 向量检索设备文档 |
| **🌡️ 感知引擎** | 通过 MCP 获取状态、规则匹配、零 LLM token 求值 |
| **🔐 RBAC 权限** | viewer/operator/admin 三层角色 |
| **🔌 MCP 接入** | 双传输模式（stdio/HTTP），本地开发到生产部署无缝切换 |
| **📦 跨平台** | Windows `.bat` + macOS/Linux `.sh` 启动脚本 |

---

## 许可

MIT
