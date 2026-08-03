---
name: pawbench-run-eval
description: >-
  在 PawBench 仓库里跑评测（评估 AI agent / 模型在 benchmark 任务上的表现）。
  当用户说「跑一下评测」「跑个 pawbench」「测一下这个模型/agent」「跑几个 task 看看分数」
  或需要用 run_bench.py 执行 benchmark 时使用。
---

# PawBench 跑评测

统一入口是仓库根目录的 `run_bench.py`。每个 agent harness（QwenPaw、OpenClaw、Hermes、
Claude Code、Codex、Aider 等）都是跑在 `pawbench-base` Docker 镜像里的一个
Harbor `BaseInstalledAgent`。

## 前置准备

1. **Docker + 基础镜像**（首次跑之前必须构建一次；改了 harness 相关代码后需要重建）：

   ```bash
   docker build -f docker/Dockerfile.pawbench-base -t pawbench-base:latest .
   ```

   Harbor 不在 PyPI 上，Dockerfile 会直接从 fork 分支
   `XiaoBoAI/harbor@pawbench-agent-patches` 的固定 commit 用
   `pip install "harbor @ git+..."` 装，不需要手动克隆任何东西。

2. **凭证**：`.env`（仓库根目录，已被 `.gitignore` 忽略，不要打印或提交里面的值）里配好：
   `DASHSCOPE_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`（视用到的模型而定）+
   `JUDGE_API_KEY` / `JUDGE_BASE_URL`（LLM judge，可复用 agent 的 key）。跑之前
   `set -a && source .env && set +a`。

3. **宿主机 Python**：跑 `run_bench.py` 本身（不是容器里的 agent）需要 Python 3.12 +
   `harbor` 包。如果 `import harbor` 报 `No module named 'harbor.models'`，通常是
   仓库根目录下本地那份 `harbor/`（vendored monorepo 检出）把真正装好的包遮蔽了——
   换一个装了正确 `harbor` 包的 conda/venv 环境（这台机器上是 `pawbench_harbor`
   环境），或确认该环境里 `pip show harbor` 版本正常。

## 常用命令

```bash
# 最简单：默认 agent（harbor:qwenpaw）跑一个 task
python run_bench.py --tasks T053 --model dashscope/qwen3.6-plus

# 换一个 harness（harbor: 前缀可省略）
python run_bench.py --agents harbor:openclaw --tasks T053 --model dashscope/qwen3.6-plus

# 同一批 task，多个 harness 对比
python run_bench.py \
  --agents harbor:qwenpaw harbor:openclaw harbor:hermes \
  --model dashscope/qwen3.6-plus \
  --tasks T002 T006

# 多个模型依次跑
python run_bench.py --model dashscope/qwen3.6-plus --model anthropic/claude-sonnet-4-6

# 跑 harbor-v2 数据集（task.toml + tests/ 格式），指定 --dataset
python run_bench.py --dataset data_v2.3 --agents harbor:qwenpaw \
  --tasks ma-betweenness-01 --model dashscope/qwen3.6-plus

# 并发跑多个 task
python run_bench.py --model dashscope/qwen3.6-plus --concurrency 4
```

## 关键参数

| 参数 | 说明 |
|---|---|
| `--dataset NAME` | `<benchmark_path>/data/` 下的子目录名。省略时按 backend 选默认值（legacy 用 `pawbench-v1.0`，harbor-v2 用 `Pawbenchv2_task_0706`）。跑 `data_v2.1`/`data_v2.2`/`data_v2.3` 等新数据集时必须显式指定。 |
| `--backend {auto,pawbench,harbor-v2}` | `auto`（默认）按数据集目录结构自动判断：含 `task.toml`+`tests/` 走 `harbor-v2`，否则走legacy 的 Markdown `pawbench` 后端。 |
| `--agents AGENT...` | 默认 `harbor:qwenpaw`。可空格分隔传多个，依次跑。 |
| `--tasks TASK_ID...` | 默认跑数据集里全部 task；harbor-v2 数据集的 task id 是任务目录名（如 `ma-betweenness-01`），legacy v1.0 是 `T053` 这种。 |
| `--model MODEL_ID` | `provider/model`，如 `dashscope/qwen3.6-plus`、`openai/gpt-4o`、`anthropic/claude-sonnet-4-6`。可传多次。 |
| `--concurrency N` | 并行跑几个 task（默认 1）。 |
| `--runs N` | 每个 task 跑几次，取均值/方差/pass@k（默认 1）。 |
| `--judge MODEL_ID` / `--judge-api-key` / `--judge-base-url` | LLM judge 相关；不传就退回 agent 的 key/base-url。 |
| `--multi-agent-mode {native,single,adaptive,forced}` | 多智能体委派模式，默认 `native`（沿用 harness 自身配置）。仅 `claude-code`/`codex`/`openclaw`/`qwenpaw` 原生支持，其它 harness 会退回 `single` 并警告。 |
| `--results-dir DIR` | 结果输出根目录（默认 `./results`）。 |
| `--save-workspace` / `--no-save-workspace` | 是否收集 agent 最终工作区（默认收集）。 |
| `--verbose` | 打印详细日志，调试用。 |

完整参数列表：`python run_bench.py --help`。

## 结果去哪看

结果写在：

```
<results-dir>/<YYYYMMDD_HHMMSS>/<task_id>/<model>/<agent>/
├── summary/            # 自包含结果包：trajectory.json、reward/、workspace/
└── trials/             # 每次尝试的原始 trial 目录（含容器日志、verifier 输出）
```

同一次 `run_bench.py` 调用（哪怕是并行跑多个 `--agents` 进程）会共享同一个
`<run_ts>_combined_report.json`，聚合所有 task/model/agent 的分数，主要字段：

- `overall` — 整体分数汇总
- `score_matrix` — 按 task × model × agent 的分数矩阵
- `results` — 每次运行的明细：`score`/`max_score`/`passed`/`status`（`success`/`error`/
  `timed_out`）/`grading_type`（`automated`/`llm_judge`/`hybrid`）
- `task_labels` — 每个 task 的五维标签（scenario/capability/complexity/modality/environment）

单个 task 失败排查：先看终端打印的 `status=error` 一行里的错误摘要，详细信息在对应
`trials/<trial_id>/exception.txt` 和 `result.json` 里。

## 常见问题

- **`[pawbench] No tasks loaded — nothing to run.`**：`--tasks` 传的 task id 跟
  `--dataset`（或自动选中的默认数据集）不匹配。legacy v1.0 用 `T0xx`，harbor-v2 数据集
  用任务目录名——先确认 `--dataset` 对不对。
- **首次跑某个 harness 很慢**：`pawbench-base` 镜像没预装该 agent CLI 时，第一次跑会
  在容器里现装；重复跑同一 agent 会复用镜像里已装好的 CLI（更快）。
- **需要更多背景**（构建脚本细节、任务标签体系、支持的 harness 列表等）见仓库根目录
  `README.md` 的 "Quick Start" / "Evaluation Workflows" 一节。
