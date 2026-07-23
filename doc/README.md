# WoTAgent 文档索引

最后核对：2026-07-23

代码分支：`trae/run-task-dag`

当前发布基线：`v2.8.0`

当前实现目标：`v3.0.0` 场景隔离与 P1 授权

## 1. 如何使用这些文档

仓库经历过多轮重构，文档中的“设计目标”和“当前实现”不能混为一谈。事实优先级
如下：

```text
当前代码与自动化测试
> handoff.md 当前快照
> 当前技术手册
> 设计与计划文档
> 历史技术文档和经验记录
```

最新已验证回归基线：

| 子系统 | 结果 |
|---|---|
| Agent 后端 | `262 passed, 3 skipped` |
| Device Simulator | `64 passed` |
| Vue 前端 | `45 passed`，typecheck/build 通过 |

这些数字只说明对应自动化范围通过，不等同于生产安全或所有真实模型场景均通过。

## 2. 当前事实文档

| 文档 | 用途 | 状态 |
|---|---|---|
| [`../handoff.md`](../handoff.md) | 当前架构、进度、真实故障、待办与安全边界 | 当前交接基线 |
| [`TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md) | 当前运行链、源码入口和职责边界 | 当前技术地图 |
| [`API_GUIDE.md`](API_GUIDE.md) | 当前 HTTP、WebSocket、身份、领域和语音接口 | 当前 API 手册 |
| [`DEPLOYMENT_PROFILES.md`](DEPLOYMENT_PROFILES.md) | 场景、Domain、Principal、Agent 与设备范围 | 当前部署模型 |
| [`WOTAGENT_2.0_TD_MCP_CONTRACT.md`](WOTAGENT_2.0_TD_MCP_CONTRACT.md) | TD 到 MCP、Policy、执行和验证契约 | 当前设备链契约 |
| [`WOTAGENT_INTERVIEW_AND_AGENT_ABSTRACTION_GUIDE.md`](WOTAGENT_INTERVIEW_AND_AGENT_ABSTRACTION_GUIDE.md) | 多主体抽象与私企/央国企面试表达 | 当前面试手册 |

## 3. 设计与计划文档

| 文档 | 已落地部分 | 仍属计划的部分 |
|---|---|---|
| [`WOTAGENT_RUN_TASK_DAG_DESIGN.md`](WOTAGENT_RUN_TASK_DAG_DESIGN.md) | Run/Task/AgentRun、DAG、有限并行、资源冲突、持久化与确认恢复 | 取消、独立重试、通用条件 Task、多实例调度 |
| [`WOTAGENT_GOAL_DRIVEN_AGENT_LOOP.md`](WOTAGENT_GOAL_DRIVEN_AGENT_LOOP.md) | `create_agent` Supervisor、Health/Care ReAct、设备 LangGraph、Supervisor 二次决策 | 统一主图、完整 rule/shadow 顶层策略、更多专业 Agent 原生 ReAct |
| [`WOTAGENT_2.0_EVALUATION_PLAN.md`](WOTAGENT_2.0_EVALUATION_PLAN.md) | 单元/集成回归、Simulator、真实模型小预算冒烟、风险闭环 | 固定发布门禁、长期统计、多实例性能与全量 SimuHome |

“计划文档”不表示其中所有能力都已实现。每份文档开头和“当前状态”章节会明确区分
已完成、部分完成和未完成。

## 4. 历史与经验文档

| 文档 | 说明 |
|---|---|
| [`WOTAGENT_2.0_COMPLETE_TECHNICAL_GUIDE.md`](WOTAGENT_2.0_COMPLETE_TECHNICAL_GUIDE.md) | 早期单 Task/Planner-Orchestrator 架构快照，仅用于理解演进 |
| [`WOTAGENT_AGENTIC_REFACTOR_EXPERIENCE.md`](WOTAGENT_AGENTIC_REFACTOR_EXPERIENCE.md) | 按日期追加的故障、修复和测试记录；旧章节中的数字保留为历史证据 |

历史文档中的 `/api/v2/tasks`、单 `task_id` 顶层运行、旧
`EldercareChatAgent`、单一 `TraceStore` 等描述都不是当前生产链。

## 5. 当前实现一句话

```text
Conversation -> Turn -> Run -> Task DAG -> AgentRun -> Loop -> ToolCall
```

Butler 使用 LangChain `create_agent` 生成结构化 `SupervisorDecision`；外层
`RunCoordinator` 与 `TaskScheduler` 负责持久化 DAG、有限并行、失败语义和二次
决策；Health/Care 使用受限 ReAct，设备专业 Agent 使用 LangGraph 子图；所有真实
设备调用继续经过 Principal 授权、AgentSpec/MCP Scope、TD、Policy 和状态验证。

## 6. 当前已知缺口

1. 外层 Run 编排仍是 Python `RunCoordinator`，不是统一 LangGraph 主图。
2. 顶层 `rule/hybrid/agentic` 与 Shadow Mode 尚未形成等价的完整 Run 实现。
3. 养老 Profile 配置了 16 个设备 ID，但当前 TD 目录只覆盖 15 个；
   `purifier-102` 缺少 TD。别墅 Profile 的 23 个设备均有 TD。
4. 部分专业 Agent 在养老场景中仍只是扩展槽位，尚无完整专用工作流。
5. 身份层是实验级持久化 Principal/PIN/Session，不是生产级统一认证。
6. 多实例事件通知、运行中进程崩溃恢复、取消和独立 Task 重试尚未完成。
7. 自动化测试以 Fake/Scripted LLM 为主；真实模型验证必须继续使用小预算冒烟
   测试，不能由回归数量替代。
