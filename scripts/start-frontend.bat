@echo off
setlocal

cd /d "%~dp0.."
set "PROJECT_DIR=%CD%\frontend\wotagent-v2-console"

if not exist "%PROJECT_DIR%" (
  echo [ERROR] Cannot find frontend\wotagent-v2-console directory.
  exit /b 1
)

cd /d "%PROJECT_DIR%"

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm is not installed or not in PATH.
  echo Install Node.js LTS first: https://nodejs.org/
  exit /b 1
)

if not exist "node_modules" (
  echo [INFO] Installing frontend dependencies...
  npm install
  if errorlevel 1 exit /b 1
)

echo [INFO] Starting frontend dev server on http://127.0.0.1:5174
npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
