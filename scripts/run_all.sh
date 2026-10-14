#!/usr/bin/env bash
# Starts the three services: tools API (8080), MCP server (8081), web app + agents ($PORT, default 8000).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONNOUSERSITE=1
PY="${PYTHON:-python}"
export TOOLS_API_URL="${TOOLS_API_URL:-http://localhost:8080}"
export MCP_URL="${MCP_URL:-http://localhost:8081/mcp}"
"$PY" -m uvicorn logipilot.tools_api.main:app --host 0.0.0.0 --port 8080 --log-level warning &
PORT=8081 "$PY" -m logipilot.mcp_server.main &
for _ in $(seq 1 30); do curl -sf localhost:8080/health >/dev/null && break; sleep 1; done
exec "$PY" -m uvicorn logipilot.web.server:app --host 0.0.0.0 --port "${PORT:-8000}"
