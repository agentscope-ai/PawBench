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
  <a href="https://github.com/agentscope-ai/OpenJudge">
    <img alt="OpenJudge Ecosystem" src="https://img.shields.io/badge/ecosystem-OpenJudge-blue?logo=github&color=0969da">
  </a>
  <a href="LICENSE">
    <img alt="license" src="https://img.shields.io/badge/license-Apache%202.0-blue">
  </a>
</p>

<p align="center">
  <strong>A Model × Harness co-evaluation benchmark for agentic AI.</strong><br>
  150 agent tasks · 9 models · 3 harnesses · task slices · diagnostic traces
</p>

---

The same model can behave very differently once it is placed inside a real agent runtime. A failure may come from model reasoning, missing tools, weak skill discovery, poor workspace awareness, brittle web access, or a completion check that is too loose. A single final pass rate cannot separate these causes.

PawBench is built around one claim:

$$\text{Agent Performance} = f(\text{Model}, \text{Harness})$$

> [!NOTE]
> PawBench is part of the [OpenJudge](https://github.com/agentscope-ai/OpenJudge) ecosystem. It shares OpenJudge's philosophy of evaluation-driven optimization, but focuses specifically on the interaction between LLMs and agent harnesses.

It evaluates **the model and the harness together** while keeping enough metadata to read both dimensions independently. v1.0 covers **9 models × 3 harnesses × 150 tasks**, with public prompts, graders, task labels, submissions, and leaderboard slices.

![PawBench overview and taxonomy](site/public/pawbench-overview-taxonomy.png)

With PawBench, you can:

- **Select models & harnesses** for text, multimodal, skill-heavy, and web-search workloads.
- **Diagnose** whether a regression comes from the model, the harness, or the grader.
- **Iterate** on a harness change, rerun the same task slice, and check whether the targeted score actually moves.
- **Contribute** new harnesses, tasks, graders, submissions, and bug fixes back into a shared evaluation loop.

## Core Findings

The initial PawBench v1.0 runs show that harness design is not a minor implementation detail. It can change the realized capability of the same model by a margin comparable to many model upgrades.

Unless otherwise noted, the numbers below come from this evaluation setting: **150 PawBench v1.0 tasks**, **9 models**, **3 harnesses** (`qwenpaw`, `openclaw`, `hermes`), and **claude opus 4.6 as judge**. Scores are reported as overall percentages.

![Harness gap analysis](site/public/pawbench-harness-gap.png)

- **Harness gaps are visible even when the model is fixed.** With the same `qwen3.6-35b-a3b` model on the same 150 tasks, QwenPaw scores **68.3**, OpenClaw **68.2**, and Hermes **56.7**, leaving an **11.5-point** spread. This is not isolated to one model: `qwen3.6-max-preview` has a **10.3-point** harness spread, `glm-5.1` has a **9.9-point** spread, and six of the nine tested models move by more than three points across harnesses.
- **Average performance differs across harnesses.** Averaged across the 27 model × harness submissions in this run, QwenPaw scores **74.9**, OpenClaw **72.9**, and Hermes **69.3**. The overall leaderboard is only the first view; slice analysis is what shows which harness is brittle on which capability, source, scenario, or modality.

![Slice diagnostics](site/public/pawbench-slice-diagnostics.png)

Slice numbers below are macro-averages across the same 27 model × harness submissions. They point to several high-value improvement areas:

- **Skill-heavy tasks are the hardest.** `Skill_Use` averages **47.2**, and `skillsbench` tasks average **40.9**, suggesting that skill discovery, skill loading, and procedural execution are still fragile.
- **Multimodal tasks remain harder than text.** Text-only tasks average **74.1**, while multimodal tasks average **64.0**.
- **Open environments add real friction.** Closed, reproducible tasks average **72.9**; open-environment tasks average **68.9**.
- **Some domains expose much larger harness differences than the overall score.** Finance, information retrieval, manufacturing quality control, and software-engineering slices are useful targets for harness debugging.

See the [live leaderboard](https://agentscope-ai.github.io/PawBench/) for the full Model × Harness matrix and all slice views.

## Evaluation Workflows

PawBench is intended to be used as a diagnostic benchmark, not just a ranking table.

| Goal | Recommended setup | What to inspect |
| :--- | :--- | :--- |
| Choose a model | Fix one harness, run multiple models | Overall score, text/multimodal split, cost and trace quality |
| Choose a harness | Fix one model, run multiple harnesses | Harness gap, task errors, tool-use traces, workspace artifacts |
| Debug a harness | Rerun targeted slices after a change | Capability/source/scenario deltas, failed graders, transcripts |
| Add a dataset | Add tasks with the five-label taxonomy | Coverage balance, grader reliability, task detail page |
| Submit results | Aggregate run logs into `submissions/*.json` | Leaderboard row, slice payloads, task error count |

> **💡 Optimize Your Evaluation Logic with OpenJudge**
> To build your own evaluation system beyond the LLM × Harness vertical, you can leverage **[OpenJudge](https://github.com/agentscope-ai/OpenJudge)**'s 50+ production-ready graders (relevance, tool selection, trajectory, etc.) to evaluate and optimize your custom agents.

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

See `python run_bench.py --help` for all flags, including `--no-results-version-path`, `--save-workspace`, and `--save-docker-image`.

### View the Leaderboard

The website exposes the Model × Harness matrix, sortable leaderboard, slice analyzer, task library, and per-task pages.

```bash
cd site
npm install
npm run build:data    # aggregate raw run logs into submissions/ and JSON for the UI
npm run dev           # http://localhost:4321/PawBench/
```

For submission formats and site data generation details, see [site/README.md](site/README.md).

## PawBench Design

### Tasks

PawBench follows a **Reuse & Tag** methodology. Instead of writing every task from scratch, it pulls tasks from established agent benchmark suites, normalizes them into one format, and tags each task across five orthogonal dimensions.

| Dimension | Field | Values |
| :--- | :--- | :--- |
| Scenario | `scenario` | L1 categories such as `Office_Productivity`, `Software_Engineering`, `Safety_Alignment` |
| Capability | `capabilities` | `Logic_Reasoning`, `Math_Computation`, `Code_Manipulation`, `Tool_Use`, `Skill_Use`, `Planning`, `Self_Verification` |
| Complexity | `complexity` | `L1` (1-2 steps), `L2` (3-5 steps), `L3` (>5 steps with branches or backtracking) |
| Modality | `modality` | `text` or `multimodal` (`image`, `audio`, `video`) |
| Environment | `environment` | `closed` (offline, reproducible) or `open` (live internet / SaaS APIs) |

v1.0 contains **150 tasks** from `claweval`, `qwenclawbench`, `pinchbench`, PawBench self-built tasks, `skillsbench`, and `wildclawbench`.

| Source                                                           | # | Main coverage |
|:-----------------------------------------------------------------| ---: | :--- |
| `self-built`                                                     | 21 | Self-built tasks covering automation, information retrieval, and safety alignment |
| [`claweval`](https://github.com/claw-eval/claw-eval)             | 52 | Office productivity, data analytics, content creation |
| [`qwenclawbench`](https://github.com/SKYLENAGE-AI/QwenClawBench) | 29 | Automation, software engineering, safety alignment |
| [`pinchbench`](https://github.com/pinchbench/skill)              | 23 | Office workflows, software engineering, information retrieval |
| [`skillsbench`](https://github.com/benchflow-ai/skillsbench)     | 15 | Long-horizon skills, domain automation |
| [`wildclawbench`](https://github.com/InternLM/WildClawBench)     | 10 | Office workflows, safety alignment |

Each task page on the site shows its prompt, expected behavior, grading criteria, automated checker code, LLM judge rubric, workspace files, and metadata.

### Harnesses

| Harness | Link | Current role |
| :--- | :--- | :--- |
| QwenPaw | [agentscope-ai/QwenPaw](https://github.com/agentscope-ai/QwenPaw) | Default PawBench harness and primary baseline |
| OpenClaw | [openclaw/openclaw](https://github.com/openclaw/openclaw) | General-purpose open agent runtime |
| Hermes | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | Alternative community agent harness |

Harnesses are treated as first-class benchmark subjects. A harness contribution should preserve the same task prompt, workspace contract, timeout behavior, transcript format, and result schema so model and harness effects remain comparable.

### Grading

Each task declares one of three grading modes:

- `automated`: task-specific checks and assertions.
- `llm_judge`: LLM-as-judge for semantic outputs.
- `hybrid`: automated checks plus LLM judgment.

Runs can be sliced by source, scenario, capability, complexity, modality, environment, grading type, model, and harness. PawBench also stores transcripts and metrics for each task. With `--save-workspace` and `--save-docker-image`, it can preserve the agent workspace and final Docker image for deeper replay.

## Roadmap

- [ ] **Harness coverage:** add Claude Code, Cursor Agent, CoPaw, and more community scaffolds.
- [ ] **Dataset expansion:** add more open-environment, multimodal, skill-heavy, long-horizon, and real-world SaaS/API tasks.
- [ ] **Controlled studies:** turn the current findings into experiments around tool count, workspace awareness, skill discovery, web tools, and artifact-level completion checks.
- [ ] **Diagnostics:** improve trace replay, workspace diffs, failure attribution, and slice-level regression reports.
- [ ] **Evaluation reliability:** calibrate LLM judge prompts, strengthen automated graders, and document known failure modes.

## Contributing

We welcome contributions that make PawBench a better shared testbed for Model × Harness evaluation.

| Contribution | What to add |
| :--- | :--- |
| New harness | Agent adapter, Dockerfile if needed, environment setup, transcript capture, result normalization |
| New tasks | Task markdown, workspace assets, five-label taxonomy, automated checks and/or LLM judge rubric |
| New results | Raw run logs or `submissions/*.json` with overall and slice scores |
| Grader fixes | More deterministic checks, clearer rubrics, bug fixes for false positives/false negatives |
| Site improvements | Better leaderboard views, slice analysis, task explorer, trace replay, and documentation |

Good first contributions include adding missing task labels, improving task rubrics, reproducing a failed slice, integrating a new harness behind `--agents`, or submitting evaluation results for an untested model × harness pair.

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
