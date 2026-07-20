# [OPEN] Debug Session: failed-run-ac

## Problem
- Run ID: `run_7aa09487f137`
- Symptoms:
  - When a run contains `failed` events, the frontend trace panel stops updating until the page is refreshed and reopened.
  - Air-conditioner control does not execute; the final response claims the AC device cannot be controlled automatically.
- Goal:
  - Use runtime evidence to determine why trace updates freeze after failure events.
  - Use runtime evidence to determine whether AC control fails due to frontend state handling, planner output, TD/MCP capability registration, or device action mapping.

## Initial Hypotheses
- H1: The frontend trace subscription stops polling/streaming as soon as it sees a failed terminal state, so later events never render live.
- H2: The frontend trace UI enters a completed/frozen branch after a failed event and ignores later appended events even though the backend still serves them.
- H3: The AC control failure is caused by `environment_agent` generating a query-only plan instead of an executable cooling action.
- H4: The WoT TD / MCP registration for the AC lacks required control actions such as power on/off or cooling-related operations.
- H5: The planner/reviewer contract accepts a non-executable plan for a cooling objective, so the run degrades into notification-only handling.

## Evidence Collected
- Historical runtime evidence from `run_7aa09487f137`:
  - event `43 task.execution.context` shows `execution_device_ids` included `ac-002`
  - event `45 model.request.started` for planner shows `all_device_count=4` but `device_count=1`
  - event `49 plan.validation.failed` rejected a query-only cooling plan
  - event `52 agent.node.failed` reported `conditional plan repair violated TD capabilities: step step-2: device 'ac-002' is not registered`
  - event `91 run.completed` proves the backend continued recording events after the intermediate failure event
- TD evidence from `wot-device-simulator/data/td/ac_bedroom1.json`:
  - device `ac-002` declares `turnOn`
  - device `ac-002` declares `setTemperature`
  - therefore the AC is registered and controllable in the simulator catalog

## Findings
- H1/H2: Confirmed in frontend behavior. The runtime can close transport on terminal refresh without performing a final delta fetch, so late events can be missed until a manual page refresh reloads them.
- H3: Partially confirmed. The first control attempt produced a query-only plan, then repair attempted to use `ac-002`.
- H4: Rejected. The AC TD is present and exposes `turnOn/setTemperature`.
- H5: Confirmed. In observe-act loop mode the planner had a scoped runtime device set of 4 devices, but semantic capability selection reduced the prompt-visible set to 1 device, hiding `ac-002` from TD validation.

## Fixes Applied
- Added planner instrumentation to record `selected_device_ids` and `all_device_ids` in `model.request.started` events for future runs.
- Frontend `refreshRun()` now always fetches incremental events after the current cursor before closing transport on terminal states.
- Observe-act loop planning now keeps the full provided scoped device set instead of re-filtering it down and accidentally dropping the room actuator.

## Verification
- Backend regression: `pytest tests/test_planner_adapter_v2.py` passed (`23 passed`)
- Frontend regression: `vitest run src/composables/useAgentRuntime.test.ts` passed (`2 passed`)
- Frontend typecheck passed.
