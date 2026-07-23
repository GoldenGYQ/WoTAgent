# [已处理] 居民房间温控失败调试记录

## 问题

- Run ID：`run_f418755a17fd`
- 用户请求：`把张爷爷房间温度调到 24 度，并确认是否生效`
- 现象：Run 意外失败。
- 目标：使用持久化事件、真实模型和真实 MCP 找到根因。

## 初始假设

- H1：`张爷爷房间` 没有解析到规范 Resident、Room 或 Device。
- H2：Deployment/RBAC/MCP Scope 拒绝温控。
- H3：Supervisor 或 Environment Agent 选择错误任务/工具。
- H4：设备执行成功，但回读验证失败。
- H5：真实模型输出不满足结构约束。

## 证据

- 数据库中的真实 ID 是 `run_f418755a17fd`，不是带 `USER` 前缀的值。
- 历史 Run 在创建 Task 前失败：

```text
task_ids=[]
supervisor_iterations=0
run.started -> model.request.started -> model.request.failed -> run.failed
```

- 失败耗时约 12 秒，与当时 `llm_timeout_seconds=12.0` 一致，但旧事件只记录空
  错误字符串。
- 使用当前 DeepSeek 端点单独调用 Supervisor，可以获得合法
  `SupervisorDecision`，说明请求本身和模型路由可用。
- 修复前，`environment_agent` 偶尔收到 `target=null`。虽然
  `effective_scope` 已收窄，但没有明确 Resident-Room-Device 上下文。
- 修复后真实端到端运行成功：
  - Target：`elderly-001`
  - Room：`A-101`
  - 空调：`ac-002`
  - 环境传感器：`environment-sensor-001`
  - 最终回读确认 24 摄氏度已经生效。

## 根因

1. 历史硬失败来自 Supervisor 第一次模型调用超时；旧日志没有记录异常类型。
2. 养老环境控制虽然正确计算了 Device Scope，但没有稳定注入 Resident Target、
   Room 和 Device Fact，Planner 因而可能反复询问房间。
3. 用户补充 `A-101` 时，没有确定性绑定回原目标，Supervisor 可能把它当成新请求。

## 修复

- `model.request.failed` 记录 `error_type`、`error_repr` 和
  `timeout_seconds`。
- `task.execution.context` 记录 Target、Effective Scope 和 Runtime Context。
- 默认 `AGENT_LLM_TIMEOUT_SECONDS` 从 12 秒调整为 20 秒。
- Supervisor 超时返回明确错误。
- 养老 `environment_agent` 尽量解析 Resident Target，并注入
  `resident_context` 和 `resolved_target`。
- `RunRecord` 持久化 `error`，失败 Run 同时保存 `error` 和
  `final_response`。
- 前端刷新后直接恢复 `run.error`。
- 对 `A-101`、`A-102` 等短回答执行 Clarification Continuation
  规范化，将其合并回原始温控目标。

## 验证

真实模型、真实后端和真实 MCP 运行完成，回复：

```text
张爷爷房间的空调已经调到24度了，现在室温也稳定在24度，已经生效了。
```

执行上下文包含：

```text
target=elderly-001
room=A-101
environment_device_id=ac-002
environment_sensor_device_id=environment-sensor-001
```

回归：

- 后端 `tests/test_api_v2.py`：`6 passed`。
- 前端 Runtime/API 契约：`9 passed`。
- 后端 `tests/test_run_runtime.py`：`10 passed`。
- `A-101 啊！` 可以在上一轮询问房间时恢复到原温控目标。
