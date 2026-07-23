# WoTAgent 多主体抽象与面试表达指南

最后核验：2026-07-23
适用代码：`trae/run-task-dag`，当前开发目标 `v3.0.0`

## 1. 这份文档解决什么问题

这不是另一份完整技术手册，而是一份面向求职表达的主文档，重点回答：

1. WoTAgent 中的人、软件 Agent、领域、设备和权限应该怎样抽象；
2. 同一个专业 Agent 能否被调用多次，实例、Task、AgentRun 和 Loop 是什么关系；
3. 当前养老场景为什么看起来只有少数领域 Agent，应该怎样继续增强；
4. 面向私企和央国企时，怎样用不同重点介绍同一个项目；
5. 哪些能力已经实现、哪些只能作为设计方向，避免面试时讲过头。

先记住一句总纲：

> WoTAgent 不是“让一个大模型直接控制所有设备”，而是一个把自然语言目标转换为受权限、设备能力和安全规则约束的多 Agent 任务执行过程，并对执行结果进行验证和审计的物联网智能体平台。

## 2. 先把五种“角色”分开

项目中最容易混淆的是“护理员是角色，Care Agent 也像角色，保安既可以是用户又可以是 Agent”。正确做法不是继续增加名字，而是分清五类对象。

### 2.1 人类主体（Human Principal）：谁在使用系统

`Principal` 是真实用户或外部服务的可信身份，例如：

- 别墅业主；
- 物业保安；
- 护理员；
- 观察员；
- 系统管理员；
- Monitor 事件源。

Principal 携带角色、关系和上下文，但它不是软件 Agent。

```text
Principal
├── role: operator / viewer / admin
├── relationship: caregiver_of elderly-001
├── resource scope: resident:elderly-001
└── context: profile, site, shift, request source
```

### 2.2 人类角色（Human Role）：这个人原则上能做什么

Role 是权限模板，不应该与具体设备清单画等号。

例如：

```text
护理员 -> 可以读取所负责居民的健康摘要、创建照护任务
观察员 -> 可以读取授权对象的状态，不能执行设备控制
别墅业主 -> 可以控制所属住宅中的普通设备
保安 -> 可以访问管辖区域中的安防能力
```

最终授权不是只看 Role，而是：

```text
角色能力
AND 用户与目标的关系
AND 当前场景和资源范围
AND 操作上下文
```

### 2.3 软件智能体（Software Agent）：系统把任务委派给谁

软件 Agent 按稳定的专业能力分类，不按人类岗位命名。

当前全局 `AgentSpec` 包含：

| Agent | 抽象能力 | 典型职责 |
|---|---|---|
| `health_agent` | 健康信息分析 | 调查生命体征、趋势和数据质量 |
| `care_agent` | 照护协同 | 通知护理员、创建照护任务 |
| `environment_agent` | 环境控制 | 灯光、空调、空气和湿度设备 |
| `security_agent` | 安防处置 | 门锁、摄像头、烟雾和燃气设备 |
| `energy_agent` | 能耗管理 | 能耗读取和受限负载调度 |
| `housekeeping_agent` | 家务执行 | 清洁和家务设备 |
| `device_ops_agent` | 设备运维 | TD、MCP、在线状态和接入异常 |

这些 Agent 是平台能力。养老院和别墅不应各自复制一套环境或安防 Agent，而应通过领域配置选择、约束和组合它们。

### 2.4 领域与部署场景（Domain / Deployment Profile）：Agent 在什么世界里工作

领域不是一个巨大的业务代码目录，也不是 Agent 本身。当前项目将其拆成两层：

- `Domain`：养老照护、智能家居等业务语义；
- `DeploymentProfile`：某次运行实际使用的站点、空间、设备、提示词和可用 Agent。

例如：

```text
eldercare profile
├── site: nursing-home-demo
├── spaces: A-101, A-102
├── devices: 手环、床垫、环境传感器、房间设备
├── prompt: 养老照护边界
└── enabled agents: health, care, environment...

villa profile
├── site: villa-demo
├── spaces: 客厅、卧室、厨房、车库...
├── devices: 灯、空调、门锁、摄像头、扫地机...
├── prompt: 智能家居边界
└── enabled agents: environment, security, energy...
```

