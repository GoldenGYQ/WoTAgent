# WoTAgent 2.0

WoTAgent 2.0 是一个面向实验验证的 LLM + WoT 智能家居控制系统。LLM 负责理解自然语言并生成结构化 `TaskPlan`；RBAC、确认、步骤执行、状态验证与有限恢复由确定性代码负责；设备能力由 WoT Thing Description（TD）描述，并动态编译为 MCP Tool。

## 核心链路

```text
Vue 2.0 Console
  -> FastAPI /api/v2
  -> Structured LLM Butler Supervisor
  -> Professional Agent / bounded re-route
  -> LangGraph Agent Loop
  -> Planner -> Policy -> Orchestrator -> Verifier/Recovery -> Response Agent
  -> MCPDeviceGateway -> TD-generated MCP tools
  -> wot-device-simulator / SimuHome gateway / ESP32 adapter
```

关键原则：

- LLM 是语义决策核心，但不直接驱动硬件。
- 普通聊天和 Monitor 事件都由 Butler 负责目标解析与专业 Agent 委派。
- 专业 Agent 返回结构化 `clarify` 时可退回 Butler，最多有限次数重新路由。
- Health Agent 对普通健康查询也使用受限 Observe-Reason-Act 调查循环。
- Planner 输出受 Pydantic Schema 约束的计划。
- Executor 是确定性编排器，只执行计划中且 TD 已声明的动作。
- PolicyEngine 在模型之外执行 RBAC 和高风险确认。
- 每次任务生成可重放 Trace，记录节点、工具输入输出、循环次数、TTFT、Token 与估算费用。
- SQLite 分别保存会话、Run/Task 运行记录和 LangGraph checkpoint，支持上下文延续与服务重启后的确认恢复。

## 仓库结构

```text
WoTAgent/
├── wotagent-prototype/              # 2.0 Agent 后端与评测
│   ├── src/wotagent/agent_graph/    # LangGraph 节点与循环
│   ├── src/wotagent/orchestration/  # 确定性执行、验证、恢复
│   ├── src/wotagent/policy/         # RBAC 与确认策略
│   ├── src/wotagent/gateway/        # MCP 与 SimuHome 网关
│   ├── src/wotagent/telemetry/      # Trace、Token、费用、TTFT
│   └── src/wotagent/evaluation/     # HomeBench/SimuHome 对齐评测
├── frontend/wotagent-v2-console/    # 独立 Vue 3 运行监控台
└── wot-device-simulator/            # TD、MCP、REST 与设备状态仿真
```

1.0 已由 Git tag `v1.0.0` 和 `main` 历史保存；当前分支不再保留旧 ReAct、EventBus、轮询感知、旧 Session Memory 与旧 Vue 前端。

## 本地启动

Windows 下推荐直接双击仓库根目录中的统一入口：

```text
start-wotagent.bat         启动完整本地服务栈
start-wotagent-safe.bat    安全模式，不把 Monitor 事件提交给大模型
start-wotagent-remote.bat  启动服务栈并创建临时 Cloudflare 公网隧道
status-wotagent.bat        查看进程和健康状态
stop-wotagent.bat          停止服务栈并关闭 Tailscale Serve
```

统一脚本依次启动仿真端（`18080`）、Agent API（`8000`）、独立 Monitor
进程和 Vue 前端（`5174`）。PID 与日志保存在
`%LOCALAPPDATA%\WoTAgent\stack`，不会写入项目仓库。

手机访问时双击 `start-wotagent-remote.bat`，窗口会输出临时
`trycloudflare.com` 地址。当前实验 API 没有真实鉴权，因此该地址仅用于短时
测试；测试完成后应立即双击 `stop-wotagent.bat`。如需 Tailscale 私网方式，
可在 PowerShell 中执行 `scripts\wotagent-stack.ps1 private`。

### 1. 仿真端

```powershell
cd D:\Users\gyq16\Desktop\PRJ\WoTAgent\wot-device-simulator
.\.venv\Scripts\python.exe -m wot_device_simulator.service
```

控制面板：`http://127.0.0.1:18080/dashboard`，MCP：`http://127.0.0.1:18080/mcp`。

默认 stdio 模式下，Agent 也可以自动启动 MCP 子进程，此时不必先启动 HTTP 仿真端。若使用 HTTP：

```env
MCP_TRANSPORT_MODE=http
MCP_SERVER_URL=http://127.0.0.1:18080/mcp
```

### 2. Agent 后端

在 `wotagent-prototype/.env` 配置：

```env
LLM_MODEL=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=your-key
ENABLE_THINKING=true
ENABLE_STREAMING=true
```

启动：

```powershell
cd D:\Users\gyq16\Desktop\PRJ\WoTAgent\wotagent-prototype
.\.venv\Scripts\python.exe -m wotagent.cli api --host 127.0.0.1 --port 8000
```

API 文档：`http://127.0.0.1:8000/docs`。

### 3. 2.0 前端

```powershell
cd D:\Users\gyq16\Desktop\PRJ\WoTAgent\frontend\wotagent-v2-console
npm install
npm run dev
```

访问 Vite 输出的地址，默认配置为 `http://localhost:5174`。

## 验证

```powershell
cd wotagent-prototype
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend\wotagent-v2-console
npm test
npm run build

cd ..\..\wot-device-simulator
.\.venv\Scripts\python.exe -m pytest -q
```

详细接口见 [API_GUIDE.md](doc/API_GUIDE.md)，实际架构见 [WOTAGENT_2.0_COMPLETE_TECHNICAL_GUIDE.md](doc/WOTAGENT_2.0_COMPLETE_TECHNICAL_GUIDE.md)。
