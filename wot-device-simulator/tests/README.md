# Test Documentation

## wot-device-simulator

### `tests/test_rules.py`

| Test | Description |
|------|-------------|
| **TestCondition** ||
| `test_gt` | Greater-than comparison evaluates correctly. |
| `test_lt` | Less-than comparison evaluates correctly. |
| `test_gte` | Greater-than-or-equal comparison evaluates correctly. |
| `test_lte` | Less-than-or-equal comparison evaluates correctly. |
| `test_eq` | Equality comparison evaluates correctly. |
| `test_ne` | Not-equal comparison evaluates correctly. |
| `test_non_numeric` | Non-numeric values return False on comparison. |
| `test_unknown_operator` | Unknown operator returns False gracefully. |
| **TestEnvironmentRule** ||
| `test_create_and_ready` | New rule is ready to trigger. |
| `test_cooldown` | Cooldown period prevents re-triggering. |
| `test_disabled` | Disabled rule is not evaluated. |
| `test_intent_default` | Default rule intent is ``control``. |
| `test_default_rules` | Default rules include expected entries. |

### `tests/test_simulator.py`

| Test | Description |
|------|-------------|
| **TestDeviceStateStore** ||
| `test_initialize_creates_tables` | Initialization creates all DB tables. |
| `test_initialize_seeds_all_devices` | Initialization seeds all expected devices. |
| `test_get_returns_empty_for_unknown` | Unknown device ID returns empty dict. |
| `test_get_property` | Device property value is retrievable. |
| `test_get_property_nonexistent` | Nonexistent property returns None. |
| `test_set_property` | Property value can be updated. |
| `test_set_property_creates_new_device` | Setting a property creates a new device entry. |
| `test_set_property_persists_to_db` | Property change persists to SQLite. |
| `test_drift_changes_temperature` | Drift simulation changes temperature values. |
| `test_drift_cooling_pulls_temp_down` | Cooling drift pulls temperature downward. |
| `test_format_summary` | Summary format includes device name and properties. |
| `test_get_all_with_info_includes_location` | Device list includes location info. |
| `test_get_all_with_info_order` | Device list is ordered by device_id. |
| `test_reset_clears_all` | Reset clears all device states. |
| **TestSimulatorEngine** ||
| `test_start_stop` | Engine starts and stops cleanly. |
| `test_control_device_turn_on` | Turn-on action toggles light to on state. |
| `test_control_device_turn_off` | Turn-off action toggles light to off state. |
| `test_control_device_set_brightness` | Brightness can be set to specific value. |
| `test_control_device_set_temperature` | Target temperature can be set. |
| `test_control_device_set_mode` | AC mode can be changed. |
| `test_control_device_nonexistent` | Controlling a nonexistent device returns error. |
| `test_poll_once` | Polling applies drift and updates timestamps. |
| `test_patch_state` | State patch updates device properties in DB. |
| `test_patch_state_nonexistent` | Patching a nonexistent device returns None. |
| `test_apply_environment_temperature` | Environment temperature affects all devices. |
| `test_control_device_stats` | Control actions increment event counters. |
| `test_inject_event` | Injected event appears in recent events. |
| `test_get_context` | Context string includes formatted device states. |
| `test_recent_events_limit` | Recent events list respects max length. |
| `test_subscribe_unsubscribe` | Subscribers can subscribe and unsubscribe. |

### `tests/test_td.py`

| Test | Description |
|------|-------------|
| **TestTDLoading** ||
| `test_load_tds` | Thing Descriptions load from remote URL. |
| `test_load_tds_local` | Thing Descriptions load from local files. |
| `test_td_has_device_id` | Each TD has a valid device_id field. |
| `test_td_has_properties` | Each TD defines expected properties. |
| `test_td_has_actions` | Each TD defines expected actions. |
| `test_light_has_luminance` | Light device TD includes luminance property. |
| `test_ac_has_temperature` | AC device TD includes temperature property. |
| `test_find_by_location` | Devices can be found by location keyword. |
| `test_get_device_by_id` | Device lookup by ID returns TD metadata. |
| `test_get_device_by_id_nonexistent` | Nonexistent device returns None. |
| `test_device_id_extraction` | Device ID is extracted from TD path. |
| `test_location_synonyms` | Location matching supports synonyms (e.g. livingroom/living room). |
