# WoTAgent 2.0 如何利用 SimuHome

这份文档用尽量直白的方式解释：SimuHome 是什么，WoTAgent 为什么要接入它，一条指令在系统中经历了什么，以及这次实验真正证明了什么。

## 1. 先说结论

我们没有把 SimuHome 当成 WoTAgent 的一部分，也没有使用它替换自己的 Agent。

我们主要利用了 SimuHome 的两样东西：

1. **设备仿真 Runtime**：它提供房间、灯、空调、风扇、窗帘、冰箱等虚拟设备，并按照 Matter 风格保存设备属性、接收设备命令。
2. **Benchmark episode**：它提供自然语言任务、初始家庭状态、要求发生的工具调用和最终设备状态断言。

WoTAgent 负责理解指令、生成计划、检查权限、执行动作和验证结果；SimuHome 负责扮演“真实设备世界”和“裁判”。

最终建立的链路是：

```text
SimuHome episode
  -> 根据 Matter 设备属性生成动态 TD
  -> 根据 TD 自动生成 FastMCP 工具
  -> MCP Client 发现工具
  -> Planner 生成 TaskPlan
  -> Policy 检查计划
  -> Executor 通过 MCP 工具执行
  -> SimuHomeBinding 翻译成 SimuHome REST/Matter 操作
  -> Verifier 再通过 MCP 读取状态
  -> 官方规则检查最终设备属性
```

## 2. SimuHome 到底是什么

可以把 SimuHome 想象成一个运行在电脑里的虚拟住宅。

每个 benchmark episode 都包含四部分：

| 内容 | 作用 |
|---|---|
| `query` | 用户的自然语言指令 |
| `initial_home_config` | 执行前有哪些房间、设备及其初始状态 |
| `required_actions` | 系统应该完成的设备发现操作 |
| `goals/asserts` | 执行后必须满足的 Matter 属性断言 |

例如 seed 7 的任务可以简化成：

```json
{
  "query": "打开客厅灯 1",
  "initial": {
    "living_room_on_off_light_1": {
      "1.OnOff.OnOff": false
    }
  },
  "required_action": {
    "tool": "get_room_devices",
    "room_id": "living_room"
  },
  "goal": {
    "device_id": "living_room_on_off_light_1",
    "1.OnOff.OnOff": true
  }
}
```

因此，不能只让大模型回复“已经打开”。只有 SimuHome 中的 `1.OnOff.OnOff` 真的从 `false` 变成 `true`，这一集才算成功。

## 3. 为什么不能直接把 SimuHome API 交给大模型

SimuHome 暴露的是自己的 REST API 和 Matter 属性，例如：

```text
GET  /api/devices/{device_id}/attributes
POST /api/devices/{device_id}/commands
POST /api/devices/{device_id}/attributes/write
```

如果 Planner 直接记住这些 URL、Matter cluster 和参数名，会产生三个问题：

1. Planner 与 SimuHome 强耦合，换成 ESP32 或其他平台就要重新设计提示词。
2. 大模型可能编造 endpoint、cluster、command 或参数。
3. 项目原本要求的 `TD -> Tool Schema -> MCP Server -> MCP Client` 主链路被绕过。

所以我们把“理解用户”与“理解设备协议”分开：

- 大模型只处理语义层动作，例如 `turnOn`、`setSpeed`、`setPosition`。
- 确定性代码负责把语义层动作翻译成 Matter/REST 操作。

## 4. 第一次尝试为什么不够好

最开始为了快速确认 SimuHome 能否运行，我们写了一个直连适配器，让 Executor 直接调用 SimuHome REST API。

这条路径能够证明：

- episode 可以重置；
- REST 命令可以改变设备状态；
- 最终状态可以按照 benchmark 规则评分。

但它绕过了 TD 和 MCP。因此，它只能叫“Runtime 连通性实验”，不能用来证明 WoTAgent 的完整技术路线。

随后我们修正了架构：

- 将直连对象改名为 `SimuHomeBinding`；
- Binding 只存在于 MCP 工具后面；
- Agent 面前仍然只有统一的 `MCPDeviceGateway`。

这里的 **Binding** 可以理解为“协议翻译器”，而 **Gateway** 是 Agent 面向所有设备的统一入口。这两个角色不能混为一谈。

