# WoTAgent 部署场景说明

状态：当前场景与授权契约

最后核对：2026-07-23

## 目的

WoTAgent 将四个边界分开：

```text
Deployment Profile
  -> 当前站点、空间、设备和领域提示词

AgentSpec 与 MCP Scope
  -> 专业 Agent 可以使用哪类能力

RBAC 与关系策略
  -> 当前 Human Principal 可以对目标资源做什么

Domain Operations
  -> 如何把主体、资源、观测、确定性规则、事件和动作投影到领域运行中心
```

Profile 不是角色。养老与别墅切换改变的是当前物联网世界；`viewer` 与
`operator` 切换改变的是同一世界中的权限。

## 共享场景目录

唯一配置来源：

```text
config/deployment_profiles.json
```

后端和 `wot-device-simulator` 共同读取该文件。

| Profile | Domain | Site | 空间数 | 设备 |
|---|---|---|---:|---:|
| `eldercare` | `eldercare` | `nursing-home-demo` | 2 | 配置 16，TD 覆盖 15 |
| `villa` | `smart_home` | `villa-demo` | 12 | 23 |

设备归属互斥，目录校验器会拒绝一个设备同时属于多个 Profile。

当前已知缺口：养老 Profile 包含 `purifier-102`，但 TD 目录中没有对应文件，因此
在补充 TD 前不能把它展示为可执行能力。别墅 23 个设备均有 TD。

每个可选 Profile 必须注册同 `domain_id` 的 DomainOperations 适配器：

| Profile | 领域运行中心 | 主要软件主体 |
|---|---|---|
| `eldercare` | 养老照护管控 | Butler、Health、Care、养老 Monitor |
| `villa` | 别墅智能管控 | Butler/秘书、Security/保安、Environment、Energy、Housekeeping、DeviceOps、别墅规则 Monitor |

软件主体不是登录用户。`JH-1`、`JH-2` 是授权 Principal；Butler 和专业 Agent
是拥有独立 AgentSpec/MCP 能力边界的软件 Principal。

同一个 Human Principal 在不同 Domain 中可以有不同绑定：

| Principal | 养老绑定 | 智能家居绑定 |
|---|---|---|
| `JH-1` | 护理员，`caregiver_of elderly-001` | 别墅业主，`owner_of site:villa-demo` |
| `JH-2` | 家属观察者，`family_of elderly-002` | 安保人员，分配到 `site:villa-demo` |

Human Role 不能作为专业 Agent 身份。专业 Agent 名称描述的是健康分析、护理协同、
安防响应、环境控制等能力类别。

## 运行流程

```text
前端选择 Profile
-> PUT /api/v2/deployment/active
-> 后端校验 Profile
-> Simulator 激活同一 Profile
-> 前端新建 Conversation
-> POST /api/v2/runs 携带 profile_id
-> Run 持久化 profile_id/domain_id/site_id
-> Supervisor 接收压缩后的领域上下文
-> Gateway 按 Profile 过滤 TD 能力
-> MCP 拒绝跨 Profile 设备调用
```

一个 Conversation 不能混用多个 Profile。切换场景后继续复用旧 Conversation 时，
后端返回 `409`。

## 相关 API

```text
GET /api/v2/deployment/profiles
PUT /api/v2/deployment/active
GET /api/v2/principals?domain_id=<domain_id>&login_only=true
GET /api/v2/principals/{principal_id}/context?domain_id=<domain_id>
GET /api/v2/principals/role-templates?domain_id=<domain_id>
GET /api/v2/principals/registration-targets?domain_id=<domain_id>
POST /api/v2/principals/register
POST /api/v2/principals/login
GET /api/v2/principals/session?domain_id=<domain_id>

GET /api/scenes
GET /api/scenes/active
PUT /api/scenes/active
```

第一组属于 WoTAgent，第二组属于 Simulator。

## 新增 Profile

1. 在 `config/deployment_profiles.json` 中新增 Profile。
2. 只引用已经存在的 TD 设备 ID。
3. 保证设备归属互斥。
4. 定义空间、启用 Agent 和领域提示词。
5. 注册对应的 DomainOperations 适配器。能在前端选择但没有运行投影的 Profile
   属于不完整实现。
6. 运行后端、前端和 Simulator 回归。

## 当前 P1 授权边界

养老场景已经执行：

```text
角色能力
AND Principal 与 Resident 的关系
AND 有效 Resident ResourceScope
AND Health MCP 范围二次校验
```

`JH-1` 绑定 `elderly-001`，`JH-2` 绑定 `elderly-002`。演示账号支持一键
Session；用户注册账号保存 PIN 和 Bearer Session。后端解析 Principal 上下文并
签发有效范围。它仍是实验级身份系统，不是生产级认证。

别墅第一阶段采用站点级关系：

```text
JH-1 -> site:villa-demo
  -> domain.read, device.read, device.control,
     scenario.execute, event.acknowledge

JH-2 -> site:villa-demo
  -> domain.read, device.read
```

这比最终的房间、岗位、班次关系模型更粗，但已经证明同一 Domain Operations API
可以在不让 LLM 授权的前提下约束不同 Principal。

最终执行范围计算公式：

```text
Principal 能力与关系范围
INTERSECT Profile 设备集合
INTERSECT AgentSpec 与 MCP Scope
INTERSECT TD 与 Policy 约束
```

`effective_scope` 只能由后端签发。Supervisor 可以申请能力，但不能创建或扩大
权限。注册流程同样只能选择后端维护的角色模板和资源目标，前端不能直接提交任意
Capability。
