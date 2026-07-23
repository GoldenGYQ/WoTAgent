# WoTAgent 运行、任务与并行智能体设计方案

状态：核心模型已落地，后续能力仍在演进

最后核对：2026-07-23

> 本文最初以“目标方案”编写。当前
> `Conversation -> Turn -> Run -> Task DAG -> AgentRun -> Loop -> ToolCall`
> 已成为真实运行模型；第 2 节描述的是重构前问题，不是当前缺陷。

## 1. 文档目标

本文定义 WoTAgent 的运行编排模型，解决旧版本“一轮用户消息等于一个
Task”的概念混乱，并为 Supervisor 动态拆解任务、多专业 Agent 协作、串并行调度、
独立重试、人工确认和分层 Trace 提供统一依据。

核心结论：

> 一轮对话产生一个 Run；一个 Run 可以动态包含多个有独立目标的 Task；一个
> Task 由一个专业 Agent 执行有限 Observe-Reason-Act 循环；Loop 和 Tool Call
> 是 Task 内部过程，不应被提升为顶层 Task。

## 2. 重构前问题（历史）

当前后端把同一个 `task_id` 同时用于：

- 一轮用户请求的运行标识；
- TraceStore 的记录主键和 `trace_id`；
- Conversation Turn 的唯一执行关联；
- LangGraph Checkpointer 的 `thread_id`；
- 前端消息卡片和时间线标识；
- 专业 Agent 执行结果标识。

当前真实链路是：

```text
Conversation
└── Turn
    └── task_id
        ├── Supervisor 路由
        ├── 一个专业 Agent
        ├── 若干 Loop
        ├── 若干 Tool Call
        └── Response
```

这套结构可以监控单次图运行，但不能准确表达：

- Supervisor 在一次请求中创建多个专项任务；
- 多个 Agent 并行调查；
- Task 之间的依赖关系；
- 每个 Task 独立的成功条件、证据、循环和用量；
- 局部失败、局部重试和补偿；
- Run 的部分成功与最终聚合。

## 3. 目标概念模型

```text
Conversation
└── Turn
    └── Run
        ├── Task A
        │   └── Agent Run
        │       ├── Loop 1
        │       │   └── Tool Call
        │       └── Loop 2
        ├── Task B
        │   └── Agent Run
        └── Task C
            └── Agent Run
```

### 3.1 对话（Conversation）

持久对话容器，负责保存用户与系统之间可跨轮使用的语义上下文。

标识：`conversation_id`

### 3.2 对话轮次（Turn）

一次用户输入和最终系统回复。Turn 是前端聊天界面的基本阅读单位。

标识：`turn_id`

### 3.3 运行实例（Run）

系统处理一个 Turn 的一次完整运行。重试同一 Turn 时可以创建新的 Run。

标识：`run_id`

职责：

- 保存根目标和运行状态；
- 聚合所有 Task；
- 提供 Run 级 Trace；
- 汇总 Token、耗时、工具调用和结果；
- 生成最终回复。

### 3.4 专项任务（Task）

具有独立目标、负责 Agent、预期证据和成功条件的专项工作单元。

标识：`task_id`

Task 必须满足：

- 有明确目标；
- 可以独立完成、失败、阻塞或取消；
- 可以被其他 Task 依赖；
- 值得独立监控、确认或重试。

### 3.5 智能体运行（AgentRun）

某个专业 Agent 执行一个 Task 的一次尝试。Task 重试时创建新的 Agent Run。

标识：`agent_run_id`

### 3.6 循环（Loop）

专业 Agent 内部的一次 Observe-Reason-Act 迭代。

标识方式：`agent_run_id + iteration`

Loop 不是 Task。多个 Loop 共同完成同一个 Task。

### 3.7 工具调用（ToolCall）

一次 MCP Resource 读取或 MCP Tool 调用。

标识：`tool_call_id`

## 4. Supervisor 与 Scheduler 的职责

### 4.1 Supervisor

Supervisor 是 LLM 驱动的主智能体，负责：

- 理解用户或 Monitor 提交的根目标；
- 动态提出 Task；
- 选择专业 Agent；
- 声明 Task 依赖；
- 提出串行或并行执行策略；
- 定义预期证据和可验证成功条件；
- 根据 AgentFinding 创建后续 Task；
- 判断 Run 是否达到目标；
- 生成面向用户的最终答复。

Supervisor 不负责：

- 分配真实 ID；
- 最终批准并行执行；
- 执行设备工具；
- 判断 RBAC 是否允许；
- 绕过 MCP Scope 或 TD；
- 忽略医疗和安全硬规则。

### 4.2 Scheduler

Scheduler 是确定性基础设施，负责：

