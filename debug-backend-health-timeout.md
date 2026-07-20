# [OPEN] Debug Session: backend-health-timeout

## Problem
- Symptom:
  - Running `scripts/wotagent-stack.ps1` starts the simulator successfully.
  - The backend fails the health check within 30 seconds and the script throws:
    - `backend did not become healthy within 30 seconds. Check C:\Users\gyq16\AppData\Local\WoTAgent\stack\logs.`
- Expected:
  - Backend should become healthy before the timeout and the stack script should continue successfully.

## Reproduction
1. Run `d:\Users\gyq16\Desktop\PRJ\WoTAgent\scripts\wotagent-stack.ps1`
2. Observe simulator ready
3. Observe backend timeout and script throw

## Initial Hypotheses
- H1: The backend process exits or crashes during startup before exposing its health endpoint.
- H2: The stack script probes the wrong backend URL, port, or health path.
- H3: The backend runtime environment is incomplete or inconsistent, causing import/config failures during boot.
- H4: The backend remains healthy-capable but starts slower than 30 seconds, so the timeout threshold is too aggressive.

## Evidence To Collect
- Backend stdout/stderr under `C:\Users\gyq16\AppData\Local\WoTAgent\stack\logs`
- Health-check target used by `wotagent-stack.ps1`
- Backend startup command and environment from the script
- Whether the backend process stays alive after launch

## Evidence Collected
- `scripts/wotagent-stack.ps1` probes backend health via `http://127.0.0.1:8000/api/health`.
- `backend.stderr.log` shows the backend crashes during app import, before the health endpoint can come up.
- Concrete traceback:
  - `DeploymentProfileRegistry.load()` -> `DeploymentProfileCatalog.model_validate(...)`
  - `ValidationError: device 'light-002' belongs to both 'eldercare' and 'villa'`

## Hypothesis Status
- H1: Confirmed. Backend exits during startup.
- H2: Rejected. Health URL/path is correct; backend never becomes ready because import-time validation fails.
- H3: Rejected for this incident. No missing-package/import-runtime failure is shown in the backend traceback.
- H4: Rejected. This is not a slow-start issue; startup aborts immediately on config validation.

## Current Root Cause
- `config/deployment_profiles.json` defines overlapping device ownership between `eldercare` and `villa`.
- The recent eldercare device expansion reused `light-002` / `light-003`, which are already owned by `villa`.
- Profile validation intentionally forbids cross-profile device overlap, so the API process aborts before serving `/api/health`.

## Fix Applied
- Added dedicated eldercare light TDs:
  - `light-101` for `A-101`
  - `light-102` for `A-102`
- Updated `config/deployment_profiles.json` so `eldercare` uses `light-101` / `light-102` instead of `light-002` / `light-003`.
- Strengthened location alias matching so `A-101` and `bedroom1` resolve to the same room group in both backend and simulator TD lookup.
- Updated frontend new-conversation guidance so eldercare examples stay within eldercare semantics while smart-home examples remain smart-home specific.

## Verification
- Frontend test passed:
  - `npm run test -- src/components/TaskWorkspace.test.ts`
- Simulator tests passed:
  - `python -m pytest tests/test_scenes.py tests/test_td.py`
- Backend profile validation test passed:
  - `wotagent-prototype\.venv\Scripts\python.exe -m pytest tests/test_deployment_profiles.py`
- Stack startup passed:
  - `scripts/wotagent-stack.ps1 start-safe`
  - Evidence:
    - `backend is ready: http://127.0.0.1:8000/api/health`
    - `frontend is ready: http://127.0.0.1:5174/`