因此更准确的抽象是：

> Domain 组合业务语义，Deployment Profile 划定运行世界，AgentSpec 定义可复用的软件能力。

### 2.5 资源与能力（Resource / Capability）：Agent 最终能碰什么

设备、居民数据、空间和风险事件都是受保护资源。能力采用稳定标识，例如：

```text
health.status.read
care.coordinate
environment.control
wot.security
```

Supervisor 可以提出任务和能力意图，但最终 `effective_scope` 由后端根据 Principal、关系、Profile、AgentSpec 和设备 TD 共同签发。

## 3. 同一个 Agent 能否被调用多次

结论是：**支持，而且当前运行模型本来就是这样设计的。**

但“Agent 被调用多次”要分成三个层次。

### 3.1 AgentSpec 不是运行实例

`AgentSpec` 是能力蓝图，类似岗位说明书：

```text
AgentSpec = 身份 + 职责 + 工具范围 + MCP Scope + 最大循环次数
```

它不会因为执行一次任务就消失，也不保存某个用户任务的临时状态。

### 3.2 每次委派产生独立 Task 和 AgentRun

当前层级为：

```text
Conversation
└── Turn
    └── Run
        ├── Task A -> AgentRun A
        ├── Task B -> AgentRun B
        └── Task C -> AgentRun C
```

每次 Supervisor 委派都会创建新的 `AgentTask`。Scheduler 给 Task 分配真实 `task_id`；Task 开始执行时，`RunCoordinator` 再创建独立的 `agent_run_id`。

因此下面的规划是合法的：

```text
Task A -> environment_agent -> 查询一楼环境
Task B -> environment_agent -> 查询二楼环境
Task C -> environment_agent -> 调整 A-101 空调
```

三个 Task 可以使用同一个 `agent_id`，但拥有不同的目标、上下文、证据、状态和 Trace。

### 3.3 可复用的是执行器，隔离的是任务状态

后端启动时会构建并复用：

- 一个 Butler Supervisor runnable；
- 一个 Health ReAct runnable；
- 一个 Care ReAct runnable；
- 每类设备专业 Agent 对应的一份已编译 LangGraph。

调用同一 Agent 时不会每次重新加载模型、重新注册工具或重新编译图。运行时复用 Agent 执行器，但为每个 Task 注入独立输入。

设备 Agent 的 LangGraph 使用：

```text
thread_id = task_id
```

因此设备图的 checkpoint 按 Task 隔离，可以在人工确认后恢复同一个 Task。

Health 和 Care 当前复用同一个 `create_agent` runnable，但每次调用传入新的消息和授权 Scope；它们当前没有跨 Task 的内部 checkpointer。跨 Run 记忆来自 ConversationState 和 Supervisor 上下文，而不是某个专业 Agent 私自保留聊天记忆。

### 3.4 同一个 Agent 的两个 Task 能否并行

结构上允许，是否真正并行由 Scheduler 决定：

1. Task 都已满足 `depends_on`；
2. 执行策略不是 `sequential`；
3. Task 被标记为并行候选；
4. 没有资源读写冲突；
5. 没有超过最大并发数，当前默认最多 4 个 Task。

资源冲突的基本规则是：

```text
read + read   -> 可以并行
read + write  -> 同一资源冲突
write + write -> 同一资源冲突
```

所以两个 `environment_agent` 查询不同房间可以并行；两个 Task 同时写同一台空调会被串行化。

### 3.5 同一 Task 是拆成多个 Agent 实例，还是由一个 Agent 长程完成

当前设计遵循“最小必要 Task”原则：

- 同一专业能力范围内的观察、判断、动作和验证，应优先作为一个 Task；
- 该专业 Agent 在 Task 内进行有限 Observe-Reason-Act 循环；
- 只有跨专业边界、需要独立监控、可以并行或存在明确交接时才拆 Task。

