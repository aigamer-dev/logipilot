#!/usr/bin/env bash
# Starts the three services: tools API (8090), MCP server (8091), web app + agents ($PORT, default 8000).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONNOUSERSITE=1
# Hosts without gcloud (e.g. Hugging Face Spaces) pass the Firestore service-account key as a secret.
if [ -n "${GCP_SA_KEY_JSON:-}" ]; then
  umask 077 && printf '%s' "$GCP_SA_KEY_JSON" > /tmp/gcp-sa.json
  export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-sa.json
fi
PY="${PYTHON:-python}"
if [ "${SINGLE_PROCESS:-}" = "1" ]; then
  export TOOLS_API_URL="http://localhost:${PORT:-8000}/tools"
  export MCP_URL="http://localhost:${PORT:-8000}/mcp-server/mcp"
  exec "$PY" -m uvicorn logipilot.web.server:app --host 0.0.0.0 --port "${PORT:-8000}"
fi
export TOOLS_API_URL="${TOOLS_API_URL:-http://localhost:8090}"
export MCP_URL="${MCP_URL:-http://localhost:8091/mcp}"
"$PY" -m uvicorn logipilot.tools_api.main:app --host 0.0.0.0 --port 8090 --log-level warning &
PORT=8091 "$PY" -m logipilot.mcp_server.main &
for _ in $(seq 1 30); do curl -sf localhost:8090/health >/dev/null && break; sleep 1; done
exec "$PY" -m uvicorn logipilot.web.server:app --host 0.0.0.0 --port "${PORT:-8000}"
