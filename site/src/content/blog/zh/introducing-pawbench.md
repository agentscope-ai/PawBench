---
title: "PawBench v1.0：给通用智能体一把可复现的尺"
description: ""
locale: zh
pubDate: 2026-06-04
author: PawBench team
tags: [release, design]
---

过去一年，通用智能体已经从演示场景走进了很多真实工作流。它可以帮人写代码、整理资料、操作网页、处理文件，也可以在更长的链路里调用工具、读写工作区、完成多步骤任务。

但问题也随之变得更具体：同一个模型，放进不同的智能体运行框架里，表现会不会差很多？一次任务失败，到底是模型没想明白，还是工具没给对、工作区没配置好、完成判定太宽松？如果只看最后的成功率，我们很难回答这些问题。

这正是 PawBench 想解决的事情。

PawBench 面向个人助理和通用智能体场景，评估的不只是底座模型，也包括承载模型运行的 Harness。这里的 Harness，可以理解为智能体的“运行框架”：它负责给模型装配 prompt、提供工具、管理工作区、隔离环境、注入技能，并决定什么时候继续执行、什么时候收尾。

换句话说，模型决定智能体的能力上限，Harness 决定这些能力能不能稳定落到真实任务里。PawBench 希望把这两件事放到同一张评测表里看清楚。

