# WoTAgent 仓库说明

## 当前架构

仓库包含三个主要项目：

- `wotagent-prototype`：FastAPI、Run/Task 编排、Agent 和评测后端。
- `frontend/wotagent-v2-console`：Vue 3 对话、Task 和 Trace 控制台。
- `wot-device-simulator`：FastMCP 设备仿真、TD 注册、REST API 和 Dashboard。

当前运行链：

```text
用户消息或 Monitor Event
-> RunService
-> Python RunCoordinator
-> Butler create_agent Supervisor
-> TaskScheduler
-> Health/Care create_agent 或设备 LangGraph
-> Principal/AgentSpec/MCP Scope/TD/Policy
-> MCP Resource/Tool
-> 执行后验证
-> Supervisor 二次决策与最终回复
```

LLM 不负责授权，也不直接执行硬件。Conversation、Run、Task、AgentRun、事件和
确认状态保存到 SQLite；设备 LangGraph 使用持久化 Checkpoint 支持确认恢复。

## 常用命令

```powershell
cd wotagent-prototype
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m wotagent.cli api --port 8000

cd ..\frontend\wotagent-v2-console
npm test
npm run typecheck
npm run build
npm run dev

cd ..\..\wot-device-simulator
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m wot_device_simulator.service
```

也可以在仓库根目录使用：

```text
start-wotagent.bat
start-wotagent-safe.bat
status-wotagent.bat
stop-wotagent.bat
```

## 维护边界

- 不要恢复 v1 EventBus、旧 `/api/chat`、旧 `/ws` 或 `/api/v2/tasks`。
- 外层真实运行时是 `RunCoordinator`，不要误写成统一 LangGraph 主图。
- Device Affordance 必须来自 TD，并通过 MCP 调用。
- Principal 授权、Policy、参数校验和状态验证必须位于模型之外。
- 添加图节点或恢复逻辑时必须保留 RunEvent 和 TraceArtifact。
- `frontend/wotagent-v2-console` 是唯一 Agent 前端；Simulator Dashboard 只负责
  设备状态。
- `smart_home`、`eldercare` 是 Domain；`villa`、养老院演示是 Deployment
  Profile；Human Principal 与 Software Agent 必须分开。
- 运行时 `.db-shm`、`.db-wal`、日志和远程附件不能作为源码提交。

## 文档基线

优先阅读：

1. `doc/README.md`
2. `handoff.md`
3. `doc/TECHNICAL_GUIDE.md`
4. `doc/API_GUIDE.md`

旧“完整技术文档”只用于理解历史演进。