## 5. 最终一次任务经历了什么

下面以“打开客厅灯 1”为例，沿真实代码路径走一遍。

### 第一步：重置 episode

评测器把 `initial_home_config` 发给 SimuHome：

```text
POST /api/simulation/reset
```

这样每一集都从自己的初始状态开始，不会受到上一集设备状态的污染。

### 第二步：从 Matter 属性生成设备能力

系统读取 episode 中的设备及属性。例如发现：

```text
1.OnOff.OnOff = false
```

代码据此判断该设备支持：

- `turnOn`
- `turnOff`
- 可读取属性 `on`

如果还存在 `1.LevelControl.CurrentLevel`，则会增加 `setBrightness`；如果存在 `1.FanControl.PercentSetting`，则会增加 `setSpeed`。

也就是说，支持什么动作不是大模型猜出来的，而是由设备实际暴露的 Matter 属性确定的。

### 第三步：生成动态 TD

设备能力被转换成一份 Thing Description。概念上类似：

```json
{
  "id": "urn:simuhome:living_room_on_off_light_1",
  "title": "Light 1 (on_off_light)",
  "location": "living_room",
  "properties": {
    "on": {"type": "string"}
  },
  "actions": {
    "turnOn": {
      "input": {
        "type": "object",
        "properties": {},
        "additionalProperties": false
      }
    }
  }
}
```

这里叫“动态 TD”，是因为不同 episode 的房间和设备数量不同，不能提前写死一套 TD 文件。

需要注意：当前实验版 TD 已经精确定义了 action 的参数 schema，但 property 的类型暂时使用通用 `string` 占位，真正的布尔值、数字和单位由 Binding 读取并归一化。后续接真实设备时，应继续完善 property 的准确类型、单位和约束。

### 第四步：TD 自动编译成 MCP 工具

TD Tool Compiler 将 TD affordance 编译为具名工具：

```text
living_room_on_off_light_1__read_state
living_room_on_off_light_1__action__turn_on
```

动作的 JSON Schema 直接来自 TD。这样 Planner、Policy 和 MCP Server 使用的是同一份参数契约。

### 第五步：MCP Client 发现工具

`MCPDeviceGateway` 不维护另一张手写设备表。它调用 MCP `list_tools`，再根据工具 metadata 重建 Planner 所需的能力目录。

因此，能力来源保持单一：

```text
Matter 属性 -> 动态 TD -> MCP 工具 metadata -> Planner 能力上下文
```

### 第六步：Planner 生成 TaskPlan

大模型看到用户指令以及裁剪后的设备能力，生成结构化计划：

```json
{
  "intent": "control",
  "steps": [
    {
      "device_id": "living_room_on_off_light_1",
      "action": "turnOn",
      "input": {},
      "postconditions": [
        {"property": "on", "operator": "eq", "value": true}
      ]
    }
  ]
}
```

大模型负责选择设备和动作，但不能填写 TD 中不存在的设备、动作或参数。

### 第七步：Policy 做确定性检查

Policy Engine 检查：

- 当前角色是否有控制权限；
- 设备是否真实存在；
- `turnOn` 是否在该设备 TD 中声明；
- 输入是否符合 TD JSON Schema；
- 是否属于需要人工确认的高风险操作。

这些判断不交给大模型。

### 第八步：Executor 调用 MCP 工具

Executor 最终调用：

```text
living_room_on_off_light_1__action__turn_on
```

MCP Server 收到调用后，将它交给 `SimuHomeBinding`。

Binding 将 `turnOn` 翻译成：

```json
{
  "endpoint_id": 1,
  "cluster_id": "OnOff",
  "command_id": "On",
  "args": {}
}
```

然后调用 SimuHome 的 `/devices/{device_id}/commands`。

### 第九步：Verifier 重新读取设备状态

执行成功不等于任务成功。Verifier 会调用：

```text
living_room_on_off_light_1__read_state
```

它再次经过 MCP 和 Binding 读取 SimuHome 属性，并将：

```text
1.OnOff.OnOff = true
```

归一化为：

```json
{"on": true}
```

随后检查 TaskPlan 中的后置条件 `on == true`。

### 第十步：Benchmark 再做一次独立评分

