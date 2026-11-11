#!/usr/bin/env bash
# Starts the Rate Shopper web app inside a codespace, unless it is already running.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
if curl -fs -o /dev/null http://127.0.0.1:8765/api/config; then
  echo "Rate Shopper is already running: see the Ports tab, port 8765."
  exit 0
fi
echo "Starting Rate Shopper on port 8765. Its page opens in a new browser tab."
exec hotelrate-mcp web --port 8765
