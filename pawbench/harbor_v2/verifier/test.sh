#!/usr/bin/env bash
set -euo pipefail

FRAMEWORK="$(
  sed -n 's/^[[:space:]]*framework[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' \
    /tests/quality/agent_judge.toml | head -n1
)"

case "${FRAMEWORK:-rewardkit}" in
  openjudge)
    uv run \
      --with "py-openjudge @ git+https://github.com/agentscope-ai/OpenJudge.git@094298f9b19fcf426add9474ad4c3ef20c36d1d1" \
      --with "tomli>=2.0.0" \
      python /tests/quality/run_openjudge.py
    ;;
  *)
    uvx --from "harbor-rewardkit>=0.1.7" \
      rewardkit /tests --workspace /home/node/workspace
    ;;
esac

test -f /logs/verifier/reward.json
