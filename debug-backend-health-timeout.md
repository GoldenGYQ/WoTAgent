# [已处理] 后端健康检查超时调试记录

## 问题

运行 `scripts/wotagent-stack.ps1` 时 Simulator 正常启动，但后端 30 秒内未通过
健康检查：

```text
backend did not become healthy within 30 seconds
```

期望后端在超时前提供 `/api/health`。

## 复现

1. 运行 `scripts/wotagent-stack.ps1`。
2. Simulator Ready。
3. 后端健康检查超时并退出。

## 初始假设

- H1：后端启动时崩溃。
- H2：脚本检查了错误地址或端口。
- H3：环境缺少依赖。
- H4：后端只是启动超过 30 秒。

## 证据

- 脚本检查地址正确：`http://127.0.0.1:8000/api/health`。
- `backend.stderr.log` 表明应用在 Import 阶段崩溃。
- 具体错误：

```text
DeploymentProfileRegistry.load()
-> DeploymentProfileCatalog.model_validate(...)
-> ValidationError: device 'light-002' belongs to both 'eldercare' and 'villa'
```

## 根因

`config/deployment_profiles.json` 让 `eldercare` 和 `villa` 共享
`light-002`/`light-003`。Profile 校验器禁止跨场景设备重叠，因此 API 在提供
健康接口前退出。这不是超时阈值或依赖问题。

## 修复

- 为养老增加独立灯 TD：
  - `light-101`：A-101
  - `light-102`：A-102
- 养老 Profile 改用 `light-101`/`light-102`。
- 加强位置别名，使 `A-101` 与 `bedroom1` 可以映射为同一房间组。
- 前端新会话提示按当前 Profile 展示，不再混用养老和智能家居示例。

## 验证

- 前端 `TaskWorkspace` 测试通过。
- Simulator `test_scenes.py`、`test_td.py` 通过。
- 后端 `test_deployment_profiles.py` 通过。
- `scripts/wotagent-stack.ps1 start-safe` 启动通过：

```text
backend is ready: http://127.0.0.1:8000/api/health
frontend is ready: http://127.0.0.1:5174/
```