WoTAgent 内部验证通过后，评测器还会读取完整 SimuHome home state，并按照原 episode 中的 Matter 属性断言精确比较。

因此有两层验证：

1. **WoTAgent Verifier**：检查语义状态，例如 `on == true`。
2. **SimuHome evaluator**：检查原始 Matter 状态，例如 `1.OnOff.OnOff == true`。

这样可以防止归一化层或回复层“自说自话”。

## 6. 支持了哪些动作翻译

当前 Binding 已实现的主要映射包括：

| WoT 动作 | SimuHome/Matter 操作 |
|---|---|
| `turnOn/turnOff` | `OnOff.On/Off` command |
| `setBrightness` | `LevelControl.MoveToLevel` command |
| `setSpeed` | 写入 `FanControl.PercentSetting` |
| `setMode` | 写入 `Thermostat.SystemMode` |
| `setTemperature` | 温控 command 或恒温器 setpoint write |
| `setPosition` | 写入窗帘目标位置 |
| `start/stop/pause/resume` | `OperationalState` command |
| `setDrynessLevel` | 写入烘干等级 |
| `setWasherMode` | 写入洗衣模式 |
| `setChannel` | 原子频道切换 command |

这些映射是普通程序逻辑，执行时不消耗大模型 Token。

## 7. 过程中做了哪些改进

### 改进一：从静态映射变成真实 Runtime 实验

之前的 `simuhome_alignment_600.json` 只是在统计“这些断言理论上能否映射到 WoT 动作”。99.15% 是静态语义覆盖率，不是 Agent 跑分。

现在真正启动了官方 Runtime，执行命令并检查最终状态，得到的是运行时证据。

### 改进二：从 REST 直连改为 TD/MCP 主链路

直连方案只保留为早期调试经验。最终 Agent 使用的是：

```text
dynamic TD -> FastMCP -> MCPDeviceGateway
```

SimuHome 只位于最底层 Binding，因此以后替换为 ESP32 binding 时，上层 Agent 不需要改变基本结构。

### 改进三：修复设备名称歧义

SimuHome 同一房间可能同时存在：

- `Light 1`
- `Dimmer Light 1`

模型曾把“bathroom light 1”错误地理解成调光灯。我们为动态设备增加了人类可读 title 和 alias，并规定优先匹配完整称谓；没有出现 `dimmer` 时，不能擅自添加该修饰词。

窗帘的自然语言 `blinds` 与设备类型 `window_covering_controller` 也增加了语义别名。

### 改进四：减少无关能力上下文

Planner 会根据用户提到的房间、设备类型和 alias 裁剪能力目录，而不是把全部设备 schema 都塞进提示词。

这可以降低：

- 输入 Token；
- 选错相似设备的概率；
- 无关设备对 Planner 的干扰。

### 改进五：保留完整实验痕迹

正式评测目录会保存：

| 文件 | 内容 |
|---|---|
| `config.resolved.json` | 模型、episode、角色、Runtime 地址和架构模式 |
| `dynamic_tds.jsonl` | 每个 episode 真正生成的 TD |
| `episode_results.jsonl` | 用户指令、计划、工具调用、断言和 Token |
| `trace_events.jsonl` | Planner、Policy、Executor、Verifier、Responder 事件时间线 |
| `summary.json/csv` | 汇总指标 |
| `analysis.md` | 人类可读实验报告 |

因此，某一集失败时可以定位究竟是：

- 设备发现失败；
- 动态 TD 缺少能力；
- Planner 选错设备；
- Policy 拒绝；
- MCP 工具调用失败；
- Matter 状态没有达到目标；
- 回复与执行事实不一致。

## 8. 实验结果

最终使用 `deepseek-v4-flash`，在 qt3 feasible 中选择 5 个代表性 episode，覆盖：

- 灯开关；
- 风扇速度；
- 多房间组合控制；
- 窗帘位置；
- 空调模式与速度；
- 冰箱温度；
- 调光灯亮度。

结果如下：

| 指标 | 结果 |
|---|---:|
| Episode success | 5/5 |
| Required action success | 100% |
| Matter assertion accuracy | 11/11 |
| Agent execution success | 100% |
| 平均 Token/episode | 3702.2 |
| 平均 TTFT | 1243.7 ms |
| 平均端到端延迟 | 3856.1 ms |
| 平均估算费用 | 0.003634 CNY/episode |
| 完整 trace event | 77 条 |
| 保存的动态 TD | 144 份 |

