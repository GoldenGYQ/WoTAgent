@echo off
setlocal

cd /d "%~dp0.."
set "PROJECT_DIR=%CD%\wot-device-simulator"

if not exist "%PROJECT_DIR%" (
  echo [ERROR] Cannot find wot-device-simulator directory.
  exit /b 1
)

cd /d "%PROJECT_DIR%"

where uv >nul 2>&1
if errorlevel 1 (
  echo [ERROR] uv is not installed or not in PATH.
  echo Install uv first: https://docs.astral.sh/uv/
  exit /b 1
)

if not exist ".venv" (
  echo [INFO] Creating Python virtual environment...
  uv venv
  if errorlevel 1 exit /b 1
)

echo [INFO] Syncing simulator dependencies...
uv sync
if errorlevel 1 exit /b 1

echo [INFO] Starting simulator API on http://127.0.0.1:18080
uv run --project . wot-sim-api