例如：

```text
“查询所有灯，若开灯数为奇数则开空调”
```

如果灯和空调都属于 `environment_agent` 的授权能力，应创建一个 Task，让它在一个有限循环中完成查询、计算、动作和验证，而不是拆成三个顶层 Task。

```text
“检查健康状态，若异常则通知护理员”
```

这是跨专业条件工作流：

1. 第一轮只创建 `health_agent` 调查 Task；
2. Health 返回结构化 Finding；
3. Supervisor 二次决策；
4. 只有异常成立时才创建 `care_agent` Task。

`depends_on` 只表示执行顺序，不表示“条件成立才执行”。这是项目真实运行中踩过的一个重要坑。

### 3.6 当前长程能力的边界

当前不是无限长程 Agent：

- Supervisor 默认最多 4 轮决策；
- Health 默认最多 4 轮模型决策；
- Device Agent 默认最多 4 轮；
- 全局工具调用默认最多 16 次；
- 专业执行有总超时；
- Care 工具调用进一步限制为最多 4 次；
- 重复不可恢复失败会通过 failure fingerprint 终止。

这不是限制智能，而是防止 Agent 在真实设备上无限试错。

## 4. 当前规划到底怎样产生

真实链路是：

```text
用户请求 / Monitor 事件
-> RunService 创建 Conversation / Turn / Run
-> Butler create_agent 生成 SupervisorDecision
-> 后端校验结构并规范化 Principal、Target、Capability、Scope
-> TaskScheduler 物化 Task DAG
-> 执行就绪 Task，必要时有限并行
-> 专业 Agent 返回 AgentFinding
-> Butler 读取全部 Finding 再次决策
-> 创建后续 Task、澄清或结束 Run
```

LLM Supervisor 负责：

- 理解根目标；
- 选择专业 Agent；
- 提出 Task、依赖、预期证据和成功条件；
- 建议串行或并行；
- 根据 Finding 决定下一步；
- 生成最终答复。

确定性程序负责：

- 校验 Agent 是否注册、Task DAG 是否有效；
- 分配 ID；
- 解析规范实体；
- 签发授权 Scope；
- 检查 RBAC、关系权限、MCP Scope 和 TD；
- 裁决资源冲突和并行；
- 执行工具并验证设备状态；
- 管理超时、中断、恢复和审计。

面试时不要把它描述成“规则系统冒充 Agent”，也不要描述成“LLM 完全自治”。更准确的表达是：

> 我采用受约束智能体架构，把目标拆解、证据判断和后续委派交给 LLM，把权限、设备能力、执行验证和风险规则留在确定性边界内。

## 5. 养老场景到底有几个 Agent

从平台注册表看，当前有 7 类专业 Agent。养老场景中最具有领域特异性的两个是：

- `health_agent`；
- `care_agent`。

养老场景当前已经形成实际链路的共享能力主要是：

- `environment_agent` 调节房间环境；
- `device_ops_agent` 检查手环、床垫和网关接入；

养老 Profile 配置中也启用了 `security_agent`、`energy_agent` 和
`housekeeping_agent`，但当前养老设备目录和业务流程尚未为它们提供完整的专用
闭环。它们目前是平台可复用的扩展槽，不能仅因为出现在 `enabled_agents` 中就
声称已经完成养老业务落地。

所以不要简单说“养老只有两个 Agent”，也不要说“养老七个 Agent 都已落地”。
更准确地说：

> 养老领域新增了 Health 和 Care 两个专业能力，已经复用环境控制和设备接入
> 能力；其他平台 Agent 可以由 Profile 启用，但仍需补充对应设备、工具 Scope
> 和领域工作流后，才算形成真实养老能力。

### 5.1 Care Agent 当前真实能力

Care Agent 已经使用受限 `create_agent` ReAct，而不是旧的聊天适配器。当前工具只有：

```text
notify_caregiver
notify_duty_caregiver
create_care_task
```

它能完成：

