<h1 align="center">🐾 PawBench</h1>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

<p align="center">
  <a href="#任务构建">
    <img alt="tasks" src="https://img.shields.io/badge/tasks-150-2ea44f">
  </a>
  <a href="https://agentscope-ai.github.io/PawBench/">
    <img alt="models" src="https://img.shields.io/badge/models-9-0969da">
  </a>
  <a href="#harness">
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
  <strong>面向通用智能体的 Model × Harness 交叉评测基准。</strong><br>
  150 道智能体任务 · 9 款模型 · 3 个 Harness · 任务切片 · 诊断轨迹
</p>

---

同一个模型，放进不同的智能体运行框架里，实际表现可能会明显变化。一次任务失败，到底是模型没想明白，还是工具没给对、Skill 没发现、工作区没读懂、Web 能力不稳定，或者完成判定太宽松？只看最终成功率，很难回答这些问题。

PawBench 围绕一个核心判断构建：

$$\text{Agent 表现} = f(\text{Model}, \text{Harness})$$

> [!NOTE]
> PawBench 是 [OpenJudge](https://github.com/agentscope-ai/OpenJudge) 生态的一部分。它沿用了 OpenJudge“评测驱动优化”的核心理念，并专注于评估 LLM × Harness 这一垂直维度的联合效果。

它同时评估 **模型本身** 和 **承载模型运行的 Harness（智能体运行框架）**，并保留足够的元数据，让两条维度都能被独立分析。v1.0 覆盖 **9 个模型 × 3 个 Harness × 150 道任务**，公开 prompt、grader、任务标签、榜单提交和切片分析。

![PawBench 概览与标签体系](site/public/pawbench-overview-taxonomy.png)

通过 PawBench，你可以：

- **选择模型 & Harness：** 为纯文本、多模态、Skill-heavy、Web 搜索等任务选择合适的模型 × Harness 组合。
- **瓶颈定位：** 判断失败来自模型、Harness、工具、工作区、评分器，还是任务设计。
- **闭环迭代：** 修改 Harness 后重跑同一批任务切片，确认目标能力是否真的提升。
- **社区共建：** 贡献新的 Harness、任务、grader、评测结果和 bugfix。

## 核心洞见

PawBench v1.0 的首批评测说明：Harness 不是无关紧要的工程包装。它会改变同一个模型最终释放出来的能力，而且差距已经接近不少模型版本升级带来的收益。

除非特别说明，以下数字均来自：这次运行：**150 道 PawBench v1.0 任务**、**9 个模型**、**3 个 Harness**（`qwenpaw`、`openclaw`、`hermes`），使用 **claude opus 4.6 as judge** 评测设置。分数均按 overall percentage 展示。

![Harness 差距分析](site/public/pawbench-harness-gap.png)

- **固定同一个模型时，Harness 差距依然明显。** 在同一个 `qwen3.6-35b-a3b` 模型和同一组 150 道任务上，QwenPaw 得分 **68.3**，OpenClaw **68.2**，Hermes **56.7**，最高和最低相差 **11.5 分**。这不是某一个模型的偶然现象， `qwen3.6-max-preview` 的 Harness 极差达到 **10.3 分**，`glm-5.1` 达到 **9.9 分**；9 个被测模型里，有 6 个模型在不同 Harness 下的分差超过 3 分。
- **不同Harness的平均表现。** 在本次运行的 27 个 model × harness submission 上做宏平均，QwenPaw 得分 **74.9**，OpenClaw **72.9**，Hermes **69.3**。总榜只是第一层视角， 真正能指导工程迭代的是切片分析：哪个 Harness 在什么能力、任务来源、场景、模态上更脆弱。

![切片诊断](site/public/pawbench-slice-diagnostics.png)

以下切片数字是同一批 27 个 model × harness submission 的宏平均，揭示了几个高价值改进方向：

- **Skill-heavy 任务最难。** `Skill_Use` 平均 **47.2**，`skillsbench` 来源任务平均 **40.9**，说明 Skill 发现、加载和按流程执行仍然脆弱。
- **多模态明显难于纯文本。** 纯文本任务平均 **74.1**，多模态任务平均 **64.0**。
- **开放环境会引入真实摩擦。** closed、可复现任务平均 **72.9**，open 环境任务平均 **68.9**。
- **部分场景的 Harness 差距远大于总榜差距。** Finance、Information Retrieval、Manufacturing Quality Control、Software Engineering 等切片很适合用来定位工具、Skill、Workspace 和搜索能力问题。

完整矩阵和切片分析见 [live leaderboard](https://agentscope-ai.github.io/PawBench/)。


## 如何使用 PawBench 评测

PawBench 不只是排行榜，更适合作为诊断型 Benchmark 使用。

| 目标 | 推荐设置 | 重点观察 |
| :--- | :--- | :--- |
| 选择模型 | 固定一个 Harness，横向跑多个模型 | 总分、text/multimodal 分裂、成本和 trace 质量 |
| 选择 Harness | 固定一个模型，横向跑多个 Harness | Harness gap、任务错误、工具调用 trace、workspace 产物 |
| 调试 Harness | 修复后重跑目标任务切片 | capability/source/scenario 差值、失败 grader、transcript |
| 扩展数据集 | 按五维标签体系新增任务 | 覆盖分布、grader 可靠性、任务详情页可读性 |
| 提交结果 | 汇总 raw logs 到 `submissions/*.json` | 榜单行、切片字段、任务错误数量 |

> **💡 基于 OpenJudge 优化自己的评测逻辑**
> 如果你需要针对自己的定制化 Agent 搭建评测系统，可以借助 **[OpenJudge](https://github.com/agentscope-ai/OpenJudge)** 提供的 50+ 生产级 Grader（语义相关性、工具调用、运行轨迹等）快速优化业务评测逻辑。

## 快速开始

### 环境要求

需要 Python 3.11+ 和 Docker。Node.js 20+ 只在本地启动排行榜站点时需要。

安装依赖，并写入凭证。默认配置推荐使用 DashScope：

```bash
pip install -r requirements.txt

cat > .env <<'EOF'
DASHSCOPE_API_KEY=...
JUDGE_API_KEY=...
JUDGE_BASE_URL=...
EOF
```

如果使用 OpenAI-compatible 或自定义 provider，再按需配置 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 或 `CUSTOM_API_KEY` / `CUSTOM_BASE_URL`。

### 运行评测

所有 Agent harness 都通过 **Harbor Bridge** 在同一个基础镜像中运行。每个 Agent（QwenPaw、OpenClaw、Hermes、Claude Code、Codex、Aider…）都是一个 [Harbor](https://github.com/av/harbor) 兼容的 `BaseInstalledAgent`，在 `pawbench-base:latest` 镜像中执行。

首次运行前，先构建基础镜像（已包含 `harbor-framework` 和所有系统依赖）：

```bash
docker build -f docker/Dockerfile.pawbench-base -t pawbench-base:latest .
```

> **说明 — harbor-v2 任务也可能依赖这个镜像。** 部分 `data_v2.1`/`data_v2.2`
> 任务的 `environment/Dockerfile` 会 `FROM pawbench-base:latest`（而不是内网
> registry 镜像），这样任务容器里天然带有全部 Harbor agent CLI（包括
> `claude`），Harbor 的 `install()` 探测到已存在就会跳过重新安装，OpenJudge
> 的 `claude-code` 判官 harness 也不会因为被测 agent 不是 claude-code 而缺失
> CLI。**`pawbench-base:latest` 目前只在本机构建、没有推送到任何镜像
> registry**：如果在新机器/CI 上看到这类任务报 "pull access denied" 或
> "image not found"，说明还没构建过这个基础镜像，先执行上面的
> `docker build` 命令即可。

> **说明 — Harbor 直接从我们的 fork 安装，无需本地克隆。** Harbor 没有发布到
> PyPI，且 PawBench 需要在上游基础上加一些 agent 侧的修复/能力（DashScope
> provider 路由、多智能体 hooks 等）。Docker 构建直接通过 pip 的 git 支持，从
> [`XiaoBoAI/harbor@pawbench-agent-patches`](https://github.com/XiaoBoAI/harbor/tree/pawbench-agent-patches)
> 的一个固定 commit 安装——不需要克隆或 COPY 任何本地目录。如果你想在宿主机
> （例如 conda 环境）里直接运行 agent，用同样的方式安装即可：
>
> ```bash
> pip install "harbor @ git+https://github.com/XiaoBoAI/harbor.git@c82aa5158f2a92a0af5469588cf94aa1f1d8fd43"
> ```

```bash
# Smoke test：用默认 Agent（harbor:qwenpaw）跑一个 PawBench v1.0 任务
python run_bench.py --tasks T053 --model dashscope/qwen3.6-plus

# 切换 Harness（harbor: 前缀可省略，qwenpaw == harbor:qwenpaw）
python run_bench.py --agents harbor:openclaw --tasks T053 --model dashscope/qwen3.6-plus

# 在指定任务集上横向对比多个 Harness
python run_bench.py \
  --agents harbor:qwenpaw harbor:openclaw harbor:hermes \
  --model dashscope/qwen3.6-plus \
  --tasks T002 T006

# 顺序评测多个模型
python run_bench.py \
  --model dashscope/qwen3.6-plus \
  --model anthropic/claude-sonnet-4-6
```

其他参数（`--no-results-version-path`、`--save-workspace`、`--save-docker-image` 等）见 `python run_bench.py --help`。

### Native / Single / Forced / Adaptive Agent 模式

PawBench 支持四种执行模式：

- `native`：不注入 PawBench 的 Multi-Agent 配置，完整沿用 harness 自身默认
  配置（不传该参数时的默认行为）。
- `single`：显式禁用 harness 的原生子 Agent 委派能力。
- `adaptive`：开放子 Agent 工具，由主 Agent 自行决定是否委派。
- `forced`：要求至少进行一次真实子 Agent 委派；如果轨迹中没有检测到
  Claude Code `Task`、Codex `spawn_agent`、OpenClaw `sessions_spawn` 或 QwenPaw
  `spawn_subagent` 调用，该任务会以 `score=0`、`passed=false` 记录。

```bash
# Harness 原生默认配置（省略该参数时也是此模式）
python run_bench.py --agents harbor:openclaw --multi-agent-mode native ...

# 严格单 Agent
python run_bench.py --agents harbor:openclaw --multi-agent-mode single ...

# 自适应 Multi-Agent
python run_bench.py --agents harbor:openclaw --multi-agent-mode adaptive ...

# 强制 Multi-Agent
python run_bench.py --agents harbor:codex --multi-agent-mode forced ...
```

目前原生支持 Multi-Agent 的 harness 是 `claude-code`、`codex`、`openclaw`
和 QwenPaw 2.0.0.post3+（`qwenpaw`）。对 `hermes` 等其他 harness 请求
`forced` 或 `adaptive` 时，框架会明确警告并降级为 `single`。结果 JSON 的
`run_config.multi_agent` 和每条结果的 `multi_agent` 字段会分别记录请求/实际
模式、委派次数和 forced 违规状态。

旧参数值继续兼容：`disabled→single`、`auto/subagents→adaptive`、
`teams/delegation/proactive→forced`。单独使用旧的 `--multi-agent` 等价于
`--multi-agent-mode adaptive`。

#### 使用 Harbor Bridge 评测 Claude Code、Codex 等其他 Agent

同样的 Harbor Bridge 可让你在相同的 150 道 PawBench 任务上评测 Claude Code、OpenAI Codex CLI、Aider 等主流编程 Agent —— 无需额外构建镜像。

**第一步：在 `.env` 中配置 API Key**（可从 `.env.example` 复制）：

```bash
# Claude Code 所需
ANTHROPIC_API_KEY=sk-ant-...

# Codex CLI 所需
OPENAI_API_KEY=sk-...
```

**第二步：在 `--agents` 中使用 `harbor:` 前缀**：

```bash
# 评测 Claude Code（支持任意 Anthropic 模型）
python run_bench.py \
  --agents harbor:claude-code \
  --model anthropic/claude-opus-4-5 \
  --tasks T053

# 评测 OpenAI Codex CLI
python run_bench.py \
  --agents harbor:codex \
  --model openai/codex-mini \
  --tasks T053

# Claude Code vs Codex vs QwenPaw 三方横向对比
python run_bench.py \
  --agents harbor:claude-code harbor:codex harbor:qwenpaw \
  --model anthropic/claude-opus-4-5 \
  --tasks T002 T006 T053
```

**支持的 Harbor Agent 列表**（`harbor:<名称>` 格式均可使用）：

| 名称 | Agent | 提供方 |
| :--- | :--- | :--- |
| `harbor:qwenpaw` | QwenPaw（默认） | 阿里巴巴 |
| `harbor:openclaw` | OpenClaw | — |
| `harbor:hermes` | Hermes | — |
| `harbor:claude-code` | Claude Code CLI | Anthropic |
| `harbor:codex` | Codex CLI | OpenAI |
| `harbor:aider` | Aider | Paul Gauthier |
| `harbor:gemini-cli` | Gemini CLI | Google |
| `harbor:qwen-code` | Qwen Code CLI | 阿里巴巴 |
| `harbor:goose` | Goose | Block |
| `harbor:opencode` | OpenCode | — |
| `harbor:openhands` | OpenHands | All-Hands-AI |
| `harbor:swe-agent` | SWE-agent | Princeton NLP |
| `harbor:cursor-cli` | Cursor CLI | Anysphere |
| `harbor:kimi-cli` | Kimi CLI | Moonshot AI |
| `harbor:copilot-cli` | GitHub Copilot CLI | GitHub |

### 查看排行榜

站点包含 Model × Harness 矩阵、可排序榜单、切片分析器、任务库和单任务详情页。

```bash
cd site
npm install
npm run build:data    # 汇总原始日志到 submissions/ 并生成前端 JSON
npm run dev           # http://localhost:4321/PawBench/
```

提交格式和站点数据生成方式见 [site/README.md](site/README.md)。

## PawBench Design

### 任务构建

PawBench 采用 **Reuse & Tag** 方法。它不是从零手写所有任务，而是从已有 Agent 评测集中抽取任务，统一成同一种格式，并按五个正交维度打标。

| 维度 | 字段 | 标签值 |
| :--- | :--- | :--- |
| 场景 | `scenario` | `Office_Productivity`、`Software_Engineering`、`Safety_Alignment` 等一级分类 |
| 能力 | `capabilities` | `Logic_Reasoning`、`Math_Computation`、`Code_Manipulation`、`Tool_Use`、`Skill_Use`、`Planning`、`Self_Verification` |
| 复杂度 | `complexity` | `L1`（1-2 步）、`L2`（3-5 步）、`L3`（超过 5 步，含分支或回溯） |
| 模态 | `modality` | `text` 或 `multimodal`（`image`、`audio`、`video`） |
| 环境 | `environment` | `closed`（离线、可复现）或 `open`（需要联网或真实 SaaS API） |

v1.0 包含 **150 道任务**，来源包括 `claweval`、`qwenclawbench`、`pinchbench`、PawBench 自建任务、`skillsbench` 和 `wildclawbench`。

| 来源                                                               | 数量 | 主要覆盖 |
|:-----------------------------------------------------------------| ---: | :--- |
| `self-built`                                                     | 21 | 自建任务，覆盖自动化、信息检索、安全对齐 |
| [`claweval`](https://github.com/claw-eval/claw-eval)             | 52 | 办公协同、数据分析、内容创作 |
| [`qwenclawbench`](https://github.com/SKYLENAGE-AI/QwenClawBench) | 29 | 自动化、软件工程、安全对齐 |
| [`pinchbench`](https://github.com/pinchbench/skill)              | 23 | 办公流程、软件工程、信息检索 |
| [`skillsbench`](https://github.com/benchflow-ai/skillsbench)     | 15 | 长程 Skill、领域自动化 |
| [`wildclawbench`](https://github.com/InternLM/WildClawBench)     | 10 | 办公流程、安全对齐 |

站点中的每个任务详情页都会展示 prompt、预期行为、评分标准、自动化检查代码、LLM judge rubric、workspace 文件和完整元数据。

### Harness

PawBench 支持两类 Harness：**内置 Harness**（随 PawBench 发布）和 **Harbor Bridge Agent**（接入任意 [Harbor](https://github.com/av/harbor) 兼容的编程 Agent）。

**内置 Harness**

| Harness | 链接 | 当前角色 |
| :--- | :--- | :--- |
| QwenPaw | [agentscope-ai/QwenPaw](https://github.com/agentscope-ai/QwenPaw) | 默认 PawBench Harness 和主要 baseline |
| OpenClaw | [openclaw/openclaw](https://github.com/openclaw/openclaw) | 通用开源 Agent runtime |
| Hermes | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | 社区 Agent Harness 对照组 |

**Harbor Bridge Agent**（使用 `--agents harbor:<名称>`）

Harbor Bridge 将 PawBench 与 [Harbor](https://github.com/av/harbor) 生态连接，把 Harbor 的 `exec()` / `upload_file()` 接口转换为对 PawBench 容器的 `docker exec` / `docker cp` 调用，让任何 Harbor 兼容 Agent 无需修改即可参与评测。使用前需先构建 `docker/Dockerfile.pawbench-base`。

| 名称 | Agent | 提供方 |
| :--- | :--- | :--- |
| `harbor:claude-code` | Claude Code CLI | Anthropic |
| `harbor:codex` | Codex CLI | OpenAI |
| `harbor:aider` | Aider | Paul Gauthier |
| `harbor:gemini-cli` | Gemini CLI | Google |
| `harbor:qwen-code` | Qwen Code CLI | 阿里巴巴 |
| `harbor:goose` | Goose | Block |
| `harbor:opencode` | OpenCode | — |
| `harbor:openhands` | OpenHands | All-Hands-AI |
| `harbor:swe-agent` | SWE-agent | Princeton NLP |
| `harbor:cursor-cli` | Cursor CLI | Anysphere |
| `harbor:kimi-cli` | Kimi CLI | Moonshot AI |
| `harbor:copilot-cli` | GitHub Copilot CLI | GitHub |

在 PawBench 中，Harness 是一等评测对象。贡献新的 Harness 时，应尽量保持相同的任务 prompt、workspace contract、timeout 行为、transcript 格式和结果 schema，这样模型因素和 Harness 因素才能保持可比。

### 评测方案

每道任务会声明三种评分模式之一：

- `automated`：任务内置检查和断言。
- `llm_judge`：用 LLM-as-judge 评估偏语义的输出。
- `hybrid`：自动检查和 LLM 判断混合评分。

评测结果可以按 source、scenario、capability、complexity、modality、environment、grading type、model 和 harness 切片。PawBench 也会保存每道任务的 transcript 和 metrics。开启 `--save-workspace` 和 `--save-docker-image` 后，还可以保留 agent workspace 和最终 Docker 镜像，方便更深入地复盘。

## Roadmap

- [x] **Harness 覆盖：** 通过 Harbor Bridge 已支持 Claude Code、Codex CLI、Aider、Gemini CLI、Cursor CLI 等 10+ Agent（`--agents harbor:<名称>`）。
- [ ] **Harness 覆盖：** 继续接入 CoPaw 等更多社区脚手架。
- [ ] **数据集扩展：** 引入更多 open-environment、multimodal、skill-heavy、long-horizon 和真实 SaaS/API 任务。
- [ ] **可控实验：** 围绕工具数量、workspace 感知、Skill 发现、Web 工具和产物级完成校验展开实验。
- [ ] **诊断能力：** 改进 trace replay、workspace diff、失败归因和 slice-level regression report。
- [ ] **评测可靠性：** 校准 LLM judge prompt，强化自动化 grader，记录已知失败模式。

## Contributing

我们欢迎社区一起把 PawBench 做成更好的 Model × Harness 评测共同体。

| 贡献方向 | 需要补充的内容 |
| :--- | :--- |
| 新 Harness | Agent adapter、必要的 Dockerfile、环境配置、transcript 捕获、结果归一化 |
| 新任务 | 任务 markdown、workspace assets、五维标签、自动化检查和/或 LLM judge rubric |
| 新评测结果 | raw run logs 或包含总分和切片分数的 `submissions/*.json` |
| Grader 修复 | 更确定性的检查、更清晰的 rubric、false positive/false negative 修复 |
| 站点改进 | 更好的榜单、切片分析、任务浏览、trace replay 和文档 |

适合优先上手的贡献包括：补齐任务标签、改进任务 rubric、复现某个失败切片、把新的 Harness 接到 `--agents` 后面，或者提交一个尚未覆盖的 model × harness 评测结果。

## 引用

如果你在研究或项目中使用到了 PawBench，请按照如下格式引用：

```bibtex
@misc{pawbench,
  title  = {PawBench: A benchmark for evaluating LLM × harness performance},
  author = {The OpenJudge Team},
  url    = {https://github.com/agentscope-ai/PawBench},
  month  = {06},
  year   = {2026}
}
```

## 致谢

PawBench 站在开源 Agent 评测社区的肩膀上，包括 [Claw-Eval](https://github.com/claw-eval/claw-eval)、[QwenClawBench](https://github.com/SKYLENAGE-AI/QwenClawBench)、[WildClawBench](https://github.com/InternLM/WildClawBench)、[PinchBench](https://github.com/pinchbench/skill)、[skillsbench](https://github.com/benchflow-ai/skillsbench) 等。
