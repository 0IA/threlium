#!/bin/bash
# Pre-download weights into HF cache (~/.cache/huggingface by default).
# Requires: python3, pip package huggingface_hub; HF_TOKEN in .env or environment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="${SCRIPT_DIR}/download-models.log"

if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  # shellcheck source=/dev/null
  source "${SCRIPT_DIR}/.env"
fi

: "${HF_TOKEN:=}"
export HF_HOME="${HF_HOME:-${HOME}/.cache/huggingface}"
export HF_TOKEN

if ! python3 -c "import huggingface_hub" 2>/dev/null; then
  echo "Install: python3 -m pip install --user huggingface_hub" >&2
  exit 1
fi

exec > >(tee -a "$LOG") 2>&1
echo "START $(date -Is) HF_HOME=${HF_HOME}"

python3 - <<'PY'
import os
from huggingface_hub import snapshot_download

token = os.environ.get("HF_TOKEN") or None
hf_home = os.environ["HF_HOME"]
repos = [
    "unsloth/Qwen3.6-35B-A3B-NVFP4",
    "BAAI/bge-m3",
    "BAAI/bge-reranker-v2-m3",
]

for repo_id in repos:
    print(f"--- {repo_id} ---")
    path = snapshot_download(
        repo_id=repo_id,
        cache_dir=hf_home,
        token=token,
    )
    print("snapshot:", path)

print("DONE")
PY

echo "DONE $(date -Is)"
