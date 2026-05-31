# 感知引擎

## 概述

感知引擎是一个**基于规则的环境监控引擎**——它持续评估设备状态与预定义规则，无需 LLM 参与即可触发自动化动作。

与意图分类步骤（使用 LLM 进行路由）不同，感知引擎在规则求值上使用**零 LLM token**——所有条件检查都是纯 Python 比较。

## 架构

```
┌──────────────────────────────────────────────────┐
│              PerceptionEngine                     │
│  ┌──────────────┐    ┌──────────────────┐        │
│  │ 轮询周期      │───▶│ 规则求值          │        │
│  │ (漂移 →       │    │ (条件 →           │        │
│  │  轮询)        │    │  动作)            │        │
│  └──────────────┘    └────────┬─────────┘        │
│                               │                   │
│                        ┌──────▼──────┐            │
│                        │ 通过         │            │
│                        │ EventBus    │            │
│                        │ 发射         │            │
│                        └──────┬──────┘            │
└───────────────────────────────┼───────────────────┘
                                │
                ┌───────────────┴──────────────┐
                ▼                               ▼
        ┌────────────────┐            ┌──────────────────┐
        │ execute_plan   │            │ WebSocket        │
        │ directly()     │            │ (主要)           │
        │ (Executor →    │            │ → 前端           │
        │  Observer)     │            │                  │
        └────────────────┘            └──────────────────┘
```

### 自动执行模式（可选，默认关闭）

当 `.env` 中设置 `ENABLE_PERCEPTION_AUTO_EXEC=true` 时，触发的规则自动运行
``Executor → Observer`` 以执行动作（例如开启空调）。这会消耗
Executor 和 Observer 调用的 LLM token。

当禁用时（默认），规则仍向 EventBus 发射事件，前端能看到它们，
但**不采取任何动作**——仅为监控模式。

```python
# .env
ENABLE_PERCEPTION_AUTO_EXEC=true
```

### 两种触发路径

| 来源 | 路由 | LLM 成本 |
|------|------|----------|
| 用户说"开空调" | 意图分类器 → 执行器 → 观察器 | 意图 LLM + 执行器 LLM |
| 规则: temp > 32°C | 直接执行器 → 观察器 | 仅执行器 LLM（跳过意图分类） |

### 规则 → LLM 上下文注入

用户发送消息时，当前环境状态作为 `perception_context` 注入到意图分类器的提示词中：

```
[当前环境状态]
ac-001(currentTemperature=33.5) | light-001(on=False) | humidifier-001(currentHumidity=45.2)
```

这有助于意图分类步骤和下游提示词在轮次之间保持上下文，无需额外的传输调用。

## 模块结构

```
src/wotagent/perception/
├── __init__.py       # 公共 API 导出
├── rules.py          # Condition, RuleAction, EnvironmentRule 模型 + 默认规则
└── engine.py         # DeviceStateStore, PerceptionEngine, 单例
```

## 核心组件

### `rules.py` — 规则模型

```python
@dataclass
class Condition:
    device_id: str
    property_name: str    # 例如 "currentTemperature"
    operator: str          # gt | lt | gte | lte | eq | ne
    value: float | int     # 阈值

@dataclass
class RuleAction:
    intent: str            # "control" | "query"
    steps: list[dict]      # 与 PipelinePlan.steps 格式相同

@dataclass
class EnvironmentRule:
    name: str
    description: str
    condition: Condition
    action: RuleAction
    cooldown_seconds: int = 300   # 防止重复触发
    enabled: bool = True
    last_triggered: float = 0.0
```

### 默认规则

| 规则 | 条件 | 动作 | 冷却 |
|------|------|------|------|
| `high_temperature` | ac-001.currentTemperature > 32°C | 空调 → setMode(cool), setTemperature(26) | 10 分钟 |
| `low_humidity` | humidifier-001.currentHumidity < 30% | 加湿器 → turnOn | 10 分钟 |

### `engine.py` — DeviceStateStore

一个基于 **SQLite** 的仿真层，维护数值型属性值并应用随机漂移。
使用 WAL 日志模式实现跨进程一致性：

- **存储**：`data/state.db` 的 SQLite 数据库，WAL 模式（`PRAGMA journal_mode=WAL`）
- **并发**：多个进程（Agent + 模拟器）可同时读写
- **重置**：`reset()` 清空所有行；`initialize()` 从 Thing Description 重新种子默认值
- **温度**：每次轮询漂移 +0.6/-0.2（空调关闭时上升，开启时下降）
- **湿度**：每次轮询漂移 ±2.0
- **布尔值**（on/off）：仅在通过 `set_property()` 控制时改变

漂移确保规则最终会触发，模拟真实的环境变化。

**注意**：模拟器项目（`wot-device-simulator`）和 Agent 共享同一个
`DeviceStateStore`——通过 MCP 工具调用引起的设备状态变化立即对
感知引擎可见，无需轮询。

### `engine.py` — PerceptionEngine

```python
engine = PerceptionEngine(polling_interval=60)  # 每 60 秒轮询一次
engine.start()        # 后台 asyncio 任务
engine.poll_once()    # 手动单次轮询
engine.stop()         # 取消后台任务
engine.get_context()  # 用于 LLM 提示词注入的文本摘要
```

当规则触发时：
1. `mark_triggered()` 设置 `last_triggered` 以用于冷却
2. 向 EventBus 发送 `wot.perception.rule_triggered` 事件
3. 触发计划返回，可选择通过 `execute_plan_directly()` 执行

### `core/agent.py` — execute_plan_directly()

```python
from wotagent.core import execute_plan_directly

final_text = await execute_plan_directly(
    pipeline, plan_json, plan,
    session_id="sess_xxx",
    user_role="operator",
    memory=memory,
)
```

完全跳过意图分类，使用预结构化计划运行 Executor → Observer。

## REPL 用法

```
/perception             显示引擎状态 + 当前环境
/perception on|off      启动 / 停止引擎
/perception rules       列出所有规则及状态
/perception poll        立即触发一次轮询周期
```

## API 端点

| 方法 | 路径 | 描述 |
|------|------|------|
| `GET` | `/api/perception/state` | 当前环境状态 + 引擎统计 |
| `GET` | `/api/perception/rules` | 所有规则及条件/状态 |
| `POST` | `/api/perception/poll` | 触发一次轮询周期 |

## 事件类型

| 类型 | 负载 | 何时触发 |
|------|------|---------|
| `wot.perception.rule_triggered` | `{rule, description, device_id, property, value, threshold, plan}` | 规则条件匹配 |
| `wot.perception.state` | (保留用于定期状态快照) | 将来使用 |

## 配置

| 环境变量 | 默认值 | 描述 |
|---------|--------|------|
| `ENABLE_PERCEPTION` | `true` | 启动时启用感知引擎 |
| `PERCEPTION_INTERVAL` | `60` | 轮询间隔（秒） |
