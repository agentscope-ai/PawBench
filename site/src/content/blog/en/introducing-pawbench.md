---
title: "PawBench v1.0: A reproducible yardstick for general-purpose agents"
description: ""
locale: en
pubDate: 2026-06-04
author: PawBench team
tags: [release, design]
---

Over the past year, general-purpose agents have moved out of demos and into real workflows. They write code, organize research, drive browsers, and handle files. On longer tasks they keep calling tools, reading and writing the workspace, and chaining steps together until something gets done.

That progress made the open questions sharper. Take the same model, drop it into a different agent runtime, and the numbers can shift a lot. When a task fails, was it the model's reasoning, a missing tool, a misconfigured workspace, or a completion check that was too generous? Looking only at the final pass rate, you cannot answer any of that.

PawBench is our attempt to fix this.

PawBench targets personal-assistant and general-agent scenarios and evaluates two layers at once: the underlying model and the harness that runs it. We use "harness" to mean the agent runtime that fits prompts around the model, exposes tools, manages the workspace, sandboxes execution, injects skills, and decides when the model should keep going versus wrap up.

Put differently, the model sets the ceiling on what an agent can do; the harness decides whether that ceiling reaches actual tasks. PawBench puts both on the same scorecard.

> [!NOTE]
> PawBench is part of the [OpenJudge](https://github.com/agentscope-ai/OpenJudge) ecosystem. It shares OpenJudge's philosophy of evaluation-driven optimization, but focuses specifically on the interaction between LLMs and agent harnesses.

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536751985-fee338de-3de5-431b-9ec2-0b007b519ef6.png" width="612" title="" crop="0,0,1,1" id="ycidF" class="ne-image">

## How PawBench v1.0 is built

PawBench v1.0 is not just another model ranking. It evaluates models, harnesses, and tasks together as a cross product.

We pulled 150 tasks from 6 high-quality agent suites: `claweval`, `qwenclawbench`, `pinchbench`, `qwenpawbench`, `skillsbench`, and `wildclawbench`. Every task carries 5 labels:

+ Scenario: things like office collaboration, software engineering, automation scripting, multimodal content generation.
+ Atomic capability: tool use, skill use, planning, reasoning, self-verification, and so on.
+ Complexity: L1 / L2 / L3, so easy tasks alone cannot inflate a score.
+ Input modality: text-only versus image, audio, video, or other multimodal inputs.
+ Environment: offline sandbox tasks versus tasks that need live web search or page fetching.

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536751880-2e37e90e-2ddd-40a3-956b-f036bc42cc10.png" width="612" title="" crop="0,0,1,1" id="S9MKo" class="ne-image">

The matrix is **9 models × 3 harnesses × 150 tasks**, which gives 4,050 cells. The three harnesses are Hermes, OpenClaw, and QwenPaw. All tasks run inside Docker sandboxes, and we keep the execution trace, grader artifacts, and environment snapshots so we can replay any slice later.

Final scores have two parts: an automated grader (rules and per-item assertions) and an LLM-as-judge component for outputs where the right answer is more about meaning than syntax. This round mixes them with a hybrid weight, scored from 0 to 1.

| Source suite | Tasks | Representative scenarios |
| --- | ---: | --- |
| claweval | 52 | Office collaboration, multimodal content |
| qwenclawbench | 29 | Software engineering, automation, safety policy |
| pinchbench | 23 | Tool use, complex execution chains |
| qwenpawbench | 21 | Skill / scheduled tasks, GUI operation, safety alignment |
| [skillsbench](https://github.com/benchflow-ai/skillsbench) | 15 | Long-horizon skills, domain automation |
| wildclawbench | 10 | Safety alignment, multi-step reasoning and negotiation |
| **Total** | **150** |  |


## The leaderboard: harness gaps are real

The leaderboard offers three slices: Overall, Text, and Multimodal. The same set of submissions can be read as a 150-task total, the 124 text-only tasks, or the 26 multimodal tasks.

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536908719-62c41dc0-78c9-49e1-9e30-3661ba43b606.png" width="1281" title="" crop="0,0,1,1" id="ufce6887f" class="ne-image">

Two things stand out from this round.

First, the harness gap is real. Average scores: QwenPaw 74.9, OpenClaw 72.9, Hermes 69.3. The 5.6-point spread between top and bottom is on par with what a model version upgrade typically buys you.

Second, mid-sized models are more harness-sensitive. Look at `qwen3.6-35b-a3b`: same model, three harnesses, an 11.5-point gap from worst to best. Flagship models are usually steadier; smaller models react more strongly to tool design, path handling, prompt assembly, and recovery from errors.

This is what PawBench is for. The leaderboard tells you which combination scores higher, and then the slicing lets you keep asking: where is the gap coming from?

## Diagnostics: 3 findings from slicing the data

A leaderboard can tell you the gap exists but not where it comes from. PawBench's slicing lets you split the 4,050 cells by model size, modality, task type, capability area, and so on, then cross-reference traces to pin down what the harness actually did differently.

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536751937-e837da61-dda1-4883-b083-d34e414e8e05.png" width="612" title="" crop="0,0,1,1" id="leOrS" class="ne-image">

### Finding 1: Smaller models are more sensitive to the harness

The clearest example is `qwen3.6-35b-a3b`. Switching only the harness moves the score by 11.5 points.

| Model | Hermes | OpenClaw | QwenPaw | Δ |
| --- | ---: | ---: | ---: | ---: |
| qwen3.6-35b-a3b | 56.7 | 68.3 | 68.3 | 11.5 |


Replaying traces surfaces a few likely reasons.

First, none of the three harnesses have strict enough artifact-level completion checks. Plenty of tasks are not done just because the model says they are. The model may not have written the file, may not have saved the diff, may not have run the tests, or may have written to the wrong directory. Today's harnesses lean on the model's self-report and skip the hard check on workspace artifacts. Smaller models declare completion early more often, so they lose more points on these.

Second, Hermes is looser about path awareness. It does not tell the model the current working directory in the prompt, and it does not enforce write paths in tools like `write_file`. The result: the model thinks it wrote the right file, then the grader scans the standard workspace and finds nothing.

| Harness | Tells model cwd / workspace? | Validates write paths? |
| --- | --- | --- |
| Hermes | Not explicitly injected | No unified check |
| OpenClaw | Explicit `workspaceDir` | File tools share the same baseline as the prompt |
| QwenPaw | Workspace path injected at runtime | File tools use a unified `workspace_dir` |


Third, tool count seems to matter for smaller models. Hermes ships with roughly 65 tool schemas by default, OpenClaw around 30, QwenPaw around 15. More is not always better. For a smaller model, an oversized tool table eats context and complicates the first-round decision. We still need experiments to confirm this, but the bad traces are consistent enough to flag it.

### Finding 2: Skill use is the clearest weak spot across capabilities

If you slice by capability, skill use is the area where everyone struggled this round. It is not low because the average pulls it down; it is low compared to tool use, planning, reasoning, and self-verification.

On 17 skill-related tasks, none of the three harnesses score well.

| Harness | Average on skill tasks | Observation |
| --- | ---: | --- |
| OpenClaw | 52.5 | Best of the three, still a weak spot |
| QwenPaw | 44.5 | Workspace skill auto-discovery is weak |
| Hermes | 44.6 | Often misses skills inside the workspace |


The issue is not "is there a skill mechanism", it is whether the harness can find skills dynamically, inject them correctly, and turn the actions inside a skill into tools the model can actually call.

Many tasks ship a `SKILL.md` in the current workspace that spells out the expert procedure or a custom script. If the harness only scans globally pre-installed skills and ignores the workspace, it misses that file entirely. The model never sees it and ends up improvising.

| Harness | Auto-discovers workspace `SKILL.md`? |
| --- | --- |
| Hermes | Scans `~/.hermes/skills/`, often misses skills inside the workspace |
| OpenClaw | Scans the workspace and renders into `<available_skills>` |
| QwenPaw | Can enable via `skill.json` and append to the prompt, but no workspace auto-scan |


The rest is on the model. Even when a skill is injected correctly, long-chain reasoning and precise computation still go wrong. The harness can put the signposts in place. Whether the model can follow them to the end of a complex task is still a model question.

### Finding 3: Web search tasks depend on what is available by default

Web search tasks here mean ones that need the model to browse pages, scrape content, or run deeper research. They do not measure the theoretical ceiling once every search API key is configured. They measure something closer to a developer's first experience: clone the pinned version, drop in an LLM key, and run.

Hermes scores low on this slice mostly because web search and page fetching tools are not in the default tool table. The source code does have `web_search` and `web_extract`, but they only turn on if you configure an extra search-provider key. We only configured LLM keys for this round, so the model never sees those tools and has to brute-force browser-style tools instead.

OpenClaw, by contrast, makes web functionality usable out of the box. `web_search` can run on keyless providers like DuckDuckGo, and `web_fetch` uses a built-in HTTP fetch that needs no extra key. QwenPaw does not have a dedicated search tool either, but it ships `browser_use`, which lets the model lean on its own URL knowledge and the browser to do limited web work.

| Harness | Web tools actually available this round | Zero-config experience |
| --- | --- | --- |
| Hermes | No `web_search` / `web_extract`, only browser-style tools | Needs extra key configuration |
| OpenClaw | `web_search` + `web_fetch` | Works out of the box |
| QwenPaw | Combined browser-style tool | No dedicated search |


So the web search numbers are not just a measure of the model's search and reading abilities. They also tell you whether the harness made the right tools available by default.

## Pulling it together: 4 design principles for harnesses

Stitching the slices together, PawBench points to 4 fairly direct principles for harness design.

<img src="https://intranetproxy.alipay.com/skylark/lark/0/2026/png/151913/1780536751934-74460985-061d-492a-8715-8b830b675c2e.png" width="612" title="" crop="0,0,1,1" id="Ab2k3" class="ne-image">

### 1. Inform Fully

If the model cannot see it, it does not exist.

The harness should tell the model the running context up front: where cwd is, where the workspace is, where outputs go, whether there is a `SKILL.md` in the workspace, and what other resources are around. Do not assume the model will figure that out.

### 2. Equip on Demand

Tools should be loaded right, and loaded lean.

"Right" means the important tools work in the default configuration: keyless web search, built-in HTTP fetch, automatic skill helper registration. "Lean" means the tool count should match the target model's context and attention budget. Bigger is not better. A bloated schema table can sink a smaller model.

### 3. Monitor Actively

Listen less to what the model said it did. Look at what it actually did.

The harness should verify that task artifacts really exist: file present, non-empty, has the required fields, tool calls well-formed, exit codes clean. For file writes, code edits, and report generation, artifact-level checks beat "I'm done" every time.

### 4. Recover Gracefully

One bad step is not the same as task failure.

When the harness sees an empty response, a plan with no action, a malformed tool call, or a missing artifact, it can give the model another shot with more context: inject the current state, note which artifact is missing, preserve intermediate results, and set a reasonable retry budget. A lot of tasks are not failures of capability. They are failures to course-correct at one critical step.

| Principle | Where it lives | One-line definition |
| --- | --- | --- |
| Inform Fully | Prompt assembly | Spell out the running environment and available resources |
| Equip on Demand | Tool injection | Keep the important tools default-on without overloading the schema |
| Monitor Actively | Execution and completion | Use artifacts and state to judge whether a task is done |
| Recover Gracefully | Error handling | Give the model a stateful retry after exceptions |


## Who PawBench is for

If you are an agent user, PawBench can help you pick the right model and harness combination. Performance varies across text-only, multimodal, skill, and web search tasks, and the best combination is not the same for each.

If you are a harness developer, PawBench is more than a leaderboard. It gives you a 4,050-cell comparison matrix and slicing tools that support three things:

1. Side-by-side check: run your harness against the same set of models, see which task types you trail on.
2. Failure profile: pick a suspect slice, compare traces, and identify the harness behavior responsible.
3. Regression check: after a fix, re-slice and see whether the score actually moves on the problem you targeted.

This kind of evaluation matters more for general-purpose agents than it does for narrow ones. Real users do not stop after one question. They have the agent operate files, call tools, run scripts, read web pages, and chain steps. PawBench tries to break those chains apart so model and harness capability can each be seen, diagnosed, and improved on its own.

> **💡 Optimize Your Evaluation Logic with OpenJudge**
> To build your own evaluation system beyond the LLM × Harness vertical, you can leverage **[OpenJudge](https://github.com/agentscope-ai/OpenJudge)**'s 50+ production-ready graders (relevance, tool selection, trajectory, etc.) to evaluate and optimize your custom agents.

## Contributing

PawBench v1.0 is open source. We welcome contributions from the community:

+ Adding new harnesses, such as CoPaw, Cursor Agent, or others.
+ Submitting new model evaluation results.
+ Contributing more tasks, annotated against the 5-label scheme.
+ Using the slice tools to dig into a slice you care about and reporting back what you find.

PawBench stands on the shoulders of the open-source agent evaluation community, including [Claw-Eval](https://github.com/claw-eval/claw-eval), [QwenClawBench](https://github.com/SKYLENAGE-AI/QwenClawBench), [WildClawBench](https://github.com/InternLM/WildClawBench), [PinchBench](https://github.com/pinchbench/skill), and [skillsbench](https://github.com/benchflow-ai/skillsbench).