- 通知指定护理员；
- 通知失败后有限回退到值班护理员；
- 创建可追踪照护任务；
- 输出结构化 Finding；
- 在居民授权 Scope 内执行。

它目前不能声称：

- 已接入真实短信、电话或企业微信；
- 已管理完整排班系统；
- 已跟踪护理员是否实际到场；
- 已形成医疗处置闭环；
- 能做医学诊断或用药决策。

### 5.2 Care Agent 最值得继续增强的方向

不要为了“数量多”随意增加 Agent。先把 Care 的协同闭环补强：

1. `list_duty_caregivers`：查询当前值班人员；
2. `read_care_plan`：读取居民非诊断性照护计划；
3. `update_care_task_status`：接受、处理中、完成、无法完成；
4. `wait_for_acknowledgement`：等待通知确认；
5. `escalate_care_task`：超时后升级给上级或备用人员；
6. `record_care_handoff`：记录换班交接；
7. `schedule_follow_up`：安排复查或回访。

形成的闭环是：

```text
风险 Finding
-> 选择责任人
-> 通知
-> 等待确认
-> 创建/更新任务
-> 超时升级
-> 记录处置结果
-> 返回 Butler 评估目标
```

只有当工具、权限、数据生命周期明显分离时，再考虑拆分：

- Care Coordination Agent；
- Scheduling Agent；
- Communication Agent。

当前阶段保持一个 Care Agent 更合理。

## 6. 面向私企的表达策略

私企面试官通常更关心：

- 你是否真正写过核心代码；
- 你能否解释架构取舍；
- 系统遇到过什么真实问题；
- 有没有测试、可观测性和工程闭环；
- 能否把技术转换为业务价值。

### 6.1 30 秒自我介绍

> 我主要关注 LLM Agent 与物联网系统的结合。项目中我负责 WoTAgent 的整体技术方案和核心实现，把用户自然语言请求拆成可观察的 Run 和 Task，由 Supervisor 动态委派 Health、Care、Environment 等专业 Agent，再通过 TD 生成受限的 MCP 能力。为了避免大模型直接控制设备，我把 RBAC、资源 Scope、TD 参数校验、执行后验证和 Monitor 硬规则放在确定性边界。项目目前完成了智能家居和养老两个场景、仿真设备链路、前端 Trace 以及较完整的自动化测试。

### 6.2 90 秒项目介绍

> 这个项目最初解决的是自然语言控制物联网设备的问题，但如果只做一次 Function Calling，很难处理多设备条件任务、权限边界和执行失败。所以我把交互建模为 Conversation、Turn、Run、Task、AgentRun、Loop 和 ToolCall。Butler Supervisor 负责理解目标并生成结构化 Task DAG；Scheduler 校验依赖、资源冲突并做有限并行；专业 Agent 在自己的工具 Scope 内调查或执行。设备能力通过 WoT TD 描述并编译为 MCP Tool，动作执行后必须重新读取状态验证，不能把 HTTP 200 当成目标完成。
>
> 后来我把养老作为第二个领域，用 Deployment Profile 隔离养老院和别墅的空间、设备、提示词和 Agent 集合；新增独立 Monitor 持续采集健康与环境数据，按确定性规则产生风险事件，再把事件作为 Run 交给 Butler 协调 Health、Care 和 Environment Agent。这个过程中我重点解决了实体名称到规范 ID、居民级 Scope、重复失败终止、跨 Run 记忆、并行 Task 和前端 Trace 等问题。

### 6.3 五分钟分层讲法

#### 第一层：需求与核心矛盾

普通智能家居系统解决“点按钮控制设备”，本项目解决：

- 自然语言目标可能不完整；
- 一个目标可能涉及多个专业能力和设备；
- LLM 输出不稳定；
- 设备有真实能力和权限边界；
- 调用成功不代表状态真的改变；
- 健康与安防事件不能完全交给 LLM 判断。

#### 第二层：总体架构

```text
用户 / Monitor
-> Butler Supervisor
-> Task DAG + Scheduler
-> 专业 Agent
-> Scoped MCP
-> TD Device / Resource
-> Verification
-> Finding
-> Butler 二次决策
```

