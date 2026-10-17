#!/usr/bin/env bash
# Publishes web/static to Firebase Hosting, pointing the chat page at the Cloud Run API.
# The page calls Cloud Run directly (CORS-allowed): Hosting rewrites would cut turns at 60 s.
# Usage: scripts/deploy_firebase.sh [project-id]
set -euo pipefail
PROJECT="${1:-logipilot-gdg26-27638}"
cd "$(dirname "$0")/.."
API="$(gcloud run services describe logipilot --region us-central1 --project "$PROJECT" \
  --format='value(status.url)')"
rm -rf build/hosting && mkdir -p build && cp -r web/static build/hosting
printf 'window.LOGIPILOT_API = "%s";\n' "$API" > build/hosting/js/config.js
firebase deploy --only hosting --project "$PROJECT" --non-interactive
echo "Site: https://$PROJECT.web.app  ->  API: $API"
