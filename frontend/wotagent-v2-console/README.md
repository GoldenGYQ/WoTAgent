# WoTAgent 2.0 Console

WoTAgent 2.0 的独立 Vue 3 运行控制台。该项目只接入 `/api/v2` 和 `/ws/v2`，不依赖旧版前端代码。

## 启动

先启动监听 `8000` 端口的 WoTAgent 后端，然后运行：

```bash
npm install
npm run dev -- --port 5175
```

访问 `http://localhost:5175`。

## 验证

```bash
npm run typecheck
npm test
npm run build
```

控制台支持任务创建、角色选择、确认审批、WebSocket 实时 Trace、REST 轮询降级、计划与原始事件查看，以及 Token、TTFT、工具调用、循环次数和估算费用统计。最近任务 ID 会保存在浏览器中，页面刷新后由后端重新加载任务记录和 Trace。
