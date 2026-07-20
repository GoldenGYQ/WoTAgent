# [OPEN] Debug Session: room-temp-fail

## Problem
- Run ID: `USERrun_f418755a17fd`
- User task: `把张爷爷房间温度调到 24 度，并确认是否生效`
- Symptom: task/run failed unexpectedly
- Goal: use real runtime evidence and, if needed, real LLM execution to identify the root cause

## Initial Hypotheses
- H1: 目标实体解析错误，`张爷爷房间` 没有被正确映射到可控空间/设备，导致下游任务准备失败。
- H2: Deployment/RBAC/MCP scope 限制拦截了温控动作，请求在调度前或执行前被拒绝。
- H3: Supervisor/Environment Agent 选错了工具或任务拆分错误，导致控制链路走偏。
- H4: 设备侧或 WoT/MCP 网关执行成功，但“确认是否生效”的回读校验失败，最终被判成失败。
- H5: 当前真实模型在该请求上输出了不符合结构约束的决策/参数，导致运行时解析或修复失败。

## Evidence Plan
- 先读取该 Run 的持久化记录、事件流、任务列表与失败字段。
- 再定位对应后端日志/运行轨迹，判断失败发生在 Supervisor、TaskPreparation、AgentExecutor 还是 Device/MCP 层。
- 若现有证据不足，再做最小化运行时插桩并用真实模型复现同类请求。

## Status
- Evidence collected from historical run and real-model reproductions
- Instrumentation added to runtime events
- Minimal fixes implemented and post-fix verified

## Evidence Collected
- Historical failed run in DB is `run_f418755a17fd` rather than `USERrun_f418755a17fd`.
- `run_f418755a17fd` failed before any task creation:
  - `task_ids=[]`
  - `supervisor_iterations=0`
  - events: `run.started -> model.request.started -> model.request.failed -> run.failed`
- Pre-fix `model.request.failed` contained an empty error string, but the duration was ~12s, matching `llm_timeout_seconds=12.0`.
- Real supervisor invocation against the configured DeepSeek endpoint succeeded with a valid structured `SupervisorDecision`, proving the business request itself is valid and the model route is reachable.
- Forced timeout probe with `llm_timeout_seconds=1.0` now records:
  - `error_type=TimeoutError`
  - `error_repr=TimeoutError()`
  - `timeout_seconds=1.0`
- Real end-to-end reproduction before resident-context fix sometimes clarified instead of executing because `environment_agent` received:
  - `target=null`
  - a narrowed `effective_scope`, but no explicit resident-room mapping
- Real end-to-end reproduction after resident-context fix completed successfully:
  - target resolved to `elderly-001`
  - resident context included `room=A-101`, `environment_device_id=ac-002`, `environment_sensor_device_id=environment-sensor-001`
  - final response confirmed temperature adjusted to 24C and effective

## Hypothesis Status
- H1: 目标实体解析错误，`张爷爷房间` 没有被正确映射到可控空间/设备，导致下游任务准备失败。
  - Partially confirmed. Not the cause of the historical hard failure, but a real instability in the normal execution path because resident context was not injected into `environment_agent`.
- H2: Deployment/RBAC/MCP scope 限制拦截了温控动作，请求在调度前或执行前被拒绝。
  - Rejected. Effective scope included the resident's authorized devices and real MCP control calls succeeded.
- H3: Supervisor/Environment Agent 选错了工具或任务拆分错误，导致控制链路走偏。
  - Rejected for the historical failed run. The run died before task creation. For live runs, the supervisor produced a correct single `environment_agent` task.
- H4: 设备侧或 WoT/MCP 网关执行成功，但“确认是否生效”的回读校验失败，最终被判成失败。
  - Rejected for the historical failed run. No device execution occurred. Post-fix live run verified both control and confirmation successfully.
- H5: 当前真实模型在该请求上输出了不符合结构约束的决策/参数，导致运行时解析或修复失败。
  - Rejected. Real-model supervisor output was valid and structured.

## Root Cause
- Root cause A: The historical failed run was caused by a supervisor LLM timeout at the very first model request. The previous runtime only recorded an empty error string, making the failure opaque.
- Root cause B: In eldercare environment-control tasks, the runtime narrowed device scope correctly but did not always inject the resolved resident target and resident room/device facts into the delegated environment-agent context. This allowed the planner to occasionally ask unnecessary clarification questions about the resident's room.
- Root cause C: Clarification follow-ups such as `A-101` were not normalized into the original goal context. The conversation store preserved plain chat history, but there was no deterministic continuation binding that converted a short slot-filling reply into `original goal + supplied room`. This allowed the supervisor to re-ask the same room question or reinterpret the reply as a brand new request.

## Fixes Applied
- Instrumentation:
  - `model.request.failed` now records `error_type`, `error_repr`, and `timeout_seconds`.
  - `task.execution.context` now records delegated target/effective scope/runtime context.
- Timeout hardening:
  - default `AGENT_LLM_TIMEOUT_SECONDS` increased from `12` to `20`.
  - supervisor timeout now re-raises with an explicit timeout message.
- Resident-context fix:
  - eldercare `environment_agent` tasks now resolve resident targets when possible.
  - delegated runtime context now includes `resident_context` and `resolved_target`.
- Failed-run summary visibility:
  - `RunRecord` now persists `error`.
  - failed runs now write both `error` and `final_response`.
  - frontend runtime restores `run.error` directly from `GET /runs/{id}`, so refresh/reload still shows the failure reason without replaying events.
- Clarification continuation normalization:
  - supervisor input preprocessing now recognizes short room answers such as `A-101` / `A-102`.
  - when the immediately preceding assistant turn is asking for the room, the supervisor receives a rewritten request in the form `original goal + 补充信息：目标房间是 A-101` instead of the bare follow-up token.

## Post-Fix Verification
- Real model + real backend + real MCP run completed successfully.
- Verified final response:
  - `张爷爷房间的空调已经调到24度了，现在室温也稳定在24度，已经生效了，您放心。`
- Verified delegated execution context contained:
  - `target = elderly-001`
  - `room = A-101`
  - `environment_device_id = ac-002`
  - `environment_sensor_device_id = environment-sensor-001`
- API contract verification:
  - backend `pytest tests/test_api_v2.py -q` passed (`6 passed`)
  - frontend `vitest` for runtime/api contracts passed (`9 passed`)
- Runtime regression verification:
  - backend `pytest tests/test_run_runtime.py -q` passed (`10 passed`)
  - unit verification confirms `A-101 啊！` is normalized into the previous temperature-control goal when the prior assistant turn was a room clarification.
