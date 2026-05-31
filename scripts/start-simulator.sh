#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_DIR="${PWD}/wot-device-simulator"

if [ ! -d "$PROJECT_DIR" ]; then
  echo "[ERROR] Cannot find wot-device-simulator directory."
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

echo "[INFO] Syncing simulator dependencies..."
uv sync

echo "[INFO] Starting simulator API on http://127.0.0.1:18080"
uv run --project . wot-sim-api