#### 第三层：三个最能体现能力的技术点

1. **Run/Task DAG**：支持一个请求产生多个 Task、依赖、并行和二次决策；
2. **受约束执行**：LLM 决策与 RBAC、Scope、TD、Policy、Verification 分离；
3. **领域与平台解耦**：AgentSpec 可复用，Profile 选择空间、设备、提示词和 Agent。

#### 第四层：真实问题与改进

选择两到三个讲，不要把所有失败都倒出来：

- 模型曾输出显示名 `Health Agent`，运行时需要 `health_agent`。改为模型只生成业务 Payload，可信身份由运行时注入；
- “张爷爷”曾被模型直接当成 MCP URI。增加领域 EntityResolver，在创建 Task 前解析为 canonical ID，并在 MCP 层二次检查 Scope；
- 同一个不可恢复错误曾被 Supervisor 重复委派。增加错误分类与 failure fingerprint；
- 工具返回成功但目标状态未改变。增加执行后读取和 verified 结果；
- 跨 Agent 条件任务曾错误地同时创建。明确 `depends_on` 不是条件语义，改为调查后由 Supervisor 二次决策。

#### 第五层：验证结果与边界

可以说：

> 当前主要通过本地 simulator 和 SQLite 做实验验证。最近一次完整回归为后端 262 passed、3 skipped，仿真端 64 passed，前端 45 passed，并完成真实模型的小范围链路验证。

同时主动限定：

> 它还不是生产级养老医疗系统；认证、真实通知渠道、分布式部署和长期设备稳定性仍需要继续完善。

这种边界感通常比夸大“已经产品化”更能体现工程判断。

## 7. 面向央国企的表达策略

央国企也关心技术，但通常更重视：

- 目标拆解与方案组织；
- 标准化和可追溯；
- 多方协作意识；
- 安全、责任边界和风险控制；
- 文档、演示和阶段交付能力。

### 7.1 30 秒版本

> 我在项目中主要承担技术方案梳理和核心平台搭建工作。项目围绕 WoT、TD、MCP 和多 Agent 构建统一物联网管控链路，我把设备能力、人员权限、专业 Agent 和业务领域做了分层，并通过 Run/Task 模型把每次决策、执行和验证留痕。养老场景中又引入独立 Monitor 和风险处置流程，既保留大模型的任务理解和协调能力，也通过确定性规则保障健康、权限和设备操作边界。

### 7.2 三分钟版本

按“背景、职责、协作、成果、复盘”讲：

1. **背景**：设备协议和操控接口异构，角色权限不同，自然语言任务可能跨设备和跨专业；
2. **职责**：参与总体方案，重点负责 Agent 编排、Task DAG、TD 到 MCP、上下文与 Trace；
3. **协作**：把设备接入、领域规则、Agent 能力和前端展示定义成明确接口，便于不同成员并行；
4. **成果**：完成别墅和养老 Profile、仿真设备、领域中心、Monitor 风险事件和自动化测试；
5. **复盘**：从一次规划固定执行逐步演进到观察、决策、执行、再观察，同时保留权限和安全底座。

### 7.3 集体协作能力要怎样说才不失真

不要虚构“带领十人团队”。可以真实表达：

- 我负责把老师的概念性需求转成可落地的模块边界；
- 我定义了前后端和设备接入的数据契约；
- 我通过文档、Trace 和测试降低协作中的理解偏差；
- 我在养老场景落地时协调了模拟设备、Monitor、Agent 和前端展示；
- 我对出现的问题进行了复盘并形成经验文档。

这体现的是组织和协调能力，不依赖夸大的职位头衔。

## 8. 面试官常见追问与建议回答

### 问题 1：为什么要多 Agent，一个 Agent 加很多工具不行吗

