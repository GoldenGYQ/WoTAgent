# WoTAgent 2.0 TD-MCP 链路契约

## 1. 架构目标

WoTAgent 2.0 将 Thing Description 作为设备能力的唯一语义来源。Matter、ESP32 私有协议和 SimuHome 都是设备数据模型或执行协议；它们需要先被翻译或绑定为 TD，再进入统一 Agent 链路。

```text
Device / Matter / SimuHome
          |
          v
Thing Description
          |
          v
TD Tool Compiler
          |
          v
FastMCP generated tools
          |
          v
MCP Client catalog -> Planner context
          |
          v
TaskPlan -> Policy -> deterministic Executor
          |
          v
generated action tool -> generated read tool -> Verifier
```

## 2. 已实现契约

### 2.1 TD 输入约束

- 每个动作的 `input` 必须是 JSON Schema object。
- 参数名、类型、范围、枚举和必填字段均写入 TD，不允许由模型或服务端隐藏映射猜测。
- 空参数动作使用 `type=object`、空 `properties` 和 `additionalProperties=false`。
- 编译阶段拒绝标量输入、空设备 ID 和工具名冲突。

### 2.2 MCP 工具生成

- 状态工具：`<device_id>__read_state`。
- 动作工具：`<device_id>__action__<action>`。
- MCP input schema 直接复制 TD action input schema。
- MCP metadata 保存 `device_id`、动作名、位置、标题和属性列表。
- 当前 18 份 TD 共生成 56 个设备工具；管理及实验工具仍独立保留。

### 2.3 Agent Client 与规划

- Client 通过 `list_tools` 发现生成工具，不复制服务端命名表。
- Client 根据 MCP metadata 解析 `(device_id, operation, affordance)`。
- Planner 的生产能力上下文由同一 MCP catalog 重建，不再独立读取本地 TD。
- 设备相关性裁剪仍在 Planner 前执行，因此不会把全部工具 schema 注入每轮 prompt。
- 离线测试可显式使用本地 TD fixture，但该路径不用于生产 v2 API。

### 2.4 执行与验证

- Executor 调用具名 TD action tool，不调用通用 `control_device`。
- Verifier 调用同设备的 TD read tool检查后置条件。
- MCP 冷启动和工具协商在 Gateway initialize 阶段完成，不占用单次设备动作超时。
- trace 中记录真实生成工具名、输入、输出和验证状态。

## 3. 验收证据

真实 stdio MCP 验收执行：

```text
TaskPlan(light-001, setBrightness, 53)
-> light_001__action__set_brightness
-> light_001__read_state
-> observed brightness=53
-> verification passed
-> task succeeded
```

测试基线：

- WoTAgent 后端：156 passed，1 个需真实模型的测试默认跳过。
- Device Simulator：59 passed。
- 新增单点测试覆盖 TD 编译、schema 一致性、FastMCP 注册执行、Client 元数据发现、Planner 单一事实源和 SimuHome 语义映射。

## 4. SimuHome 适配状态

已完成 600 个公开 episode 的静态语义适配分析：

- 六类 query type 各 100 个；feasible/infeasible 各 300 个。
- 1177 条 Matter 目标断言中 1167 条可翻译为 WoTAgent 规范动作，静态覆盖率 99.15%；结果写入 `data/evaluation/simuhome_alignment_600.json`。
- 仅剩 10 条电视频道 major/minor 组合断言未映射，需建模为原子 `setChannel` 动作。
- 静态报告中的 `td_action_ready_episodes` 只表示动作语义可翻译；该历史文件的 `simuhome_runtime_connected=false` 不再代表当前实现状态。

已完成 qt3 Runtime 子链路：

1. 每个 episode 的 Matter home config 转换为动态 TD。
2. TD action schema 编译为具名 FastMCP 工具。
3. Agent 通过 `MCPDeviceGateway` 发现和调用工具。
4. MCP 工具通过 SimuHome REST/Matter binding 执行 command/attribute write。
5. episode reset、required action、最终状态断言、Token/TTFT 和完整 trace 持久化。

代表性 qt3 feasible 5 集结果为 5/5 episode 成功、11/11 Matter 断言通过。证据位于 `data/evaluation/runs/simuhome_qt3_mcp_representative_5/`。

尚未完成的是房间环境因果控制、虚拟时间、锚点事件和工作流调度，因此不能声称 qt1/qt2/qt4 或 600 集全量适配完成。

频道 major/minor 等组合字段不会拆成伪动作；应在 TD 中定义一个原子 `setChannel` schema 后再接入。
