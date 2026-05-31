@echo off
setlocal

cd /d "%~dp0.."
set "PROJECT_DIR=%CD%\wotagent-prototype"

if not exist "%PROJECT_DIR%" (
  echo [ERROR] Cannot find wotagent-prototype directory.
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

echo [INFO] Syncing backend dependencies...
uv sync
if errorlevel 1 exit /b 1

if not exist ".env" if exist ".env.example" (
  copy /Y ".env.example" ".env" >nul
  echo [INFO] Created .env from .env.example. Update API key if needed.
)

echo [INFO] Starting WoTAgent backend on http://127.0.0.1:8000
uv run --project . wotagent api --host 127.0.0.1 --port 8000
