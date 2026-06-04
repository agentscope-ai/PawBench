<h1 align="center">🐾 PawBench</h1>

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="#tasks">
    <img alt="tasks" src="https://img.shields.io/badge/tasks-150-2ea44f">
  </a>
  <a href="https://agentscope-ai.github.io/PawBench/">
    <img alt="models" src="https://img.shields.io/badge/models-9-0969da">
  </a>
  <a href="#harnesses">
    <img alt="harnesses" src="https://img.shields.io/badge/harnesses-3-8250df">
  </a>
  <a href="https://agentscope-ai.github.io/PawBench/">
    <img alt="leaderboard" src="https://img.shields.io/badge/leaderboard-live-cf222e">
  </a>
  <a href="https://github.com/agentscope-ai/PawBench">
    <img alt="GitHub repo" src="https://img.shields.io/badge/GitHub-pawbench-24292f">
  </a>
  <a href="LICENSE">
    <img alt="license" src="https://img.shields.io/badge/license-Apache%202.0-blue">
  </a>
</p>

<p align="center">
  <strong>A benchmark for evaluating LLM × harness performance.</strong><br>
  150 agent tasks · 9 models · 3 harnesses · diagnostic traces
</p>

---

The same model can behave very differently in different agent runtimes. When a task fails, the problem might be the model, the available tools, the workspace setup, or a completion check that was too loose. A final pass rate alone cannot tell these apart.

PawBench evaluates the model and the harness together:

$$\text{Agent Performance} = f(\text{LLM}, \text{Harness})$$

v1.0 covers **9 models × 3 harnesses × 150 tasks**. Our initial evaluation highlights a key finding:

- **Harness gaps are stable and significant**: The **5.6-point** average gap between the top and bottom harnesses is close to or even exceeds the performance gains typically brought by many model version upgrades (for example, on `qwen3.6-35b-a3b`, the performance gap between different harnesses reaches up to **11.5 points**).

