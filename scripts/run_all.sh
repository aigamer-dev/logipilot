#!/usr/bin/env bash
# Starts the three services: tools API (8090), MCP server (8091), web app + agents ($PORT, default 8000).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONNOUSERSITE=1
PY="${PYTHON:-python}"
export PORT="${PORT:-8000}"  # hosts such as SnapDeploy inject PORT
# Shared secret between the agents and the internal tools API / MCP server, fresh per boot.
export TOOLS_API_KEY="${TOOLS_API_KEY:-$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')}"
if [ "${SINGLE_PROCESS:-}" = "1" ]; then
  # The app itself runs on a private loopback port behind the fast-start front door (web/boot.py).
  export APP_INTERNAL_PORT="${APP_INTERNAL_PORT:-8001}"
  export TOOLS_API_URL="http://127.0.0.1:${APP_INTERNAL_PORT}/tools"
  export MCP_URL="http://127.0.0.1:${APP_INTERNAL_PORT}/mcp-server/mcp"
  exec "$PY" -m uvicorn logipilot.web.boot:app --host 0.0.0.0 --port "${PORT:-8000}" --no-server-header
fi
export TOOLS_API_URL="${TOOLS_API_URL:-http://localhost:8090}"
export MCP_URL="${MCP_URL:-http://localhost:8091/mcp}"
"$PY" -m uvicorn logipilot.tools_api.main:app --host 127.0.0.1 --port 8090 --log-level warning --no-server-header &
PORT=8091 "$PY" -m logipilot.mcp_server.main &
for _ in $(seq 1 30); do curl -sf localhost:8090/health >/dev/null && break; sleep 1; done
exec "$PY" -m uvicorn logipilot.web.boot:app --host 0.0.0.0 --port "${PORT:-8000}" --no-server-header
