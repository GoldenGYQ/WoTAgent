#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/../wotagent-prototype"
echo "[INFO] Starting independent WoT monitor"
uv run --project . wotagent monitor --interval 2
