#!/usr/bin/env bash
set -euo pipefail

cd /root/boyin.liu/pawbench-harbor-test

set -a
source ".env"
set +a

PROXY="http://123.57.212.178:3333/v1"
PKEY="$ANTHROPIC_API_KEY"
export OPENAI_API_KEY="$PKEY"
export OPENAI_BASE_URL="$PROXY"
export OPENAI_API_BASE="$PROXY"
export ANTHROPIC_API_KEY="$PKEY"
export ANTHROPIC_BASE_URL="http://123.57.212.178:3333"
export JUDGE_API_KEY="$PKEY"
export JUDGE_BASE_URL="$PROXY"
export USER_SIM_API_KEY="$PKEY"
export USER_SIM_BASE_URL="$PROXY"
export USER_SIM_MODEL="qwen3.6-plus"
export USER_SIM_TEMPERATURE="0.7"

STAMP="$(date +%Y%m%d_%H%M%S)"
RESULTS_ROOT="results/claude-code-rerun-all-${STAMP}"
IMAGE="pawbench-base:latest"
MODEL="anthropic/qwen3.6-plus"
JUDGE="openai/qwen3.7-max"
MA_TASKS=(ma-cec-09 ma-kmeanspp-01 ma-randomforest-03)

run_mode() {
    local mode="$1"
    shift
    local mode_flags=(--multi-agent-mode "$mode")
    echo "=== $(date '+%F %T') mode=${mode} ==="
    uv run python run_bench.py \
        --backend harbor-v2 \
        --dataset Pawbenchv2_task_0706 \
        --runs 1 \
        --concurrency 1 \
        --agents harbor:claude-code \
        --model "$MODEL" \
        --judge "$JUDGE" \
        --docker-image "$IMAGE" \
        --results-dir "${RESULTS_ROOT}/${mode}/claude-code" \
        "${mode_flags[@]}" \
        "$@"
}

run_mode single
run_mode adaptive --tasks "${MA_TASKS[@]}"
run_mode forced --tasks "${MA_TASKS[@]}"

uv run python scripts/package_claude_code_trajectories.py \
    --results-root "$RESULTS_ROOT" \
    --output "pawbenchv2_0706_three_modes_trajectories_claude_only.zip"

echo "ALL_DONE results=${RESULTS_ROOT}"
