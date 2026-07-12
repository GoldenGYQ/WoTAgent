# WoTAgent 2.0 上下文工程审计与阶段评测报告

## 1. 审计结论

本轮在批量评测前审计了 Planner 与 Response Agent 的实际消息栈，发现并修复两类无效或有害上下文：

1. Response Composer 的 2.0 事实 Prompt 外层仍经过旧 Responder Agent middleware，重复注入身份、RBAC 和通用回复规则。
2. Planner 对任何请求都注入全部 18 台设备，简单单设备任务也承受全量 TD 上下文和同类设备干扰。

修复后：

- Response 2.0 直接调用独立、无工具、关闭 thinking 的模型，不再经过旧 Agent middleware。
- Planner 根据房间和设备类型确定性召回候选；无法识别任务范围时回退全部设备。
- 候选上下文保留 action input Schema 和房间别名。实验表明这部分属于必要 Token，不能仅为压缩而删除。
- 原始 Chain-of-Thought 不保存；只记录结构化规划摘要。

## 2. 延迟与 Token 指标定义

| 指标 | 定义 |
|---|---|
| Input Tokens/Turn | 一轮内 Planner、Responder 等模型调用的输入 Token 总和 |
| Output Tokens/Turn | 一轮内模型输出 Token 总和 |
| Tokens/Turn | 一轮模型总 Token |
| Time to Plan | Planner 请求开始到完整 Function Call 可用的时间 |
| Response TTFT | Response 流式请求开始到首个非空文本 Token 的时间 |
| Response Latency | Response 请求开始到完整回复结束的时间 |
| End-to-End Latency | 用户请求进入 Graph 到最终回复生成完成的时间 |
| Tool Calls/Turn | 一轮真实设备工具调用次数 |
| Model Calls/Turn | 一轮模型调用次数 |

Planner 使用强制 Function Calling 和完整结构化返回，本轮不把 completion latency 冒充 TTFT，统一记录 Time to Plan。Response 使用真实流式输出，记录 TTFT。

## 3. 上下文消融

数据：HomeBench 严格适配子集中的同一批 20 条任务。

| 上下文 | Exact Match | Tokens/Case | Mean Time to Plan |
|---|---:|---:|---:|
| 全量 18 设备 | 80% | 3821.2 | 1772ms |
| 相关设备召回 | 100% | 1311.6 | 1878ms |

相关设备召回减少约 65.7% Token，同时降低同类设备干扰。Time to Plan 没有同步下降，说明当前延迟主要受模型服务固定开销与生成阶段影响，而非只由输入长度决定。

早期极简上下文曾达到 1153.1 Token/Case，但 Exact Match 只有 30%。失败原因是删除了 master/guest bedroom 别名和 action 参数 Schema。补回必要契约后，20 条 Exact Match 恢复到 100%，Token 增加约 13.7%。

## 4. HomeBench Planner 评测

来源：HomeBench `test_data.jsonl`。适配规则仅保留当前 WoT TD 中真实存在的房间、设备、动作和合法参数；过滤依赖未提供初始状态的相对调节任务。

- 原始测试集：17,366 条。
- 当前严格可对齐：615 条。
- 本轮评测：前 100 条严格可对齐任务。

| 指标 | 结果 |
|---|---:|
| Intent Accuracy | 100% |
| Exact Match | 99% |
| Precision | 99% |
| Recall | 99% |
| F1 | 99% |
| Executable Plan Rate | 100% |
| Total Input Tokens | 113,528 |
| Total Output Tokens | 16,362 |
| Total Tokens | 129,890 |
| Tokens/Case | 1298.9 |
| Mean Time to Plan | 1933ms |

唯一失败样本为“Turn off the lights in the kitchen”。厨房灯 TD 没有 `turnOff`，标准答案采用 `setBrightness(0)`；模型生成 `turnOn` 并附加 `on=false` 后置条件，属于语义冲突。该结果说明后续应增加 action/postcondition 一致性校验。

## 5. 真实完整链路评测

环境：真实 DeepSeek-compatible API、LangGraph、Policy、MCP、设备模拟器、Verifier、流式 Response Agent。

任务覆盖：

- 双步骤灯光控制。
- 关闭灯光。
- 空调开机与温度设置。
- 风扇开机与速度设置。
- 设备状态查询。

| 指标 | 结果 |
|---|---:|
| Cases | 5 |
| Goal-State Success Rate | 100% |
| Tokens/Turn | 2212 |
| Input Tokens/Turn | 2023 |
| Output Tokens/Turn | 189 |
| Mean End-to-End Latency | 4120ms |
| Mean Time to Plan | 2432ms |
| Mean Response Latency | 1042ms |
| Mean Response TTFT | 795ms |
| Mean Tool Calls | 1.6 |
| Mean Model Calls | 2.0 |

同一“打开客厅灯并设置 60%”任务，优化前记录为 3302 Token，本轮为 2491 Token，下降约 24.6%，且计划、设备状态和回复保持正确。

## 6. SimuHome 对齐判断

SimuHome snapshot 已下载并检查。其 qt1/qt2/qt3 任务依赖 Matter 房间状态、洗衣机、烘干机、窗帘、环境照度和时序调度等当前 WoTAgent 设备空间尚未提供的能力。直接替换设备名称会改变任务语义，因此本阶段不运行伪对齐分数，也不消耗模型 Token。

当前只复用 SimuHome 的方法：

- 最终 goal-state 评分。
- required action/里程碑记录。
- feasible/infeasible 分类。
- 环境状态与动作结果分离。

待项目增加对应 TD 和环境变量后，再运行正式 SimuHome-adapted 子集。

## 7. 产物

- `data/evaluation/homebench_v2_100_strict.json`
- `data/evaluation/homebench_v2_20_all_context.json`
- `data/evaluation/homebench_v2_20_context_v2.json`
- `data/evaluation/end_to_end_v2_5.json`

这些文件包含逐条计划、评分、Token 和延迟，便于复查而不只保留汇总数字。

## 8. 下一步

1. 增加 action 与 postcondition 一致性校验，阻断 `turnOn + on=false`。
2. 加入 HomeBench 无效指令子集，评价拒绝和澄清能力。
3. 对 Planner 评测执行 `pass^3` 小样本，评价重复运行稳定性。
4. 扩充环境 TD 后再接 SimuHome qt2/qt3。
