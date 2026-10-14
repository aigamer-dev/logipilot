#!/usr/bin/env bash
# Publishes the repo to a Hugging Face Docker Space. Needs `hf auth login` once.
# Usage: scripts/deploy_hf_space.sh <hf-user-or-org>/<space-name>
set -euo pipefail
SPACE="${1:?usage: deploy_hf_space.sh <owner>/<space>}"
cd "$(dirname "$0")/.."
hf repo create "$SPACE" --repo-type space --space-sdk docker --exist-ok >/dev/null
STAGE="$(mktemp -d)"
git archive HEAD | tar -x -C "$STAGE"
# A Space reads its config from README frontmatter; the GitHub README stays clean.
{ printf -- '---\ntitle: LogiPilot\nemoji: 🚚\ncolorFrom: indigo\ncolorTo: yellow\nsdk: docker\napp_port: 8000\npinned: false\n---\n\n'; cat README.md; } > "$STAGE/README.md"
hf upload "$SPACE" "$STAGE" . --repo-type space --commit-message "Deploy LogiPilot"
rm -rf "$STAGE"
echo "Space: https://huggingface.co/spaces/$SPACE"