这里的 144 份 TD 是 5 个 episode 分别生成的设备 TD 数量之和，不代表有 144 台固定设备。

## 9. 我们能得出什么结论

### 已经能够证明

1. SimuHome Matter 设备可以自动转换为 WoT 语义能力。
2. 动态 TD 可以自动生成 MCP 工具，并被 MCP Client 发现。
3. Planner 可以基于这些动态能力生成可执行 TaskPlan。
4. Executor 和 Verifier 可以通过 MCP 完成真实状态闭环。
5. 代表性 qt3 即时控制任务可以在原始 SimuHome Runtime 中成功运行。
6. 完整过程可以被 trace、Token 和最终状态指标复盘。

### 还不能证明

1. **不能说 SimuHome 600 集全部跑通。** 当前只真实运行了 5 个 qt3 feasible episode。
2. **不能说时序任务已经完成。** qt1、qt2、qt4 涉及虚拟时间、环境变化、锚点事件和工作流调度。
3. **不能用 5/5 宣称模型具有普遍稳定性。** 还需要扩大样本并进行多次重复运行。
4. **不能直接等价为真实 ESP32 实验。** ESP32 仍需要自己的 TD、传输协议和 binding，但上层 TD/MCP/Agent 结构可以复用。

## 10. 一句话介绍这项工作

可以这样介绍：

> 我们将 SimuHome benchmark 中的 Matter 设备配置动态转换为 WoT Thing Description，再自动编译为 FastMCP 工具，由 WoTAgent 的 MCP Client、Planner、确定性 Executor 和 Verifier 完成自然语言设备控制，并通过 SimuHome 原始 Matter 状态断言进行闭环评测；当前在 5 个代表性 qt3 feasible episode 上实现 5/5 成功，并保留了动态 TD、完整 trace、Token、TTFT 和最终状态证据。

这句话强调的是已经真实实现的链路，同时没有夸大成全量 benchmark 或真实硬件已经完成。

## 11. 自己如何运行和检查

### 11.1 转换程序在哪里

当前转换不是一段提示词，而是固定的 Python 程序，主要分为四层：

| 层 | 代码 | 作用 |
|---|---|---|
| Matter 能力识别 | `wotagent/gateway/simuhome.py::_device_capability` | 根据属性 key 判断设备支持哪些属性和动作 |
| 动态 TD 生成 | `wotagent/evaluation/simuhome_mcp.py::capabilities_to_tds` | 将设备能力记录转换为 `ThingDescription` |
| MCP 工具编译 | `wotagent/wot/tool_compiler.py::compile_td_tools` | 根据 TD 生成 read/action 工具名和 JSON Schema |
| 执行协议绑定 | `wotagent/gateway/simuhome.py::SimuHomeBinding._binding` | 把 WoT 动作翻译成 SimuHome REST/Matter command 或 attribute write |

完整组装入口是：

```text
wotagent/evaluation/simuhome_mcp.py::SimuHomeMCPHarness.create
```

它依次完成：reset episode、发现设备、生成 TD、构建 FastMCP Server、创建 MCP Client 和 `MCPDeviceGateway`。

### 11.2 准备 SimuHome 环境

第一次运行时，在项目根目录执行：

```powershell
cd .cache\benchmarks\SimuHome
uv sync
```

`uv sync` 会根据 SimuHome 的 `pyproject.toml` 安装它需要的 Python 3.13 和依赖。已经安装过则不需要重复执行。

### 11.3 启动 SimuHome Runtime

```powershell
cd D:\Users\gyq16\Desktop\PRJ\WoTAgent\.cache\benchmarks\SimuHome
uv run simuhome server-start --port 18081
uv run simuhome health --port 18081
```

看到 `[health] OK`，说明 Runtime 可用。使用 `18081` 是为了避免与 WoTAgent API 的 `8000` 和原设备仿真器的 `18080` 冲突。

### 11.4 只查看动态 TD，不调用大模型

在另一个 PowerShell 中执行：

