#!/usr/bin/env bash
# Deploys the Cloudflare Worker key vault and writes .env.judges (vault URL + one access token).
# Needs `npx wrangler login` once. Usage: scripts/deploy_vault.sh <path-to-gcp-sa-key.json>
set -euo pipefail
SA_KEY="${1:?usage: deploy_vault.sh <gcp-service-account-key.json>}"
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
W="npx --yes wrangler@3"
TOKEN="lpv_$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
cd vault
printf '%s' "$TOKEN" | $W secret put VAULT_TOKEN >/dev/null
printf '%s' "$TYPESAFE_API_KEY" | $W secret put TYPESAFE_API_KEY >/dev/null
printf '%s' "$OLLAMA_KEY" | $W secret put OLLAMA_KEY >/dev/null
printf '%s' "$GEMINI_API_KEY" | $W secret put GEMINI_API_KEY >/dev/null
printf '%s' "$GOOGLE_CLOUD_PROJECT" | $W secret put GOOGLE_CLOUD_PROJECT >/dev/null
$W secret put GCP_SA_KEY_JSON < "$SA_KEY" >/dev/null
URL="$($W deploy 2>&1 | grep -oE 'https://[a-z0-9.-]+\.workers\.dev' | head -1)"
cd ..
cat > .env.judges <<J
# Judges: copy this file to .env. It holds no API keys, only a revocable token for the key vault.
KEY_VAULT_URL=$URL
KEY_VAULT_TOKEN=$TOKEN
J
echo "Vault: $URL  (.env.judges written)"