> 可以做简单版本，但工具一多，提示词、权限范围和失败处理会混在一起。我的拆分依据不是角色名字，而是稳定能力边界：健康信息分析、照护协同、环境控制、安防等。每个 Agent 只看到 AgentSpec 和 MCP Scope 授权的能力，Supervisor 负责跨专业协调。这样既降低单 Agent 工具空间，也便于权限、测试和审计。

### 问题 2：你们是真多 Agent，还是一个模型换提示词

> 底层模型服务可以相同，但每个专业 Agent 有独立 AgentSpec、工具集合、MCP Scope、Task、AgentRun 和 Trace，并由 Supervisor 动态创建依赖和并行关系。多 Agent 的关键不在于是否购买多个模型实例，而在于是否存在独立目标、能力边界、运行状态和协作协议。

### 问题 3：同一个 Agent 会不会同时执行两个任务

> 可以。执行器被复用，但每个 Task 和 AgentRun 独立。Scheduler 根据依赖、并行标记和资源读写声明决定是否并行。同一设备存在写冲突时必须串行；不同资源的只读任务可以并行。

### 问题 4：为什么外层不用统一 LangGraph

> 当前 Butler 使用 `create_agent`，设备专业 Agent 使用 LangGraph 子图，但 Run/Task DAG、调度和二次决策由 Python `RunCoordinator` 驱动。这不是一个完全统一的 LangGraph 主图。现阶段这样便于明确持久化、调度和恢复边界；后续可以评估主图化，但不能只为“框架统一”牺牲已经稳定的任务、权限和审计语义。

### 问题 5：LLM 到底负责什么，程序又负责什么

> LLM 负责理解目标、拆解任务、选择专业 Agent、判断证据是否充分和决定下一步；程序负责实体规范化、权限、资源 Scope、DAG 校验、工具执行、TD 参数校验、状态验证、超时和审计。这个边界让系统既有动态决策能力，又不会让模型绕过设备和安全约束。

### 问题 6：TD 和 MCP 有什么关系

> TD 描述设备实际具备的属性、动作、事件和参数，是设备能力事实源；编译器把 TD 转成工具 Schema，再通过 MCP 暴露给 Agent。MCP 解决能力发现和调用协议，TD 解决设备语义与约束，两者不是替代关系。

### 问题 7：养老场景为什么不让 LLM 直接判断风险

> 心率、血氧、跌倒和环境阈值需要持续监测、低延迟和可审计，不能依赖 LLM 每次轮询。独立 Monitor 接收数据、持久化并执行确定性规则，只把结构化风险事件交给 Agent 协调。LLM 可以调查证据和安排处置，但不能忽略医疗硬告警。

### 问题 8：你个人最有价值的工作是什么

推荐回答：

> 我最有价值的工作不是单独写某个页面或 Prompt，而是把模糊的“多角色物联网 Agent”概念落成了可运行的契约：人类 Principal、软件 Agent、Task、资源 Scope、TD/MCP 和验证 Trace 各自负责什么，并通过测试和真实链路问题不断修正这些边界。

## 9. 能说到什么程度

| 主题 | 可以明确说 | 谨慎说 | 不要说 |
|---|---|---|---|
| 多 Agent | 有 Supervisor、7 类 AgentSpec、Task DAG 和独立 AgentRun | 不同 Agent 可复用同一底层模型 | 每个 Agent 都是独立训练的大模型 |
| LangGraph | 设备专业 Agent 使用 LangGraph，支持 interrupt/checkpoint | 外层可继续评估主图化 | 整个系统已经是统一 LangGraph 主图 |
| Care | 有受限 ReAct、通知回退和创建任务 | 通知目前是模拟/平台记录 | 已连接真实医院或真实短信系统 |
| 权限 | 有 Principal、Role、关系、资源 Scope 和 MCP 二次校验 | 当前是实验级身份系统 | 已达到生产医疗合规 |
| 设备 | TD 编译 MCP，仿真端完成链路验证 | ESP32/真实设备可按同一 TD 接入 | 已大规模部署真实养老设备 |
| 可靠性 | 有验证、失败分类、超时、确认和测试 | 仍需真实设备长期测试 | LLM 永远不会犯错 |
| 评测 | 有自动化测试、Trace、Token/耗时指标和场景评测 | 真实模型测试控制预算执行 | 已经有行业权威 Benchmark 结论 |

