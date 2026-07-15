#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../frontend/wotagent-v2-console"

if ! command -v npm &>/dev/null; then
  echo "[ERROR] npm is not installed or not in PATH."
  exit 1
fi

if [ ! -d node_modules ]; then
  npm install
fi

echo "[INFO] Starting WoTAgent 2.0 console"
npm run dev
