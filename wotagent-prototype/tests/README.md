# Test Documentation

## wotagent-prototype

### `tests/test_core.py`

| Test | Description |
|------|-------------|
| **TestEventSchema** ||
| `test_event_creation` | Event can be created with required fields. |
| `test_event_types` | Event types match expected pattern `wot.*`. |
| **TestEventBus** ||
| `test_emit_and_subscribe` | Subscribers receive emitted events. |
| `test_history_replay` | Late subscribers can replay past events. |
| `test_max_history` | Event history respects max length. |
| `test_cursor` | Cursor-based replay works correctly. |
| `test_subscriber_count` | Subscriber count tracks correctly. |
| `test_unsubscribe` | Unsubscribed callbacks stop receiving events. |
| **TestRBAC** ||
| `test_role_hierarchy` | Role hierarchy grants inherited permissions. |
| `test_role_enum` | All role enum values are accessible. |
| `test_wildcard_match` | Wildcard patterns match correctly in permission checks. |
| **TestWoT** ||
| `test_find_devices` | All devices are returned by location query. |
| `test_find_devices_by_capability` | Devices filterable by capability type. |
| `test_find_devices_by_location` | Devices filterable by location. |
| `test_get_device_by_id` | Device lookup by ID returns correct device. |
| **TestMemory** ||
| `test_add_and_retrieve` | Messages can be added and retrieved. |
| `test_window_trimming` | Message window trims to configured size. |
| `test_clear` | Clearing removes all messages. |
| `test_save_and_load` | Memory persists and reloads from disk. |
| `test_load_nonexistent` | Loading nonexistent file leaves memory empty. |
| `test_truncate` | Truncation keeps prefix of messages. |
| `test_truncate_negative_index` | Negative index truncates from end. |
| **TestTools** ||
| `test_get_all_tools` | All tools are listed with correct metadata. |
| `test_tool_metadata` | Tool metadata includes name, description, and schema. |
| `test_list_devices_tool` | List-devices tool returns device names. |
| **TestAgent** ||
| `test_create_agent` | Agent is created with bound tools. |
| `test_create_pipeline` | Pipeline is created with agent and memory. |
| **TestSession** ||
| `test_create_and_get` | Session is created and retrievable by ID. |
| `test_get_or_create` | get_or_create returns existing or new session. |
| `test_session_expiry` | Expired sessions are not returned as active. |
| `test_list_active` | Only active sessions are listed. |
| **TestEventLogging** ||
| `test_event_logger_writes_to_log_file` | Events are written to the log file. |

### `tests/test_memory.py`

| Test | Description |
|------|-------------|
| **TestConversationMemory** ||
| `test_add_and_get_messages` | Messages are stored and retrievable in order. |
| `test_clear` | Clearing removes all messages. |
| `test_save_and_load` | Messages persist to disk and reload correctly. |
| **TestSessionTranscript** ||
| `test_transcript_creation` | Transcript records events and produces a dict summary. |
| `test_load_chat_empty` | Loading a nonexistent session returns empty list. |
| `test_json_serializable` | Transcript to_dict() output is JSON-serializable. |

### `tests/test_patch.py`

| Test | Description |
|------|-------------|
| **TestPatchModule** ||
| `test_patch_importable` | Patch module imports without error. |
| `test_patches_applied` | All expected patches are applied correctly. |
| `test_capture_reasoning_content` | Reasoning content is captured from DeepSeek responses. |
| `test_capture_no_reasoning` | Responses without reasoning return empty string. |

### `tests/test_perception.py`

| Test | Description |
|------|-------------|
| **TestCondition** ||
| `test_gt` | Greater-than comparison evaluates correctly. |
| `test_lt` | Less-than comparison evaluates correctly. |
| `test_eq` | Equality comparison evaluates correctly. |
| `test_non_numeric` | Non-numeric values return False on comparison. |
| **TestEnvironmentRule** ||
| `test_cooldown` | Cooldown period prevents re-triggering. |
| `test_enabled_disabled` | Rule can be enabled and disabled. |
| **TestPerceptionEngine** ||
| `test_poll_once_no_trigger` | Poll returns empty when no thresholds are exceeded. |
| `test_poll_once_trigger_high_temp` | High temperature triggers the cooling rule. |
| `test_poll_once_cooldown_respected` | Rule respects cooldown and does not re-trigger. |
| `test_get_context` | Context summary includes device state information. |
| `test_start_stop` | Engine starts and stops cleanly. |
| `test_toggle_rule` | Individual rules can be toggled on/off. |
| `test_get_all_states_returns_cached_on_mcp_fail` | Returns cached states when MCP call fails. |
| **TestDefaultRules** ||
| `test_default_rules_count` | Returns the expected number of rules. |
| `test_default_rules_have_names` | Every default rule has a name, description, and steps. |

### `tests/test_schemas.py`

| Test | Description |
|------|-------------|
| **TestDeviceModels** ||
| `test_device_state_response_defaults` | Device state response includes default fields. |
| `test_device_action_response_roundtrip` | Action response serializes and deserializes. |
| `test_device_list_entry` | Device list entry includes required metadata. |
| `test_device_list_response` | Device list response wraps entries correctly. |
| **TestLegacySchemas** ||
| `test_objective` | Objective model has default values. |
| `test_task_spec_min_length` | Task spec enforces minimum content length. |
| `test_plan_step_optional_fields` | Plan step optional fields are None by default. |
| `test_plan_default_id` | Plan generates default ID when omitted. |
| `test_execution_result` | Execution result stores output and status. |
| `test_environment_event` | Environment event carries source and data. |

### `tests/test_state.py`

| Test | Description |
|------|-------------|
| **TestConversationState** ||
| `test_initial_state` | Fresh state has empty context and no continuation hint. |
| `test_record_turn` | Recording a turn produces context with the exchange. |
| `test_continuation_hint` | Hint appears when intent is active and agent asked a question. |
| `test_multiple_turns` | Only recent exchanges appear in context. |
| `test_question_detection` | Question-ending patterns and keywords are detected. |
| `test_reset_intent` | Explicit reset clears active intent. |
| `test_exchange_limit` | Only the last 5 exchanges are retained internally. |