See the [live leaderboard](https://agentscope-ai.github.io/PawBench/) for the full matrix and slice analysis.

![PawBench leaderboard overview](site/public/pawbench-leaderboard-overview.png)

With PawBench, you can:

- **Select:** Pick a model × harness setup for text, multimodal, skill, and web-search workloads.
- **Diagnose:** Compare a harness against the same model set and see which task slices it trails on.
- **Iterate:** Inspect traces and rerun slices after a fix to check whether the targeted score actually moves.

## Quick Start

### Requirements

Python 3.11+ and Docker are required. Node.js 20+ is only needed for the leaderboard site.

Install dependencies and add credentials. DashScope is the recommended provider for the default setup:

```bash
pip install -r requirements.txt

cat > .env <<'EOF'
DASHSCOPE_API_KEY=...
JUDGE_API_KEY=...
JUDGE_BASE_URL=...
EOF
```

For OpenAI-compatible or custom providers, set `OPENAI_API_KEY` / `OPENAI_BASE_URL` or `CUSTOM_API_KEY` / `CUSTOM_BASE_URL` as needed.

### Run Evaluation

Before the first run, build the default Docker harness image:

```bash
docker build -f docker/Dockerfile.pawbench-qwenpaw -t qwenclawbench-qwenpaw:latest .
```

```bash
# Smoke test: run one PawBench v1.0 task with the default qwenpaw harness
python run_bench.py --tasks T053 --model dashscope/qwen3.6-plus

# Pick a different harness
python run_bench.py --agents openclaw --tasks T053 --model dashscope/qwen3.6-plus

# Compare harnesses on a task subset
python run_bench.py \
  --agents qwenpaw openclaw hermes \
  --model dashscope/qwen3.6-plus \
  --tasks T002 T006

# Sequentially evaluate multiple models
python run_bench.py \
  --model dashscope/qwen3.6-plus \
  --model anthropic/claude-sonnet-4-6
```

Results are written under `./results/<YYYYMMDD_HHMMSS>/pawbench/<model>/<agent>/`. See `python run_bench.py --help` for all flags (`--no-results-version-path`, `--save-workspace`, `--save-docker-image`, ...).

### View the Leaderboard

```bash
cd site
npm install
npm run build:data    # aggregate raw run logs into submissions/ and JSON for the UI
npm run dev           # http://localhost:4321/PawBench/
```

## PawBench Design

### Tasks

PawBench follows a **Reuse & Tag** methodology. Instead of writing every task from scratch, it pulls tasks from established agent benchmark suites, normalizes them into one format, and tags each task across five orthogonal dimensions.

| Dimension | Field | Values |
| :--- | :--- | :--- |
| Scenario | `scenario` | 13 L1 categories such as `Office_Productivity`, `Software_Engineering`, `Safety_Alignment` |
| Capability | `capabilities` | `Logic_Reasoning`, `Math_Computation`, `Code_Manipulation`, `Tool_Use`, `Skill_Use`, `Planning`, `Self_Verification` |
| Complexity | `complexity` | `L1` (1-2 steps), `L2` (3-5 steps), `L3` (>5 steps with branches or backtracking) |
| Modality | `modality` | `text` or `multimodal` (`image`, `audio`, `video`) |
| Environment | `environment` | `closed` (offline, reproducible) or `open` (live internet / SaaS APIs) |

v1.0 contains **150 tasks** from `claweval`, `qwenclawbench`, `pinchbench`, PawBench self-built tasks, `skillsbench`, and `wildclawbench`.

| Source | # | Main coverage |
| :--- | ---: | :--- |
| [`claweval`](https://github.com/claw-eval/claw-eval) | 52 | Office productivity, data analytics, content creation |
| [`qwenclawbench`](https://github.com/SKYLENAGE-AI/QwenClawBench) | 29 | Automation, software engineering, safety alignment |
| [`pinchbench`](https://github.com/pinchbench/skill) | 23 | Office workflows, software engineering, information retrieval |
| PawBench | 21 | Self-built tasks covering automation, information retrieval, and safety alignment |
| [`skillsbench`](https://github.com/benchflow-ai/skillsbench) | 15 | Long-horizon skills, domain automation |
| [`wildclawbench`](https://github.com/InternLM/WildClawBench) | 10 | Office workflows, safety alignment |

### Harnesses

| Harness | Link |
| :--- | :--- |
| QwenPaw | [agentscope-ai/QwenPaw](https://github.com/agentscope-ai/QwenPaw) |
| OpenClaw | [openclaw/openclaw](https://github.com/openclaw/openclaw) |
| Hermes | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) |

### Evaluation setup

Each task declares one of three grading modes:

- `automated`: task-specific checks and assertions.
- `llm_judge`: LLM-as-judge for semantic outputs.
- `hybrid`: automated checks plus LLM judgment.

Runs can be sliced by source, scenario, capability, complexity, modality, environment, grading type, model, and harness. PawBench also stores transcripts and metrics for each task. With `--save-workspace` and `--save-docker-image`, it can preserve the agent workspace and final Docker image for deeper replay.

## Roadmap

☐ Add more harnesses, including Claude Code, Cursor Agent, CoPaw, and community scaffolds.

☐ Turn the blog findings into controlled experiments, especially around tool count, workspace awareness, skill discovery, web tools, and artifact-level completion checks.

☐ Add more task types for deeper harness stress testing, especially open-environment, multimodal, skill-heavy, and long-horizon tasks.

☐ Improve trace replay and slice diagnostics so harness regressions are easier to isolate.

## Contributing

Contributions are welcome in four areas:

- Integrate a new harness.
- Submit model evaluation results.
- Add tasks and annotate them with the five-label taxonomy.
- Improve the leaderboard, slice analysis, or trace tooling.

## Citation

If you use PawBench in your research or project, please cite it as:

```bibtex
@misc{pawbench,
  title  = {PawBench: A benchmark for evaluating LLM × harness performance},
  author = {The OpenJudge Team},
  url    = {https://github.com/agentscope-ai/PawBench},
  month  = {06},
  year   = {2026}
}
```

## Acknowledgments

PawBench is built on top of the open-source agent evaluation community, including [Claw-Eval](https://github.com/claw-eval/claw-eval), [QwenClawBench](https://github.com/SKYLENAGE-AI/QwenClawBench), [WildClawBench](https://github.com/InternLM/WildClawBench), [PinchBench](https://github.com/pinchbench/skill), [skillsbench](https://github.com/benchflow-ai/skillsbench), and others.