## 10. 现有文档怎样读

建议按以下顺序，不要从 9 万字的经验文档硬啃：

1. **本文件**：建立面试主线和说法边界；
2. [文档索引](README.md)：先确认哪些是现状、计划和历史；
3. [当前真实技术地图](TECHNICAL_GUIDE.md)：核对当前真实源码入口；
4. [Run / Task DAG 设计](WOTAGENT_RUN_TASK_DAG_DESIGN.md)：理解 Run、Task、AgentRun、Loop 和并行；
5. [TD / MCP 契约](WOTAGENT_2.0_TD_MCP_CONTRACT.md)：准备 TD 到 MCP 的底层追问；
6. [上下文与评测报告](WOTAGENT_2.0_CONTEXT_AND_EVALUATION_REPORT.md)：准备上下文和评测问题；
7. [智能体化重构经验](WOTAGENT_AGENTIC_REFACTOR_EXPERIENCE.md)：挑选两三个真实问题作为复盘案例；
8. [项目交接文档](../handoff.md)：只作为当前实现状态索引，不适合直接背诵。

旧的 `WOTAGENT_2.0_COMPLETE_TECHNICAL_GUIDE.md` 可以查历史设计，但当前架构事实以 `TECHNICAL_GUIDE.md` 和代码为准。

根目录的 `面试一问一答.md` 和 `蚂蚁国际面试准备_关英颀.md` 形成于旧运行时，
其中仍有“三阶段流水线”或“单 Agent”等过时说法。它们可以保留通用面试题，
但 WoTAgent 架构回答必须以本文件为准。

## 11. 七天准备法

### 第一天：只练主线

- 背熟 30 秒和 90 秒版本；
- 能画出八节点总体链路；
- 能解释为什么不是“大模型直连设备”。

### 第二天：吃透运行模型

- Conversation、Turn、Run、Task、AgentRun、Loop、ToolCall；
- 同一个 Agent 多次调用与并行；
- Supervisor 与 Scheduler 的职责边界。

### 第三天：吃透设备链路

- TD；
- Tool Schema；
- MCP；
- Scope；
- 状态验证。

### 第四天：吃透权限与领域抽象

- Principal 和 Agent 的区别；
- Role、Relationship、Resource Scope；
- Domain 与 Deployment Profile；
- 养老和别墅如何复用 Agent。

### 第五天：准备三个问题案例

只选三个：

- agent_id 可信身份；
- 居民实体解析与 Scope；
- 条件委派或执行后验证。

每个案例按：

```text
现象 -> 根因 -> 修改 -> 测试 -> 设计结论
```

### 第六天：分别模拟私企和央国企

- 私企版突出代码、架构、测试和取舍；
- 央国企版突出目标拆解、标准化、协作和风险意识；
- 两版事实完全一致，只调整重点。

### 第七天：做压力追问

重点练：

- 为什么不用单 Agent；
- 为什么不全交给 LLM；
- 为什么不全部使用 LangGraph；
- 真实设备在哪里；
- Care Agent 到底做了什么；
- 项目最大的不足是什么。

## 12. 最后的表达原则

1. 先讲问题，再讲框架，不要从 LangChain 名词开始；
2. 一次只主动讲三个技术亮点；
3. 每个亮点准备一个真实问题和测试证据；
4. 明确实验系统与生产系统的边界；
5. 不用“完全自治”“企业级”“医疗诊断”等过度表述；
6. 把不足描述为已识别的工程边界，并给出下一步，不要自我否定；
7. 多 Agent 的价值不是 Agent 数量，而是目标、能力、权限、状态和协作协议的分离。

最适合作为结尾的一句话：

> 这个项目让我真正理解，Agent 工程的难点不是让模型多调用几个工具，而是如何把模型的动态决策放进一个可授权、可验证、可恢复、可观察的真实系统中。
