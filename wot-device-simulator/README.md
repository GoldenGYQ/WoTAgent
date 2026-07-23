# wot-device-simulator

[English](README.md) | [简体中文](README.zh-CN.md)

面向 Agent 开发的独立 IoT 设备模拟器。所有服务运行在 **单个 FastMCP 服务** 的 `:18080` 端口上 —— MCP 工具、REST 端点和控制面板都在一个进程内。

---

## 架构

```
┌───────────────────────────────────────────────────────┐
│  wot-device-simulator  (端口 18080)                    │
│                                                       │
│  ┌───────────────────────────────────────────────┐    │
│  │  SimulatorEngine (漂移、规则、事件)              │    │
│  │  DeviceStateStore (SQLite)                     │    │
│  └──────────────────┬────────────────────────────┘    │
│                     │                                 │
│     ┌───────────────┴───────────────────────┐          │
│     │                                       │          │
│     ▼                                       ▼          │
│  ┌─────────────────┐  ┌─────────────────────────┐     │
│  │  FastMCP 服务    │  │  REST 端点              │     │
│  │  /mcp (MCP HTTP)|   │  /api/state, /api/events│     │
│  │  Agent 控制/查询 │  │  /api/control, /api/poll│     │
│  │                 │  │  /api/weather/...       │     │
│  └─────────────────┘  ├─────────────────────────┤     │
│                        │  控制面板                 │     │
│                        │  /dashboard (HTML UI)  │     │
│                        └─────────────────────────┘     │
└───────────────────────────────────────────────────────┘
```

### Agent 连接方式

| 传输 | Agent 配置 | 连接方式 |
|------|-----------|---------|
| **stdio** (默认) | `MCP_TRANSPORT_MODE=stdio` | Agent 自动拉起 `mcp_server.py` 为子进程 |
| **HTTP** (生产) | `MCP_TRANSPORT_MODE=http` + `MCP_SERVER_URL=http://<ip>:18080/mcp` | POST 到此服务器的 `/mcp` 端点 |

---

## 快速开始

```bash
# 安装
cd wot-device-simulator
uv venv && uv sync

# 启动（一行命令搞定一切）
wot-sim-api
# 或: uv run --project . wot-sim-api
```

启动后访问：
- **控制面板：** `http://127.0.0.1:18080/dashboard`
- **MCP 端点：** `http://127.0.0.1:18080/mcp`（供 Agent 连接）

---

## 端口 18080 提供的服务

### MCP 工具（Agent 使用，路径 `/mcp`）

| 工具 | 说明 |
|------|------|
| `<device>__read_state` | 根据 TD 自动生成的状态读取工具，属性列表来自 TD |
| `<device>__action__<action>` | 根据 TD 自动生成的动作工具，输入 schema 直接来自 TD |
| `list_devices` | 列出所有设备（ID、标题、位置、能力） |
| `query_all_states` | 获取所有设备运行时状态（一次调用） |
| `query_state(device_id)` | 获取单个设备运行时状态 |
| `control_device(device_id, action, params)` | 兼容及模拟器调试用底层总线；v2 Agent 使用 TD 生成工具 |
| `drift()` | 触发一次环境漂移周期 |
| `poll_once()` | 评估规则并返回命中规则 |
| `patch_state(device_id, property, value)` | 直接设置设备属性 |
| `set_environment(temperature, humidity, ...)` | 设置环境参数 |
| `inject_event(type, data)` | 注入自定义事件 |
| `get_td(device_id)` | 获取设备的 Thing Description |
| `download_all_td()` | 获取所有 Thing Description |

