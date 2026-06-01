# 测试文档

## wotagent-prototype

### `tests/test_core.py`

| 测试 | 说明 |
|------|------|
| **TestEventSchema** — 事件模式类型 ||
| `test_event_creation` | 验证事件创建必须字段。 |
| `test_event_types` | 验证事件类型匹配 `wot.*` 格式。 |
| **TestEventBus** — 事件总线发布/订阅 ||
| `test_emit_and_subscribe` | 订阅者能收到已发布的事件。 |
| `test_history_replay` | 后加入的订阅者可回放历史事件。 |
| `test_max_history` | 事件历史记录数受最大长度限制。 |
| `test_cursor` | 基于游标的事件回放正确工作。 |
| `test_subscriber_count` | 订阅者计数正确跟踪。 |
| `test_unsubscribe` | 取消订阅后回调不再接收事件。 |
| **TestRBAC** — 基于角色的访问控制 ||
| `test_role_hierarchy` | 角色层级能继承权限。 |
| `test_role_enum` | 所有角色枚举值可正常访问。 |
| `test_wildcard_match` | 通配符模式在权限检查中正确匹配。 |
| **TestWoT** — 设备注册中心查询 ||
| `test_find_devices` | 位置查询返回所有设备。 |
| `test_find_devices_by_capability` | 可按能力类型过滤设备。 |
| `test_find_devices_by_location` | 可按位置过滤设备。 |
| `test_get_device_by_id` | ID 查找设备返回正确结果。 |
| **TestMemory** — 核心会话记忆 ||
| `test_add_and_retrieve` | 消息可添加和读取。 |
| `test_window_trimming` | 消息窗口会修剪到配置大小。 |
| `test_clear` | 清除操作移除所有消息。 |
| `test_save_and_load` | 记忆持久化到磁盘并可重新加载。 |
| `test_load_nonexistent` | 加载不存在的文件保持记忆为空。 |
| `test_truncate` | 截断保留消息前缀。 |
| `test_truncate_negative_index` | 负索引从末尾截断。 |
| **TestTools** — 工具注册和设备列表 ||
| `test_get_all_tools` | 所有工具被列出且元数据正确。 |
| `test_tool_metadata` | 工具元数据包含名称、描述和模式。 |
| `test_list_devices_tool` | 设备列表工具返回设备名称。 |
| **TestAgent** — 智能体创建与流程编排 ||
| `test_create_agent` | 智能体创建时绑定工具。 |
| `test_create_pipeline` | Pipeline 包含智能体和记忆模块。 |
| **TestSession** — 会话管理 ||
| `test_create_and_get` | 会话可通过 ID 创建和读取。 |
| `test_get_or_create` | get_or_create 返回已有或新建会话。 |
| `test_session_expiry` | 过期会话不再被列为活跃。 |
| `test_list_active` | 仅返回活跃会话。 |
| **TestEventLogging** — 事件日志 ||
| `test_event_logger_writes_to_log_file` | 事件被写入日志文件。 |

### `tests/test_memory.py`

| 测试 | 说明 |
|------|------|
| **TestConversationMemory** — 会话消息缓冲区 ||
| `test_add_and_get_messages` | 消息按顺序存储并可读取。 |
| `test_clear` | 清除操作移除所有消息。 |
| `test_save_and_load` | 消息持久化到磁盘并可重新加载。 |
| **TestSessionTranscript** — 双 JSONL 日志记录器 ||
| `test_transcript_creation` | Transcript 记录事件并生成字典摘要。 |
| `test_load_chat_empty` | 加载不存在的会话返回空列表。 |
| `test_json_serializable` | to_dict() 输出可 JSON 序列化。 |

### `tests/test_patch.py`

| 测试 | 说明 |
|------|------|
| **TestPatchModule** — DeepSeek 推理补丁 ||
| `test_patch_importable` | 补丁模块可正常导入。 |
| `test_patches_applied` | 所有预期补丁被正确应用。 |
| `test_capture_reasoning_content` | 能从 DeepSeek 响应中捕获推理内容。 |
| `test_capture_no_reasoning` | 无推理内容时返回空字符串。 |

### `tests/test_perception.py`

| 测试 | 说明 |
|------|------|
| **TestCondition** — 设备属性阈值条件 ||
| `test_gt` | 大于比较正确评估。 |
| `test_lt` | 小于比较正确评估。 |
| `test_eq` | 等于比较正确评估。 |
| `test_non_numeric` | 非数值返回 False。 |
| **TestEnvironmentRule** — 自动化感知规则 ||
| `test_cooldown` | 冷却期阻止重复触发。 |
| `test_enabled_disabled` | 规则可启用和禁用。 |
| **TestPerceptionEngine** — 后台环境监控引擎 ||
| `test_poll_once_no_trigger` | 未超阈值时轮询返回空。 |
| `test_poll_once_trigger_high_temp` | 高温触发降温规则。 |
| `test_poll_once_cooldown_respected` | 规则遵守冷却期不重复触发。 |
| `test_get_context` | 上下文摘要包含设备状态。 |
| `test_start_stop` | 引擎可正常启停。 |
| `test_toggle_rule` | 单个规则可开关。 |
| `test_get_all_states_returns_cached_on_mcp_fail` | MCP 失败时返回缓存状态。 |
| **TestDefaultRules** — 内置默认规则 ||
| `test_default_rules_count` | 返回预期数量的规则。 |
| `test_default_rules_have_names` | 每条规则都有名称、描述和步骤。 |

### `tests/test_schemas.py`

| 测试 | 说明 |
|------|------|
| **TestDeviceModels** — 设备状态/动作模型 ||
| `test_device_state_response_defaults` | 设备状态响应包含默认字段。 |
| `test_device_action_response_roundtrip` | 动作响应可序列化与反序列化。 |
| `test_device_list_entry` | 设备列表条目包含必要元数据。 |
| `test_device_list_response` | 设备列表响应正确封装条目。 |
| **TestLegacySchemas** — 遗留模式模型 ||
| `test_objective` | Objective 模型有默认值。 |
| `test_task_spec_min_length` | Task spec 强制最小内容长度。 |
| `test_plan_step_optional_fields` | 计划步骤可选字段默认为 None。 |
| `test_plan_default_id` | 省略 ID 时自动生成。 |
| `test_execution_result` | 执行结果存储输出和状态。 |
| `test_environment_event` | 环境事件携带来源和数据。 |

### `tests/test_state.py`

| 测试 | 说明 |
|------|------|
| **TestConversationState** — 跨轮次意图连续性 ||
| `test_initial_state` | 初始状态上下文为空，无延续提示。 |
| `test_record_turn` | 记录轮次后上下文包含对话内容。 |
| `test_continuation_hint` | 意图激活且智能体提问时显示提示。 |
| `test_multiple_turns` | 仅最近对话出现在上下文中。 |
| `test_question_detection` | 问句结尾模式和关键词可检测。 |
| `test_reset_intent` | 显式重置清除活跃意图。 |
| `test_exchange_limit` | 内部仅保留最近 5 轮对话。 |
