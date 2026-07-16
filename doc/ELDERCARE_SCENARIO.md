# WoTAgent 2.0 养老院实验场景

## 1. 实现目标

养老院场景同时支持两条链路：

1. **事件驱动**：传感器数据进入网关后，由确定性的 `HealthMonitor` 定时或即时判定风险，再把风险事件交给 `Butler Agent` 分派处置。
2. **按需查询**：护理员可通过 API 或 MCP Resource 获取老人最新生命体征、当日睡眠和健康摘要，不需要让大模型逐项调用 Tool 轮询数据库。
3. **自然语言处置**：主聊天 API 先把养老请求路由到专用 Planner，再由 Butler 分派 Care/Environment 子图；普通家居请求仍走原 TD Planner。

```text
手环 / 床垫 / 温湿度传感器 / Web 模拟器
                  |
          HTTP 数据入口（当前实现）
                  |
        EldercareStore (SQLite/WAL)
                  |
  HealthMonitor（即时检测 + 5 秒定时扫描）
                  |
              RiskEvent
                  |
       Butler LangGraph 父图
          /               \
 Environment 子图       Care 子图
 adjust_temperature     notify_caregiver
                        create_care_task
```

当前实验版以 HTTP 作为 ESP32 和 Web 控制台的数据入口。数据库表结构和 `SensorReading` 契约是 IoT Data Gateway 的稳定边界；后续 MQTT 订阅器或 BLE 网关只需把消息转换成同一契约，无须修改监测器和 Agent 图。

## 2. 数据模型与风险规则

支持的传感器类型：`heart_rate`、`spo2`、`fall_detected`、`bed_occupancy`、`sleep_quality`、`room_temperature`、`room_humidity`。

| 风险 | 判定规则 | 等级 | 默认处置 |
|---|---|---|---|
| 持续高心率 | 5 分钟窗口内至少 3 条数据，首尾跨度至少 4 分钟，且均大于 100 bpm | high | 通知护理员、创建照护任务 |
| 低血氧 | SpO2 小于 92%；小于 88% 升为 critical | high / critical | 通知护理员、创建照护任务 |
| 疑似跌倒 | `fall_detected=true` | critical | 通知护理员、创建照护任务 |
| 房间过热 | 室温大于 28 C | medium | 通知护理员、调整房间温度至 24 C |

同一老人、同一风险类型在 10 分钟内去重，避免定时扫描重复创建 Agent 任务。风险识别不调用 LLM，因此不会产生 Token 成本。

## 3. 多智能体与权限边界

`Butler Agent` 是 Supervisor 父图，负责把风险事件转成 `DelegatedTask`。父图通过 LangGraph `Send` 动态扇出到子图，再汇总结果。

- `environment_agent` 仅允许使用 `eldercare-environment` MCP 范围和 `adjust_temperature` 动作。
- `care_agent` 仅允许使用 `eldercare-care` MCP 范围和 `notify_caregiver`、`create_care_task` 动作。
- 每次委派还携带 `resident_scope`。授权节点同时验证 Agent 身份、MCP 范围、老人范围和动作白名单。

图拓扑是预先定义的，但本轮实际派生哪些子图由风险事件动态决定。例如跌倒只进入 Care 子图；房间过热同时进入 Care 与 Environment 子图。

聊天命令还会执行用户角色检查：`viewer` 只能读取健康 Resource，`operator/admin` 才能通知护理员、创建任务或调节环境。事件监测器使用 `system` 身份自动处置。开启前端“深度思考”后，养老 Planner 同样先运行私有分析模型，再生成受 Schema 约束的命令计划。

## 4. MCP 设计

健康上下文使用 MCP Resource：

```text
elderly://elderly-001/vitals/latest
elderly://elderly-001/sleep/today
elderly://elderly-001/health/summary
```

有副作用的操作使用 MCP Tool：