### REST 端点（供面板和手动使用）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 服务健康 + 引擎状态 |
| `GET` | `/api/devices` | 列出所有设备（TD 元数据） |
| `GET` | `/api/state` | 完整仿真状态 + 所有设备运行时状态 |
| `GET` | `/api/state/{device_id}` | 单个设备运行时状态 |
| `POST` | `/api/control` | 控制设备（`{"device_id", "action", "parameters"}`） |
| `POST` | `/api/poll` | 触发一次轮询周期 |
| `GET` | `/api/events?limit=50` | 近期事件历史 |
| `POST` | `/api/events/inject` | 注入自定义事件 |
| `POST` | `/api/state/patch` | 设置单个设备属性 |
| `POST` | `/api/environment/set` | 批量设置温度/湿度/PM2.5/气体浓度 |
| `GET` | `/api/weather/fetch?city=beijing` | 获取实时天气 |
| `POST` | `/api/weather/apply` | 获取天气并应用到仿真 |

### 控制面板

- **地址：** `http://127.0.0.1:18080/dashboard`
- 实时设备卡片，带滑块、开关和快捷操作
- 环境控制（温度、湿度、PM2.5、气体）
- 天气获取与应用
- 事件注入
- 事件流（每 3 秒自动轮询）

---

## 内部架构

```
src/wot_device_simulator/
├── service.py           # FastMCP HTTP 服务 — wot-sim-api 入口
├── mcp_server.py        # MCP 工具定义（与 Agent stdio 模式共享）
├── simulator.py         # DeviceStateStore (SQLite) + SimulatorEngine (漂移、规则、事件)
├── state_cli.py         # 直接 SQLite 查询 CLI
├── dashboard.html       # 仿真平台控制面板 UI
└── td.py                # Thing Description 加载器 & 发现
```

### 文件职责

| 文件 | 角色 | 关键导出 |
|------|------|---------|
| `service.py` | HTTP 服务入口 | `main()` — 启动 FastMCP HTTP，通过 `@mcp.custom_route()` 注册 REST |
| `mcp_server.py` | MCP 工具定义 | `_build_server(sim)` — 创建带全部 IoT 工具的 FastMCP 实例 |
| `simulator.py` | 核心仿真逻辑 | `DeviceStateStore` (SQLite CRUD)、`SimulatorEngine` (漂移、规则、事件发布)、`control_device()` |
| `state_cli.py` | SQLite 查询 CLI | 查询/重置设备状态的 CLI |
| `dashboard.html` | 控制面板 | 单文件 HTML/JS 面板（HTTP 轮询，无 WebSocket） |
| `td.py` | 设备元数据 | `find_devices()`、`get_device_by_id()`、`load_tds()` |

### 数据流

```
MCP 调用 或 REST 请求
       │
       ▼
  service.py (路由处理)
       │
       ├─ MCP 工具调用 → simulator.py (control_device / drift / patch_state)
       ├─ REST /api/state → simulator.py (DeviceStateStore.get_all())
       ├─ REST /api/control → simulator.py (control_device)
       └─ REST /api/poll → simulator.py (poll_once → drift + rules)
                              │
                              ▼
                         DeviceStateStore
                         (SQLite 位于 data/state.db)
                              │
                              ▼
                         发布事件给订阅者
                         (面板通过 GET /api/events 自动轮询)
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WOT_SIM_TD_ROOT` | `data/td/` | 本地 TD 目录 |
| `WOT_SIM_TD_REGISTRY` | — | 远程 TD 注册中心 URL |

### Agent 端（远程部署）

在 `wotagent-prototype/.env` 中：

```bash
MCP_TRANSPORT_MODE=http
MCP_SERVER_URL=http://<仿真平台IP>:18080/mcp
```

---

## 开发说明

- **没有 `wot-sim-mcp` CLI 命令。** `mcp_server.py` 是内部模块，被 `service.py` 导入，也被 Agent stdio 模式自动拉起。用户无需手动运行。
- **没有 FastAPI 封装。** `service.py` 使用纯 FastMCP，REST 端点通过 `@mcp.custom_route()` 注册。
- **面板使用 HTTP 轮询**（3 秒间隔），不使用 WebSocket。
- 所有状态持久化在 `data/state.db`（SQLite，WAL 模式）。
