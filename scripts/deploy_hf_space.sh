#!/usr/bin/env bash
# Publishes the repo to a Hugging Face Docker Space. Needs `hf auth login` once.
# Secrets (TYPESAFE_API_KEY, OLLAMA_KEY, GCP_SA_KEY_JSON) and the GOOGLE_CLOUD_PROJECT variable
# are set once in the Space settings; this script only ships code.
# Usage: scripts/deploy_hf_space.sh <hf-user-or-org>/<space-name>
set -euo pipefail
SPACE="${1:?usage: deploy_hf_space.sh <owner>/<space>}"
cd "$(dirname "$0")/.."
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git archive HEAD | tar -x -C "$STAGE"
# A Space reads its config from README frontmatter; the GitHub README stays clean.
{
  printf -- '---\ntitle: LogiPilot\nemoji: 🚚\ncolorFrom: indigo\ncolorTo: yellow\n'
  printf -- 'sdk: docker\napp_port: 8000\npinned: false\n---\n\n'
  cat README.md
} > "$STAGE/README.md"
uvx --from huggingface_hub python - "$SPACE" "$STAGE" <<'PY'
import sys
from huggingface_hub import HfApi

space, folder = sys.argv[1], sys.argv[2]
api = HfApi()
api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
api.upload_folder(repo_id=space, repo_type="space", folder_path=folder,
                  commit_message="Deploy LogiPilot")
print(f"Space: https://huggingface.co/spaces/{space}")
PY
