#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_DIR="${PWD}/frontend/wot-vue"

if [ ! -d "$PROJECT_DIR" ]; then
  echo "[ERROR] Cannot find frontend/wot-vue directory."
  exit 1
fi

cd "$PROJECT_DIR"

if ! command -v npm &>/dev/null; then
  echo "[ERROR] npm is not installed or not in PATH."
  echo "Install Node.js LTS first: https://nodejs.org/"
  exit 1
fi

if [ ! -d "node_modules" ]; then
  echo "[INFO] Installing frontend dependencies..."
  npm install
fi

echo "[INFO] Starting frontend dev server on http://localhost:5173"
npm run dev
