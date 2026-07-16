@echo off
setlocal

cd /d "%~dp0.."
set "PROJECT_DIR=%CD%\wotagent-prototype"

if not exist "%PROJECT_DIR%" (
  echo [ERROR] Cannot find wotagent-prototype directory.
  exit /b 1
)

cd /d "%PROJECT_DIR%"
echo [INFO] Starting independent WoT monitor
uv run --project . wotagent monitor --interval 2