```powershell
cd D:\Users\gyq16\Desktop\PRJ\WoTAgent\wotagent-prototype

.\.venv\Scripts\python.exe scripts\inspect_simuhome_td.py `
  --episode ..\.cache\benchmarks\SimuHome\data\benchmark\qt3_feasible_seed_7.json `
  --output data\evaluation\manual\simuhome_seed7_inspection.json
```

这条命令会：

1. 重置 seed 7 的虚拟家庭；
2. 生成动态 TD；
3. 编译并发现 MCP 工具；
4. 读取当前状态并按 episode 目标评分；
5. 将完整结果写入指定 JSON 文件。

它不会调用 LLM，因此报告中应看到：

```json
{
  "llm_called": false,
  "token_cost": 0
}
```

因为此时还没有执行开灯动作，初始状态通常不满足目标，所以 `evaluation.score` 可能为 0。这是正常的。

### 11.5 手动通过 MCP 执行动作并评分

继续使用 seed 7：

```powershell
.\.venv\Scripts\python.exe scripts\inspect_simuhome_td.py `
  --episode ..\.cache\benchmarks\SimuHome\data\benchmark\qt3_feasible_seed_7.json `
  --device-id living_room_on_off_light_1 `
  --action turnOn `
  --output data\evaluation\manual\simuhome_seed7_turn_on.json
```

本机实测输出的关键信息为：

```text
LLM called: false
Token cost: 0
Dynamic TD count: 26
MCP tool count: 110
Called MCP tool: living_room_on_off_light_1__action__turn_on
Verification MCP tool: living_room_on_off_light_1__read_state
Observed on: true
Matter assertions: 1/1
Episode score: 1
```

这条命令证明的是确定性链路，不评价 Planner 的自然语言理解能力。

带参数动作使用 `--arguments`。例如检查风速动作：

```powershell
--device-id bathroom_humidifier_1 `
--action setSpeed `
--arguments '{"speed":30}'
```

设备 ID 和动作名必须存在于该 episode 生成的 `capabilities_from_mcp` 中，否则 MCP Client 会拒绝调用。

### 11.6 运行包含 LLM Planner 的完整评测

确认 `wotagent-prototype/.env` 中配置了模型后执行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_simuhome_v2.py `
  --benchmark-root ..\.cache\benchmarks\SimuHome\data\benchmark `
  --base-url http://127.0.0.1:18081/api `
  --seeds 7 `
  --limit 1 `
  --output-dir data\evaluation\runs\my_seed7_run
```

这次会调用 Planner 和 Response 模型，会产生 Token 消耗。它测试的是完整流程：

```text
自然语言 -> TaskPlan -> Policy -> MCP Action -> Verification -> Response -> Benchmark score
```

### 11.7 应该重点检查哪些字段

打开检查脚本或完整评测生成的 JSON 后，重点看：

| 字段 | 说明 |
|---|---|
| `tds` | Matter 设备最终被转换成了什么 TD |
| `capabilities_from_mcp` | MCP Client 实际发现了哪些设备能力 |
| `mcp_tools` | 自动生成的工具名、schema 和 metadata |
| `manual_execution.result._mcp_tool` | 实际调用的具名 MCP action tool |
| `manual_execution.observed_state._mcp_tool` | 验证时使用的 MCP read tool |
| `binding_invocations` | MCP 工具最终执行了什么 REST/Matter 操作 |
| `evaluation.required_actions` | 房间设备发现要求是否满足 |
| `evaluation.targets_eval` | 每条预期值、实际值和差异 |
| `evaluation.score` | required action 和全部状态断言是否同时通过 |

### 11.8 关闭测试 Runtime

优先使用 SimuHome 自带命令：

```powershell
cd D:\Users\gyq16\Desktop\PRJ\WoTAgent\.cache\benchmarks\SimuHome
uv run simuhome server-stop --port 18081
```

如果该命令在 Windows 上报进程工具缺失，可先确认 `18081` 确实属于 SimuHome，再停止监听进程：

```powershell
$conn = Get-NetTCPConnection -LocalPort 18081 -State Listen
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)"
$process.CommandLine
Stop-Process -Id $conn.OwningProcess
```

不要在未检查 `CommandLine` 的情况下停止一个不确定归属的进程。