```text
adjust_temperature(resident_id, target_temperature, reason)
notify_caregiver(resident_id, message, urgency)
create_care_task(resident_id, title, priority)
```

启动独立 MCP 服务：

```powershell
cd wotagent-prototype
.\.venv\Scripts\python.exe -m wotagent.eldercare.mcp_server --transport http --host 127.0.0.1 --port 18082
```

MCP 地址为 `http://127.0.0.1:18082/mcp`。MCP 服务与 Agent API 共享 `data/eldercare.db`，因此 Resource 读取的是 API/监测器已经写入的数据。

主 API 运行时还会把居民环境设备映射到通用 WoT/MCP 网关。演示数据默认映射为张爷爷 `ac-002`、李奶奶 `ac-003`。调温动作会调用 TD 自动生成的 `setTemperature` Tool，并回读 `targetTemp` 验证；没有配置设备映射的居民才使用明确标注的仿真模式。

## 5. API 与 ESP32 接入

| 方法 | 地址 | 作用 |
|---|---|---|
| GET | `/api/v2/eldercare/residents` | 居民列表 |
| GET | `/api/v2/eldercare/residents/{id}/summary` | 健康摘要 |
| POST | `/api/v2/eldercare/readings` | 传感器或 Web 手动录入 |
| POST | `/api/v2/eldercare/simulate` | 运行内置场景 |
| GET | `/api/v2/eldercare/events` | 风险事件 |
| GET | `/api/v2/eldercare/actions` | 子智能体处置记录 |

ESP32 可直接向局域网中的 Agent API 提交：

```json
{
  "resident_id": "elderly-001",
  "sensor_type": "heart_rate",
  "value": 82,
  "unit": "bpm",
  "source": "esp32-wristband-01"
}
```

风险触发后，响应中的 `tasks[].task_id` 可直接用于现有任务、SSE 和 WebSocket Trace 接口。Trace 会记录风险触发、Butler 委派、授权结果、子图完成、Tool 输入输出和最终状态。确定性监测链路的模型调用与 Token 使用均为 0，这是预期行为。

## 6. 演示步骤

1. 启动 Agent API 和 Vue 控制台。
2. 使用一键实验账号登录：`JH-1` 为王护理员/operator，默认关注张爷爷；`JH-2` 为赵观察员/viewer，默认关注李奶奶。两个账号使用独立的 `user_id` 和会话记录。
3. 打开控制台的“养老监测”视图，在四个业务模块间切换：
   - 设备管理：传感器数据新鲜度、接入协议、空调 TD/MCP 状态。
   - 数据采集：手动传感器读数和五种模拟场景。
   - 健康检测：实时指标、阈值规则和定时监测器状态。
   - 异常告警：风险事件、确认处理、处置动作和 Agent Trace。
4. 点击“模拟跌倒”“持续高心率”或“房间过热”。
5. 查看风险事件、Agent 处置动作和页面底部的事件任务 Trace。
6. 通过手动传感器输入验证正常读数不会错误触发 Agent。
7. 切换到“Agent 会话”，直接输入“查看张爷爷今天的健康状态”或“把张爷爷房间温度调到 24 度”。

内置场景：`normal`、`high_heart_rate`、`low_spo2`、`fall`、`hot_room`。

## 7. 当前边界

- 已实现 HTTP 数据入口、SQLite 时序式存储、定时扫描、Web 模拟输入、MCP Resource/Tool 和多子图处置。
- 通知护理员、创建任务和温控动作当前是可追踪的仿真执行，不会真的发送短信或控制空调。
- MQTT Broker 订阅和 BLE 边缘采集尚未内置；它们应作为网关适配器接入 `SensorReading`，而不是改写 Agent。
- 当前规则适合实验验证，不构成医疗诊断或真实养老机构告警标准。
- `JH-1/JH-2` 是前端本地实验登录，不包含密码、JWT 或服务端身份认证；角色会传入 Agent RBAC，但不能作为生产安全边界。
