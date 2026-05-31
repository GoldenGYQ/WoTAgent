#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_DIR="${PWD}/wotagent-prototype"

if [ ! -d "$PROJECT_DIR" ]; then
  echo "[ERROR] Cannot find wotagent-prototype directory."
  exit 1
fi

cd "$PROJECT_DIR"

if ! command -v uv &>/dev/null; then
  echo "[ERROR] uv is not installed or not in PATH."
  echo "Install uv first: https://docs.astral.sh/uv/"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[INFO] Creating Python virtual environment..."
  uv venv
fi

echo "[INFO] Syncing backend dependencies..."
uv sync

echo "[INFO] Starting WoTAgent backend on http://127.0.0.1:8000"
uv run --project . python -m wotagent.cli api --host 127.0.0.1 --port 8000
