#!/usr/bin/env bash
# Apply PawBench's local fixes to the vendored (and gitignored) harbor/ tree.
#
# The harbor/ directory is excluded from version control (.gitignore), so the
# agent-side fixes it needs cannot be committed directly. They are shipped as a
# patch under patches/ and applied here. This script is idempotent: running it
# repeatedly is safe (already-applied patches are detected and skipped).
#
# Usage (from anywhere):
#   scripts/apply-harbor-patches.sh
#
# Run this once after obtaining/refreshing the harbor/ tree and before building
# the Docker image or using the host conda env (editable install picks the fix
# up live).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PATCH="patches/harbor-agent-fixes.patch"

if [ ! -d harbor ]; then
  echo "[apply-harbor-patches] harbor/ not found under $REPO_ROOT — nothing to patch."
  exit 0
fi
if [ ! -f "$PATCH" ]; then
  echo "[apply-harbor-patches] ERROR: patch file $PATCH is missing." >&2
  exit 1
fi

patch_semantics_present() {
  rg -q 'urlparse\(base_url\)' harbor/src/harbor/agents/installed/qwenpaw.py &&
  rg -q '_DEFAULT_QWENPAW_VERSION = "2\.0\.0\.post3"' harbor/src/harbor/agents/installed/qwenpaw.py &&
  rg -q 'MULTI_AGENT' harbor/src/harbor/agents/installed/qwenpaw.py &&
  rg -q 'hermes sessions export /logs/agent/hermes-session\.jsonl' harbor/src/harbor/agents/installed/hermes.py &&
  ! rg -q 'sessions export .*--source cli' harbor/src/harbor/agents/installed/hermes.py &&
  rg -q '_build_multi_agent_args' harbor/src/harbor/agents/installed/codex.py &&
  rg -q 'CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS' harbor/src/harbor/agents/installed/claude_code.py
}

if git apply --reverse --check "$PATCH" 2>/dev/null || patch_semantics_present; then
  echo "[apply-harbor-patches] already applied — skipping."
elif git apply --check "$PATCH" 2>/dev/null; then
  git apply "$PATCH"
  echo "[apply-harbor-patches] applied $PATCH"
else
  echo "[apply-harbor-patches] ERROR: $PATCH does not apply cleanly to harbor/." >&2
  echo "  The vendored harbor tree likely diverged from when the patch was made." >&2
  echo "  Re-create it with the two fixes documented in the patch header." >&2
  exit 1
fi
