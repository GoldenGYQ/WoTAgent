# [已处理] 空调控制失败调试记录

## 问题

- Run ID：`run_7aa09487f137`
- 现象：
  - Run 出现 `failed` 事件后，前端 Trace 停止更新，刷新后才能看到后续事件。
  - 空调控制没有执行，最终回复声称设备无法自动控制。
- 目标：
  - 判断 Trace 卡住发生在前端还是后端。
  - 判断空调失败来自 Planner、TD/MCP、设备映射还是前端状态。

## 初始假设

- H1：前端看到失败终态后过早停止流式订阅。
- H2：界面进入冻结分支，忽略后端后续事件。
- H3：`environment_agent` 生成了只查询、不执行的计划。
- H4：空调 TD/MCP 没有注册控制 Action。
- H5：Planner/Reviewer 接受了不能完成制冷目标的计划。

## 已收集证据

- `task.execution.context` 显示 `execution_device_ids` 包含 `ac-002`。
- Planner 事件显示 `all_device_count=4`，但 `device_count=1`。
- `plan.validation.failed` 拒绝了只查询的制冷计划。
- `agent.node.failed` 报告修复计划中的 `ac-002` 未注册。
- 后续仍出现 `run.completed`，证明后端在中间失败后继续记录事件。
- `wot-device-simulator/data/td/ac_bedroom1.json` 声明了 `turnOn` 和
  `setTemperature`，因此空调在 Simulator 中真实可控。

## 结论

- H1/H2：成立。终态刷新时 Transport 可能关闭，但没有先拉取最后一段增量事件。
- H3：部分成立。首次计划只查询，修复阶段才尝试调用 `ac-002`。
- H4：不成立。空调 TD 和 Action 存在。
- H5：成立。Observe-Act 获得 4 台授权设备，但语义筛选只把 1 台注入 Planner，
  导致 TD 校验看不到 `ac-002`。

## 修复

- Planner 事件增加 `selected_device_ids` 和 `all_device_ids`。
- 前端 `refreshRun()` 在终态关闭 Transport 前先获取 Cursor 后的增量事件。
- Observe-Act Planner 保留调用方传入的完整 Scoped Device Set，不再重复筛掉
  房间执行器。

## 验证

- 后端：`tests/test_planner_adapter_v2.py`，`23 passed`。
- 前端：`src/composables/useAgentRuntime.test.ts`，`2 passed`。
- 前端 typecheck 通过。
