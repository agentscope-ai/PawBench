#!/usr/bin/env bash
# 全 agent × 三模式(single/adaptive/forced)评测编排。
# 模型配置参考 pawbenchv2_0706_three_modes_trajectories_claude_only.zip:
#   被测模型 qwen3.6-plus, judge qwen3.7-max, 数据集 Pawbenchv2_task_0706。
# 统一走 Anthropic 中转代理 (123.57.212.178:3333),同时支持 OpenAI/Anthropic 两种格式。
# 统一镜像 pawbench-base:latest(预装全部 agent)。
set -u

cd /root/boyin.liu/pawbench-harbor-test

# ── 凭证 / 端点 ──────────────────────────────────────────────
set -a; source ".env"; set +a
PROXY="http://123.57.212.178:3333/v1"
PKEY="$ANTHROPIC_API_KEY"
export OPENAI_API_KEY="$PKEY" OPENAI_BASE_URL="$PROXY" OPENAI_API_BASE="$PROXY"
export ANTHROPIC_API_KEY="$PKEY" ANTHROPIC_BASE_URL="http://123.57.212.178:3333"
export JUDGE_API_KEY="$PKEY" JUDGE_BASE_URL="$PROXY"
export USER_SIM_API_KEY="$PKEY" USER_SIM_BASE_URL="$PROXY" USER_SIM_MODEL="qwen3.6-plus" USER_SIM_TEMPERATURE="0.7"

# ── 配置 ─────────────────────────────────────────────────────
DATASET="Pawbenchv2_task_0706"
JUDGE="openai/qwen3.7-max"
IMAGE="pawbench-base:latest"
CONC=2
STAMP="$(date +%Y%m%d_%H%M%S)"
BASE="results/allagents-3modes-${STAMP}"
mkdir -p "$BASE"

# harbor AgentName 合法名(mini-swe→mini-swe-agent, qwen-code→qwen-coder)
AGENTS=(mini-swe-agent aider hermes openclaw codex qwen-coder qwenpaw claude-code)
MA_TASKS=(ma-cec-09 ma-kmeanspp-01 ma-randomforest-03)

model_for() {
  case "$1" in
    claude-code) echo "anthropic/qwen3.6-plus" ;;
    *)           echo "openai/qwen3.6-plus" ;;
  esac
}

run_one() {  # $1=agent $2=mode $3=results-subdir  (rest=extra flags)
  local agent="$1" mode="$2" sub="$3"; shift 3
  local model; model="$(model_for "$agent")"
  local rdir="${BASE}/${sub}/${agent}"
  echo "==================================================================="
  echo "[$(date +%H:%M:%S)] agent=${agent} mode=${mode} model=${model}"
  echo "==================================================================="
  local ma_flags=(--multi-agent-mode "$mode")
  uv run python run_bench.py \
    --backend harbor-v2 \
    --dataset "$DATASET" \
    --runs 1 --concurrency "$CONC" \
    --agents "harbor:${agent}" \
    --model "$model" \
    --judge "$JUDGE" \
    --docker-image "$IMAGE" \
    --results-dir "$rdir" \
    "${ma_flags[@]}" "$@" \
    2>&1 | tee -a "${BASE}/${sub}_${agent}.log"
}

echo "############ PHASE single (all 10 tasks) ############"
for a in "${AGENTS[@]}"; do
  run_one "$a" single single
done

echo "############ PHASE adaptive (ma-* only) ############"
for a in "${AGENTS[@]}"; do
  run_one "$a" adaptive adaptive --tasks "${MA_TASKS[@]}"
done

echo "############ PHASE forced (ma-* only) ############"
for a in "${AGENTS[@]}"; do
  run_one "$a" forced forced --tasks "${MA_TASKS[@]}"
done

echo "ALL_DONE $(date +%Y-%m-%d_%H:%M:%S)  base=${BASE}"
