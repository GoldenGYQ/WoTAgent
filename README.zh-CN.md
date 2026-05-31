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

系统基于 **LangChain 1.3+**，采用“意图分类优先”的三角色运行链路（Responder、Executor、Observer），后端为 **FastAPI + WebSocket**，前端为 **Vue 3**。

---

## 架构

```
User message
  |
  v
+--------------------------+
| Intent Classifier        |
| tool-calling, temp = 0   |
+-----------+--------------+
      |
    +-----+------+
    |            |
    | chat       | control / query
    v            v
+-------------+   +----------------+
| Responder   |   | Executor       |
| no tools    |   | MCP tools      |
+------+------+   +--------+-------+
     |                   |
     |                   v
     |             +-----+------+
     |             | Observer   |
     |             | format reply
     |             +-----+------+
     |                   |
     +-------------------+-------> EventBus -> WebSocket -> Vue Frontend
                   |
                   +-> Log
                   +-> Session Transcript

Perception Engine
  |
  v
ready plan -> Executor -> Observer
```

触发路径：
- 用户消息：Intent Classifier -> chat 走 Responder；control/query 走 Executor -> Observer
- 规则触发：Perception Engine -> Executor -> Observer（跳过意图分类）

---

## 快速开始

### 一键启动（推荐）

在仓库根目录使用三个启动脚本。

启动顺序（很重要）：
1. 仿真平台
2. Agent 后端
3. 前端

每个脚本请在独立终端执行：

```bat
scripts\start-simulator.bat
scripts\start-agent.bat
scripts\start-frontend.bat
```

启动后访问：
- 前端：`http://localhost:5173`
- Agent API：`http://127.0.0.1:8000`
- 仿真平台控制台：`http://127.0.0.1:18080/dashboard`

### 手动启动

### 1. 先启动仿真平台

```bash
cd wot-device-simulator
uv venv
uv sync
uv run --project . wot-sim-api
```

### 2. 启动 Agent 后端

```bash
cd wotagent-prototype
uv venv
uv sync
cp .env.example .env   # 配置 DeepSeek/OpenAI Key
uv run --project . wotagent api
```

### 3. 启动前端

```bash
cd frontend/wot-vue
npm install
npm run dev
```

### 4. 可选：REPL

```bash
uv run --project . wotagent repl
```

---

## 主要特性

| 特性 | 说明 |
|------|------|
| 多角色流水线 | 意图分类 + Responder/Executor/Observer，职责清晰 |
| 自然语言控制 | 例如“把客厅灯调到30%”可自动分类并执行 |
| 意图连续性 | 通过上下文注入和延续提示处理追问场景 |
| 实时事件流 | EventBus 事件通过 WebSocket 推送到前端 |
| 会话转录 | 双 JSONL（full/chat）用于审计和调试 |
| 环境感知 | 规则引擎轮询环境并自动触发执行链路 |
| RBAC 权限 | viewer/operator/admin 三层角色 |
| MCP 接入 | 设备通过 MCP 工具统一调用 |

---

## 项目结构

```
WoTAgent/
├── scripts/                    # 一键启动脚本
│   ├── start-agent.bat
│   ├── start-frontend.bat
│   └── start-simulator.bat
├── wotagent-prototype/         # Agent 后端（Python）
├── wot-device-simulator/       # 仿真平台
└── frontend/wot-vue/           # Vue 前端
```

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [Architecture](wotagent-prototype/doc/ARCHITECTURE.md) | 架构设计、模块关系、事件传输 |
| [Pipeline](wotagent-prototype/doc/PIPELINE.md) | 流水线阶段、流式输出、转录机制 |
| [Events](wotagent-prototype/doc/EVENTS.md) | CloudEvents 类型与 WebSocket 协议 |
| [Perception](wotagent-prototype/doc/PERCEPTION.md) | 规则引擎、轮询、冷却机制 |
| [Technical Guide](doc/TECHNICAL_GUIDE.md) | Agent 与仿真平台逐文件职责说明 |
| [API Guide](doc/API_GUIDE.md) | 后端/仿真平台 API 使用说明与示例 |

---

## 工作机制（简述）

1. **意图分类**
   - `chat` -> Responder
   - `control/query` -> Executor -> Observer

2. **实时事件**
   - 每个阶段把 CloudEvent 发到 EventBus
   - 由 WebSocket 主通道推送给前端

3. **感知自动化**
   - Perception Engine 定时轮询状态
   - 命中规则后直接走 Executor -> Observer
