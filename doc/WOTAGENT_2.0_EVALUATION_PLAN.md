# WoTAgent 2.0 测试与评价计划

## 1. 评价原则

每完成一个功能单元，不只报告测试数量，还要回答：功能是否正确、安全边界是否成立、失败是否可解释、轨迹是否可观测、成本是否可接受。

## 2. 四级验证节奏

1. 单点测试：一个模型、策略函数、节点或适配器。
2. 功能单元测试：一条完整分支，如 control 或 viewer deny。
3. 子系统测试：LangGraph + Gateway + Simulator。
4. 大测试：前端 + API + Graph + MCP + Simulator/ESP32。

LangGraph 节点测试采用每个测试新建 graph 和 `InMemorySaver`；持久化、确认与恢复测试使用固定 `thread_id` 检查 checkpoint 和 resume。

## 3. 确定性功能指标

| 指标 | 计算方式 | 目标 |
|---|---|---|
| Schema 通过率 | 合法计划通过数 / 合法计划总数 | 100% |
| 非法计划阻断率 | 非法计划被拒绝数 / 非法计划总数 | 100% |
| 越权零调用率 | RBAC 拒绝且设备调用为 0 的用例比例 | 100% |
| 状态验证覆盖率 | 含 postcondition 的控制步骤产生验证结果比例 | 100% |
| 虚假成功阻断率 | 接口成功但状态未变时判失败比例 | 100% |
| 依赖顺序正确率 | DAG 执行顺序满足依赖的用例比例 | 100% |
| 重试上界合规率 | 实际调用次数不超过预算的比例 | 100% |
| Trace 完整率 | 请求、策略、执行、验证和终态齐全比例 | 100% |

## 4. LLM Planner 指标

| 指标 | 含义 |
|---|---|
| Intent Accuracy | chat/query/control/clarify 分类准确率 |
| Device Resolution Accuracy | 设备 ID 解析准确率 |
| Action Exact Match | TD action 完全匹配率 |
| Parameter Exact Match | 参数名和值完全正确率 |
| Executable Plan Rate | 首次通过 Schema、TD 和 DAG 校验的计划比例 |
| Clarification Precision | 应澄清样本中正确进入 clarify 的比例 |
| Hallucinated Device Rate | 引用不存在设备的比例，越低越好 |
| Unsafe Plan Rate | 包含越权或未声明动作的计划比例，越低越好 |

LLM 指标必须固定数据集、模型、temperature、Prompt 版本并至少重复多次；不能只展示一次成功演示。

## 5. Agent Graph 轨迹指标

- Route Accuracy：请求是否经过正确节点路径。
- Node Success Rate：各节点成功完成比例。
- Trajectory Exact Match：关键节点序列与期望序列一致比例。
- Recovery Success Rate：首次失败后在预算内恢复的比例。
- Loop Bound Compliance：任何任务都不超过最大循环次数。
- Human Confirmation Integrity：未确认时零执行，恢复后计划哈希不变。
- Checkpoint Resume Accuracy：恢复后不重复已完成副作用。

## 6. 系统指标

- 端到端任务成功率与部分成功率。
- P50、P95、P99 总延迟及 Planner、Gateway、Verifier 分段延迟。
- 每任务模型调用次数、输入/输出 Token 和估算成本。
- MCP/HTTP/MQTT 调用成功率、超时率和熔断次数。
- WebSocket 终态到达率和事件顺序正确率。
- 并发吞吐、队列长度、慢消费者丢弃事件数。

## 7. 安全评价集

- viewer/operator/admin 权限矩阵。
- Prompt injection 诱导调用未授权工具。
- RAG 文档包含恶意指令。
- 不存在设备、动作和越界参数。
- 重放请求、确认 token 过期与计划替换。
- WebSocket 跨会话订阅。

## 8. 功能单元评价模板

```text
功能单元：
范围：
测试结果：通过数 / 总数
正确性：
安全性：
可靠性：
可观测性：
性能：
已知缺口：
结论：通过 / 有条件通过 / 不通过
```

## 9. 当前阶段评价

功能单元：确定性 Orchestrator

- 正确性：已覆盖依赖执行、真实状态验证、DAG 环检测。
- 安全性：已覆盖 viewer 拒绝且设备零调用。
- 可靠性：已覆盖瞬时失败的有界重试与虚假成功阻断。
- 可观测性：TaskTrace 已记录 policy、execution、verification 和终态。
- 已知缺口：尚未加入事件发射、确认恢复、并行 DAG 和持久化 trace。
- 结论：基础内核通过，允许进入 LangGraph 集成阶段。

功能单元：LangGraph 基础状态图

- 测试结果：4/4 通过；与 Orchestrator 联合测试累计 9/9 通过。
- 正确性：control、chat、policy deny 三条路由与单节点调用均符合预期。
- 安全性：viewer deny 在 graph 路由阶段终止，Executor 未运行。
- 可观测性：graph state 保留 plan、policy decisions、trace、response 和 phase。
- 已知缺口：确认 interrupt、checkpoint resume、Recovery/Replanner 节点尚未实现。
- 结论：基础图有条件通过，允许接入真实 Planner 适配层。

功能单元：LangChain Function Calling Planner 适配器

- 测试结果：4/4 通过；连同图、编排器和原 Agent 工厂共 15/15 通过。
- 正确性：合法调用可转换为强类型 TaskPlan；非法依赖和缺失工具调用会在执行前失败。
- 安全性：Prompt 仅提供压缩后的注册设备、action 和 property，不注入完整不可信 TD。
- 可测试性：模型 Runnable 可替换，默认测试不访问外网。
- 已知缺口：尚未使用固定自然语言数据集评价真实 DeepSeek 的设备解析率和计划可执行率。
- 结论：适配器通过；真实模型质量必须在 API 切换前后单独评价。

阶段回归：当前后端测试集 83/83 通过，新增模块未破坏 1.0 基线。

功能单元：`POST /api/v2/tasks`

- 测试结果：API 单点测试 1/1 通过；第二次全量后端回归 88/88 通过。
- 正确性：HTTP 返回的 plan、status 和 response 均来自 LangGraph state，不使用前端占位回复。
- 隔离性：v2 与旧 `/api/chat`、WebSocket 路径并存，尚未直接替换稳定入口。
- 已知缺口：v2 WebSocket 事件流、确认恢复和持久化 TaskTrace 尚未接入。
- 技术债：FastAPI `on_event` 已弃用；EventBus 日志适配器存在未 await coroutine 的运行时告警，后续单独修复并加告警零容忍测试。
- 结论：API 旁路入口通过，可以继续建设 v2 事件流。