- 为 Run、Task 和 Agent Run 分配 ID；
- 校验 Task DAG；
- 检测循环依赖；
- 检查 `depends_on` 是否满足；
- 执行最大并发数限制；
- 检查资源读写冲突；
- 获取设备或居民资源锁；
- 应用 RBAC、Policy、MCP Scope 和 TD 约束；
- 管理超时、取消、重试和人工确认；
- 调度就绪 Task；
- 汇总 Task 状态。

原则：

> Supervisor 拥有任务拆解、依赖分析和并行策略建议权；Scheduler 拥有并行执行
> 的最终裁决权。

## 5. Task DAG 与并行策略

Supervisor 输出结构化计划：

```json
{
  "goal": "检查张爷爷当前整体状态",
  "execution_strategy": "parallel_when_safe",
  "tasks": [
    {
      "local_id": "health",
      "agent_id": "health_agent",
      "objective": "调查最新健康状态和数据新鲜度",
      "depends_on": [],
      "expected_evidence": ["最新生命体征", "观测时间"],
      "success_criteria": ["获得已验证的最新健康数据"]
    },
    {
      "local_id": "environment",
      "agent_id": "environment_agent",
      "objective": "调查房间温湿度和空气状态",
      "depends_on": [],
      "expected_evidence": ["当前温度", "当前湿度"],
      "success_criteria": ["获得当前房间环境状态"]
    },
    {
      "local_id": "care",
      "agent_id": "care_agent",
      "objective": "在风险成立时通知护理人员",
      "depends_on": ["health"],
      "expected_evidence": ["通知送达结果"],
      "success_criteria": ["护理人员通知已验证送达"]
    }
  ]
}
```

程序把 `local_id` 转换为真实 `task_id`，并校验依赖。

### 5.1 允许并行

- 多个只读调查任务；
- 不同专业领域的独立查询；
- 不同设备上的无冲突操作；
- 多个独立证据来源；
- 不依赖彼此结果的 Agent Task。

### 5.2 必须串行

- 后一 Task 依赖前一 Task 的 Finding；
- 查询后根据结果执行条件分支；
- 动作执行后读取状态验证；
- 失败后执行替代或补偿；
- 等待人工确认的高风险动作。

### 5.3 资源冲突规则

Scheduler 至少区分：

- read/read：允许并发；
- read/write：按一致性要求决定；
- 同资源 write/write：串行；
- 高风险资源：人工确认后串行。

资源锁示例：

```text
device:ac-001
room:A-101
resident:elderly-001
security-zone:floor-1
```

## 6. 建议数据模型

```python
class RunRecord(BaseModel):
    run_id: str
    conversation_id: str
    turn_id: str
    root_goal: str
    status: RunStatus
    task_ids: list[str]
    final_response: str = ""


class AgentTask(BaseModel):
    task_id: str
    run_id: str
    parent_task_id: str | None
    agent_id: str
    objective: str
    status: TaskStatus
    expected_evidence: list[str]
    success_criteria: list[str]
    depends_on: list[str]
    resource_claims: list[ResourceClaim]
    execution_mode: Literal["sequential", "parallel_candidate"]
    finding: AgentFinding | None


class AgentRunRecord(BaseModel):
    agent_run_id: str
    run_id: str
    task_id: str
    agent_id: str
    attempt: int
    status: AgentRunStatus
    iteration_count: int
    tool_call_count: int
```

所有 LLM 输出使用 Pydantic 模型校验。LLM 不直接生成系统主键。

## 7. Trace 设计

每个事件统一包含：

```json
{
  "run_id": "run_xxx",
  "turn_id": "turn_xxx",
  "task_id": "task_xxx",
  "parent_task_id": null,
  "agent_run_id": "agent_run_xxx",
  "iteration": 2,
  "tool_call_id": "tool_call_xxx",
  "type": "agent.tool.completed",
  "node": "health_agent",
  "data": {}
}
```

建议事件：

- `run.started`
- `run.completed`
- `run.failed`
- `supervisor.plan.created`
- `task.created`
- `task.ready`
- `task.blocked`
- `task.started`
- `task.completed`
- `task.failed`
- `task.waiting_confirmation`
- `scheduler.parallel_group.started`
- `scheduler.resource_conflict.detected`
- `agent.run.started`
- `agent.loop.started`
- `agent.tool.started`
- `agent.tool.completed`
- `agent.finding.completed`
- `supervisor.goal.evaluated`

不记录模型隐式思维链，只记录结构化决策理由。

## 8. API 设计

### 8.1 新接口

