# 测试文档

## wot-device-simulator

### `tests/test_rules.py`

| 测试 | 说明 |
|------|------|
| **TestCondition** — 条件模型 ||
| `test_gt` | 大于比较正确评估。 |
| `test_lt` | 小于比较正确评估。 |
| `test_gte` | 大于等于比较正确评估。 |
| `test_lte` | 小于等于比较正确评估。 |
| `test_eq` | 等于比较正确评估。 |
| `test_ne` | 不等于比较正确评估。 |
| `test_non_numeric` | 非数值传入时返回 False。 |
| `test_unknown_operator` | 未知操作符优雅返回 False。 |
| **TestEnvironmentRule** — 环境规则模型 ||
| `test_create_and_ready` | 新规则创建后即可触发。 |
| `test_cooldown` | 冷却期阻止重复触发。 |
| `test_disabled` | 禁用规则不被评估。 |
| `test_intent_default` | 默认规则意图为 control。 |
| `test_default_rules` | 默认规则包含预期条目。 |

### `tests/test_simulator.py`

| 测试 | 说明 |
|------|------|
| **TestDeviceStateStore** — SQLite 设备状态持久化 ||
| `test_initialize_creates_tables` | 初始化创建所有数据库表。 |
| `test_initialize_seeds_all_devices` | 初始化填充所有预期设备。 |
| `test_get_returns_empty_for_unknown` | 未知设备 ID 返回空字典。 |
| `test_get_property` | 设备属性值可读取。 |
| `test_get_property_nonexistent` | 不存在的属性返回 None。 |
| `test_set_property` | 属性值可更新。 |
| `test_set_property_creates_new_device` | 设置属性自动创建设备条目。 |
| `test_set_property_persists_to_db` | 属性变更持久化到 SQLite。 |
| `test_drift_changes_temperature` | 漂移模拟改变温度值。 |
| `test_drift_cooling_pulls_temp_down` | 制冷漂移使温度下降。 |
| `test_format_summary` | 摘要格式包含设备名称和属性。 |
| `test_get_all_with_info_includes_location` | 设备列表包含位置信息。 |
| `test_get_all_with_info_order` | 设备列表按 device_id 排序。 |
| `test_reset_clears_all` | 重置清除所有设备状态。 |
| **TestSimulatorEngine** — 事件驱动的设备仿真引擎 ||
| `test_start_stop` | 引擎可正常启停。 |
| `test_control_device_turn_on` | 开关灯动作使灯亮起。 |
| `test_control_device_turn_off` | 关灯动作使灯熄灭。 |
| `test_control_device_set_brightness` | 可设置亮度到指定值。 |
| `test_control_device_set_temperature` | 可设置目标温度。 |
| `test_control_device_set_mode` | 可切换空调模式。 |
| `test_control_device_nonexistent` | 控制不存在设备返回错误。 |
| `test_poll_once` | 轮询应用漂移并更新时间戳。 |
| `test_patch_state` | 状态修补更新数据库中的设备属性。 |
| `test_patch_state_nonexistent` | 修补不存在的设备返回 None。 |
| `test_apply_environment_temperature` | 环境温度影响所有设备。 |
| `test_control_device_stats` | 控制动作递增事件计数器。 |
| `test_inject_event` | 注入事件出现在最近事件列表中。 |
| `test_get_context` | 上下文字符串包含格式化的设备状态。 |
| `test_recent_events_limit` | 最近事件列表受最大长度限制。 |
| `test_subscribe_unsubscribe` | 订阅者可以订阅和取消订阅。 |

### `tests/test_td.py`

| 测试 | 说明 |
|------|------|
| **TestTDLoading** — Thing Description 加载与设备元数据 ||
| `test_load_tds` | 从远程 URL 加载 Thing Description。 |
| `test_load_tds_local` | 从本地文件加载 Thing Description。 |
| `test_td_has_device_id` | 每个 TD 包含有效 device_id。 |
| `test_td_has_properties` | 每个 TD 定义了预期属性。 |
| `test_td_has_actions` | 每个 TD 定义了预期动作。 |
| `test_light_has_luminance` | 光照设备的 TD 包含照度属性。 |
| `test_ac_has_temperature` | 空调设备的 TD 包含温度属性。 |
| `test_find_by_location` | 可按位置关键词查找设备。 |
| `test_get_device_by_id` | ID 查询返回 TD 元数据。 |
| `test_get_device_by_id_nonexistent` | 不存在设备返回 None。 |
| `test_device_id_extraction` | 从 TD 路径提取设备 ID。 |
| `test_location_synonyms` | 位置匹配支持同义词（如 livingroom/living room）。 |
