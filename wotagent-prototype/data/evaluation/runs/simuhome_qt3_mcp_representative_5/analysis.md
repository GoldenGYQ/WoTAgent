# WoTAgent 2.0 SimuHome qt3 评测

生成时间：2026-07-13T12:18:12.415768+00:00

运行目录：`D:\Users\gyq16\Desktop\PRJ\WoTAgent\wotagent-prototype\data\evaluation\runs\simuhome_qt3_mcp_representative_5`

## 指标

| 指标 | 数值 |
|---|---:|
| Episode 数 | 5 |
| Episode 成功率 | 100% |
| Goal-State 成功率 | 100% |
| Action Trace 准确率 | 100% |
| 平均总 Token | 0.00 |
| 平均响应 TTFT | 0.00 ms |

## Episode

| Episode | 类型 | 可行性 | 成功 | Goal-State | Trace | Token | TTFT |
|---|---|---:|---:|---:|---:|---:|---:|
| episode_0003_1 | qt3 | true | true | 100% | 100% | 0 | 0 ms |
| episode_0031_2 | qt3 | true | true | 100% | 100% | 0 | 0 ms |
| episode_0040_0 | qt3 | true | true | 100% | 100% | 0 | 0 ms |
| episode_0047_0 | qt3 | true | true | 100% | 100% | 0 | 0 ms |
| episode_0095_0 | qt3 | true | true | 100% | 100% | 0 | 0 ms |

## 范围

本次 Runtime 适配只执行 `qt3` 的显式设备动作，并通过 TD 动态生成 MCP Tool，
再由 SimuHome Matter/REST Binding 执行和评分。`qt1`、`qt2`、`qt4` 需要环境
因果、虚拟时间或事件工作流，不包含在本次结果中。