```text
POST /api/v2/runs
GET  /api/v2/runs/{run_id}
GET  /api/v2/runs/{run_id}/tasks
GET  /api/v2/runs/{run_id}/events
GET  /api/v2/runs/{run_id}/tasks/{task_id}/agent-runs
POST /api/v2/runs/{run_id}/tasks/{task_id}/confirm
WS   /ws/v2
```

WebSocket 建立后由客户端发送 `{"run_id": "...", "after": 0}`。旧
`/api/v2/tasks` 与旧 Task Trace API 已删除，不再保留 `task_id == run_id`
的兼容语义。

## 9. LangGraph 落地

主图建议为：

```text
START
  → supervisor_plan
  → validate_task_dag
  → schedule_ready_tasks
  → dispatch_specialists
  → aggregate_findings
  → supervisor_evaluate
      ├── create_more_tasks → validate_task_dag
      ├── wait_confirmation
      └── finalize_run
  → END
```

并行执行优先使用 LangGraph `Send` 或显式并行子图，使每个 Task 在 Trace 中可见。
Scheduler 可以使用 `asyncio.gather` 执行同一就绪批次，但不能把并发工作隐藏成一个
不可观测节点。

专业 Agent 使用官方 `create_agent` 构建，并只暴露 AgentSpec 授权的工具。

## 10. 安全边界

必须保留：

- RBAC；
- Policy；
- AgentSpec；
- MCP Scope；
- TD 能力与参数校验；
- 幂等键；
- 执行后状态验证；
- 高风险人工确认；
- Monitor 医疗和安全硬规则；
- 最大任务数、最大并发数、最大循环数和总超时。

并行不能绕过上述任何约束。

## 11. 前端交互模型

聊天界面仍以 Turn 展示：

```text
用户消息
└── Run
    ├── Health Task       已完成
    ├── Environment Task  执行中
    ├── Security Task     已完成
    └── Care Task         等待依赖
```

展开 Task 后显示：

- 专业 Agent；
- Objective；
- 依赖；
- Loop；
- Tool Call；
- Evidence；
- AgentFinding；
- Token、耗时和错误。

前端默认订阅 Run 级事件，按 `task_id` 分组，不需要为每个 Task 建立独立 WebSocket。

## 12. 当前实现状态

截至 2026-07-23，已完成：

1. Run、Task、AgentRun、ResourceClaim 和结构化 SupervisorDecision。
2. Conversation Turn 从 `task_id` 迁移为 `run_id`。
3. 确定性 DAG Scheduler、依赖校验、资源冲突检测和安全并行。
4. `create_agent` Butler Supervisor 与 Health ReAct Agent。
5. Environment、Security、Energy、Housekeeping、DeviceOps 专业子图适配。
6. Run 级事件流、HTTP 查询和 WebSocket 回放。
7. 高风险 Task 等待确认，并恢复同一个 LangGraph checkpoint。
8. 前端 Run → Task 时间线和确认交互。
9. 删除旧 StructuredSupervisorRouter、TaskRunner、Task API 和侧栏 Trace。
10. SQLite RunStore 持久化 Run、Task、AgentRun、事件和待确认负载。
11. 服务重启后重建 Coordinator，并按 AgentSpec 恢复同一个 Task checkpoint。
12. 每个 Task 创建独立 AgentRun；同一 Agent 的无依赖只读 Task 可安全并行。
13. Supervisor 的资源声明只能收窄后端签发的有效范围，不能扩权。
14. Compact RunEvent 与按需 TraceArtifact 分离，前端按调度轮次、Task、
    Agent 和工具细节分层显示。

待后续：

1. 通用条件 Task 的 guard/skip 语义；当前跨 Agent 条件动作应分轮委派。
2. 为 Task 取消、独立重试和补偿增加接口。
3. 进程在普通 running Task 中途崩溃后的自动续跑。
4. 多后端实例部署时，将事件通知从进程内 Condition 替换为 Redis/PostgreSQL。
5. 外层 `RunCoordinator` 是否迁移为统一 LangGraph 主图仍是候选方向，不是
   当前已实现能力。

## 13. 验收标准

- 一个 Turn 可以产生一个 Run 和多个 Task；
- 无依赖且无资源冲突的 Task 可以并行；
- 有依赖的 Task 必须等待；
- Supervisor 可以建议并行，但 Scheduler 可以拒绝；
- 同一设备冲突写操作不会并发；
- 每个 Task 有独立 Finding、循环次数、Token 和状态；
- Run 可以表达成功、部分成功、失败和等待确认；
- 前端可以按 Run 查看任务树，并展开单个 Task；
- RBAC、MCP Scope、TD 和硬告警不能被 Supervisor 绕过；
- 旧 Task API 不再存在，所有交互统一使用 Run API；
- Fake LLM、Fake MCP 和确定性状态序列可以复现全部测试。