> [!NOTE]
> PawBench 是 [OpenJudge](https://github.com/agentscope-ai/OpenJudge) 生态的一部分。它沿用了 OpenJudge“评测驱动优化”的核心理念，并专注于评估 LLM × Harness 这一垂直维度的联合效果。

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536751985-fee338de-3de5-431b-9ec2-0b007b519ef6.png" width="612" title="" crop="0,0,1,1" id="ycidF" class="ne-image">

## PawBench v1.0 是怎么构建的
PawBench v1.0 不是单纯做一个模型排行榜，而是把“模型、Harness、任务”三者放在一起做交叉评测。

这次评测从 6 个高质量 Agent 评测集中抽取了 150 道任务，包括 `claweval`、`qwenclawbench`、`pinchbench`、`qwenpawbench`、`skillsbench` 和 `wildclawbench`。每道题都会按照 5 个维度打标：

+ 应用场景：例如办公协同、软件工程、自动化脚本、多模态内容生成。
+ 原子能力：例如工具调用、Skill 使用、规划、逻辑推理、自我校验。
+ 复杂度：L1 / L2 / L3，避免只靠简单题刷高分。
+ 输入模态：区分纯文本任务和图像、音频、视频等多模态任务。
+ 运行环境：区分离线沙箱任务和需要联网的 Web 搜索 / 网页获取任务。

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536751880-2e37e90e-2ddd-40a3-956b-f036bc42cc10.png" width="612" title="" crop="0,0,1,1" id="S9MKo" class="ne-image">

评测矩阵是 **9 个模型 × 3 个 Harness × 150 道任务**，一共 4,050 个测试单元。三家 Harness 分别是 Hermes、OpenClaw 和 QwenPaw。所有任务都在 Docker 沙箱中运行，执行轨迹、grader 产物和环境快照都会被保留下来，方便后续按切片复盘。

最终得分由两部分组成：一部分来自自动评分器，包括规则和子项断言；另一部分来自 LLM-as-judge，用于评估更偏语义的结果质量。本期评测采用混合权重计算最终分数，分数范围为 0 到 1。

| 来源任务簇 | 任务数 | 典型场景 |
| --- | ---: | --- |
| claweval | 52 | 办公协同、多模态内容生成 |
| qwenclawbench | 29 | 软件工程、自动化脚本、安全策略 |
| pinchbench | 23 | 工具调用、复杂执行链路 |
| qwenPawBench | 21 | Skill / 定时任务、GUI 操作、安全对齐 |
| [skillsbench](https://github.com/benchflow-ai/skillsbench) | 15 | 长程 Skill、领域自动化 |
| wildclawbench | 10 | 安全对齐、多步推理与协商 |
| **合计** | **150** |  |


## 先看榜单：Harness 的差距确实存在
PawBench 榜单支持 Overall、Text、Multimodal 三个切片。也就是说，同一组提交结果既可以看 150 道任务的总分，也可以只看 124 道纯文本任务，或只看 26 道多模态任务。

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536908719-62c41dc0-78c9-49e1-9e30-3661ba43b606.png" width="1281" title="" crop="0,0,1,1" id="ufce6887f" class="ne-image">

从本次结果看，有两个结论比较明显。

第一，Harness 之间有稳定差距。三家 Harness 的平均分分别是：QwenPaw 74.9，OpenClaw 72.9，Hermes 69.3。最高和最低之间相差 5.6 分，这个差距已经接近甚至超过不少模型版本升级带来的收益。

第二，中小模型更容易被 Harness 影响。以 `qwen3.6-35b-a3b` 为例，同一个模型换不同 Harness，最高和最低能差到 11.5 分。旗舰模型通常更稳，但参数规模较小的模型对工具设计、路径管理、prompt 装配和异常恢复会更敏感。

这也是 PawBench 的价值所在：它不只是告诉我们“哪个组合分数高”，还可以继续往下追问，“差距到底从哪里来”。

## 再做诊断：3 个切片发现
榜单能告诉我们差距存在，但说不清差距来自哪里。PawBench 的切片能力可以把 4,050 个测试单元按模型规模、模态、任务类型、技能领域等维度拆开，再对照执行轨迹，定位 Harness 行为上的差异。

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536751937-e837da61-dda1-4883-b083-d34e414e8e05.png" width="612" title="" crop="0,0,1,1" id="leOrS" class="ne-image">

### 发现一：中小模型对 Harness 更敏感
最典型的样本是 `qwen3.6-35b-a3b`。同一个模型只换 Harness，分数差距达到 11.5 分。

| 模型 | Hermes | OpenClaw | QwenPaw | Δ |
| --- | ---: | ---: | ---: | ---: |
| qwen3.6-35b-a3b | 56.7 | 68.3 | 68.3 | 11.5 |


对执行轨迹做复盘后，我们看到几个可能原因。

首先，三个 Harness 都还缺少足够严格的“产物级完成校验”。不少任务不是模型说“我完成了”就真的完成了，它可能没有写出文件、没有把 diff 落盘、没有跑测试，也可能把结果写到了错误目录。现有 Harness 往往更依赖模型的自我声明，缺少对 workspace 产物的硬校验。小模型更容易过早宣布完成，因此在这类任务里掉分更明显。

其次，Hermes 在路径感知和路径校验上更宽松。它没有在 prompt 中明确告诉模型当前工作目录，也没有在 `write_file` 这类工具层面强约束写入路径。结果是：模型以为自己写对了，评测程序去标准工作区扫描时却找不到产物。

| Harness | 是否告诉模型 cwd / workspace | 写文件是否校验路径 |
| --- | --- | --- |
| Hermes | 缺少明确注入 | 缺少统一校验 |
| OpenClaw | 显式注入 `workspaceDir` | 文件工具与 prompt 使用同一基准 |
| QwenPaw | 运行时注入 workspace 路径 | 文件工具使用统一 `workspace_dir` |


再次，工具表体量也会影响小模型。Hermes 默认装载约 65 个工具 schema，OpenClaw 约 30 个，QwenPaw 约 15 个。工具越多不一定越好；对小模型来说，过大的工具表会挤占上下文，也会增加首轮决策负担。这个原因目前仍需要进一步实验验证，但从异常轨迹上看，它值得被单独关注。

### 发现二：Skill 调用是能力维度里的明显短板
如果按“能力维度”切开看，Skill 调用是这次评测里比较明显的短板。也就是说，它不是和所有任务混在一起看一个总分，而是和工具调用、规划、逻辑推理、自我校验等其他能力切片相比，表现更吃力。

在 17 道 Skill 相关任务上，三家 Harness 的得分都不高。

| Harness | Skill 任务均分 | 观察 |
| --- | ---: | --- |
| OpenClaw | 52.5 | 三家中相对最好，但仍是短板 |
| QwenPaw | 44.5 | 工作区 Skill 自动发现不足 |
| Hermes | 44.6 | 容易漏掉 workspace 内 Skill |


这里的问题不是“有没有 Skill”这么简单，而是 Harness 能不能动态发现、正确注入，并把 Skill 里的操作能力变成模型可用的工具。

很多任务会在当前工作区放一个 `SKILL.md`，里面写清楚专家流程或专用脚本。如果 Harness 只扫描全局预装 Skill，而不扫描当前工作区，就会漏掉这份关键指南。模型看不到它，自然只能自己摸索。

| Harness | 是否自动发现工作区 `SKILL.md` |
| --- | --- |
| Hermes | 扫描 `~/.hermes/skills/`，容易漏掉 workspace 内 Skill |
| OpenClaw | 扫描 workspace，并渲染到 `<available_skills>` |
| QwenPaw | 可通过 `skill.json` 启用并追加 prompt，但缺少 workspace 自动扫描 |


另一个问题来自模型本身。即使 Skill 成功注入，长链路推理和精细计算仍然会出错。也就是说，Harness 可以把路标放清楚，但模型能不能沿着路标完成复杂推理，仍然取决于模型能力。

### 发现三：Web 搜索任务很依赖默认可用性
这里说的 Web 搜索任务，主要是需要模型查网页、抓取内容、做深度调研的任务。它测的不是“把所有搜索服务 key 都配好之后的理论上限”，而是更接近开发者第一次 clone 后的默认体验：拉固定版本源码，写入 LLM 密钥，然后直接跑。

在这类任务里，Hermes 表现偏低，核心原因是 web 搜索和网页抓取工具在零配置下没有进入可用工具表。Hermes 源码里有 `web_search` / `web_extract`，但需要额外配置搜索服务 key 才会启用。本次评测只配置 LLM 密钥，所以模型实际拿不到这两个工具，只能尝试用 browser 类工具硬做。

相比之下，OpenClaw 在零配置下提供了更直接的 web 能力：`web_search` 可以走 DuckDuckGo 等 keyless provider，`web_fetch` 依赖内置 HTTP 抓取，不需要额外 key；QwenPaw虽然也没有可用的search工具，但提供了`browser_use`工具，这个工具能够依赖模型知识储备通过URL提供一定的web能力。

| Harness | 本批实际 web 工具 | 零配置体验 |
| --- | --- | --- |
| Hermes | 无 `web_search` / `web_extract`，仅有 browser 类工具 | 需要额外配置 key |
| OpenClaw | `web_search` + `web_fetch` | 开箱即用 |
| QwenPaw | browser 类合并工具 | 无专用 search |


这说明，Web 搜索任务的评测结果不只反映模型搜索和阅读能力，也反映 Harness 是否把关键工具做成了默认可用。

## 最后看优化：4 条 Harness 设计原则
把上面的切片结果合起来看，PawBench 给 Harness 设计提供了 4 条比较直接的原则。

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536751934-74460985-061d-492a-8715-8b830b675c2e.png" width="612" title="" crop="0,0,1,1" id="Ab2k3" class="ne-image">

### 1. Inform Fully：充分告知
模型看不见的东西，对它来说就不存在。

Harness 应该明确告诉模型当前运行环境：cwd 在哪、workspace 在哪、输出目录在哪、工作区里有没有 `SKILL.md`，以及有哪些可用资源。不要假设模型会自己猜到这些上下文。

### 2. Equip on Demand：按需装备
工具要装得对，也要装得精。

“装得对”指关键工具应该在默认配置下可用，例如 keyless web search、内置 HTTP fetch、Skill helper 自动注册。“装得精”指工具数量要匹配目标模型的上下文和注意力预算。工具不是越多越好，过多的 schema 反而可能压垮小模型。

### 3. Monitor Actively：主动监控
不要只听模型说了什么，要看它做了什么。

Harness 应该检查任务产物是否真的落地：文件是否存在、是否非空、是否包含必填字段、工具调用是否合法、exit code 是否正常。尤其在文件写入、代码修改、报告生成这类任务中，产物级校验比一句“我完成了”可靠得多。

### 4. Recover Gracefully：弹性恢复
一次异常不一定代表任务失败。

当 Harness 发现模型空响应、只画计划、工具调用异常或产物缺失时，可以给它一次更有信息量的续推机会，例如注入当前状态、说明缺少什么产物、保留中间结果，并设置合理的 retry budget。很多任务不是不会做，而是在关键节点缺少一次及时纠偏。

| 原则 | 发生位置 | 一句话定义 |
| --- | --- | --- |
| Inform Fully | prompt 装配层 | 把运行环境和可用资源讲清楚 |
| Equip on Demand | 工具表注入层 | 关键工具默认可用，工具数量不过载 |
| Monitor Actively | 执行与完成判断层 | 用产物和状态判断任务是否真的完成 |
| Recover Gracefully | 异常应对层 | 异常后给模型带状态的重试机会 |


## PawBench 能帮谁
如果你是智能体用户，PawBench 可以帮你选择更合适的模型和 Harness 组合。比如，面对纯文本任务、多模态任务、Skill 任务或 Web 搜索任务，不同组合的表现并不一样。

如果你是 Harness 开发者，PawBench 不只是一个榜单。它提供了 4,050 个 cell 的对照矩阵和切片分析能力，可以帮助你做三件事：

1. 横向自检：把你的 Harness 跑到同一组模型上，看在哪些任务类型上落后。
2. 失败画像：锁定可疑切片，对比 trace，找到具体的 Harness 行为问题。
3. 回归验证：每次修复后重新切片，看分数变化是否真的对应到问题上。

这类评测对通用智能体尤其重要。因为真实用户不会只问模型一个问题就结束，他们会让智能体操作文件、调用工具、跑脚本、读网页、跨步骤完成任务。PawBench 希望把这些复杂链路拆开，让模型能力和 Harness 能力都能被看见、被诊断、被持续改进。

> **💡 基于 OpenJudge 优化自己的评测逻辑**
> 如果你需要针对自己的定制化 Agent 搭建评测系统，可以借助 **[OpenJudge](https://github.com/agentscope-ai/OpenJudge)** 提供的 50+ 生产级 Grader（语义相关性、工具调用、运行轨迹等）快速优化业务评测逻辑。

## 欢迎贡献
PawBench v1.0 已开源。我们欢迎社区一起参与：

+ 接入新的 Harness，例如 CoPaw、Cursor Agent 或其他社区框架。
+ 提交新的模型评测结果。
+ 贡献更多任务，并按照五维标签体系完成标注。
+ 基于 slice 能力分析你关心的任务切片，反馈诊断报告。

PawBench 站在开源 Agent 评测社区的肩膀上，包括 [Claw-Eval](https://github.com/claw-eval/claw-eval)、[QwenClawBench](https://github.com/SKYLENAGE-AI/QwenClawBench)、[WildClawBench](https://github.com/InternLM/WildClawBench)、[PinchBench](https://github.com/pinchbench/skill) 和 [skillsbench](https://github.com/benchflow-ai/skillsbench)。
