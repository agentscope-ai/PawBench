# Harness-core AgentScope Closed-Loop Demo

This demo shows one complete, evidence-gated loop:

```text
task test → judge result → pass validation or failure attribution
          → H-only Feature mapping → one Feature OFF → compare → report
```

The core design is unchanged. AgentScope owns the agent loop; Harness-core owns
Feature controls, traces, attribution routing, ablation policy, and reports.
The stable reasoning system is consumed as a read-only decision boundary.

## Five-minute run

From the Harness-core repository root:

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/closed_loop_demo.py \
  --iterations 1 \
  --workers 3
```

Expected result:

```text
3 tasks
2 single-Feature-OFF comparisons
2 supported hypotheses
all policy checks true
```

Open:

- `harness_ablation_runs/agentscope/closed_loop_demo/REPORT_EN.md`
- `harness_ablation_runs/agentscope/closed_loop_demo/REPORT_ZH.md`
- `harness_ablation_runs/agentscope/closed_loop_demo/summary.json`

## What the three tasks prove

| Domain | Observed result | Decision | Controlled comparison |
| --- | --- | --- | --- |
| UA | pass | task design, judge, external resources, and pass audit are clear | no ablation |
| WS | fail without workspace binding | H1 → F1.1 | all ON passes; F1.1 OFF fails |
| MA | fail without local context injection | H5 → F5.1 | all ON passes; F5.1 OFF fails |

Only final accepted `H1`–`H5` evidence can schedule an experiment. `Ex-*` and
`M*` may appear in the same verdict, but never trigger Feature ablation by
themselves. Each comparison disables exactly one Feature and keeps the other
fourteen enabled.

## Real components and local fixtures

The demo executes these production components directly:

- AgentScope runtime and tools;
- the 15 Harness-core Feature switches;
- Harbor-compatible filesystem input/output and ATIF export;
- `main_reasoning.simple_v2` schema, evidence, and audit validators;
- the existing passed-task Ex-1/Ex-2/Ex-3 validator;
- the replayable, non-authoritative trajectory shadow auditor;
- the existing evidence-aware H-to-F bridge;
- score, trajectory-event, and artifact-hash comparison.

For an offline, reproducible quick start, only three boundaries are fixtures:
the agent model, reasoner model, and exact-content judge. The receipt records
this distinction in `demo_receipt.json`. The offline reasoner is not presented
as a substitute for the production Qwen plus Agents audit.

For a reported pass, the compact report shows four independent facts:

- Ex-1 — task, rubric, and output agree;
- Ex-2 — judge score and aggregation agree with the evidence;
- Ex-3 — no blocking provider or external-resource failure is present;
- audit — the pass-validation receipt is internally consistent.

A clear pass produces an empty attribution and no ablation. Any flagged or
insufficient check remains visible for manual review and still schedules no
automatic Feature experiment.

## Production reasoning path

Run the existing agentic grader first. Qwen3.7-Max proposes the attribution;
Agents performs the final evidence audit for failed tasks and the existing Ex
validator handles reported passes.

```bash
.venv/bin/python skills/pawbench-v2-agentic-grader/scripts/run_batch.py \
  --source-root <prepared-v2-source> \
  --output <new-reasoning-run>
```

Convert the final run into an ablation plan:

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/closed_loop_plan.py \
  --reasoning-root <new-reasoning-run> \
  --source-root <prepared-v2-source> \
  --output <new-closed-loop-plan>
```

The planner prefers `agentic-audit/verdicts/<task>.json`. It reuses
`agentic-audit/pass-validations/<task>.json` when present and otherwise invokes
the same passed-score validator. It never rewrites the reasoning recording.

## Output contract

```text
<closed-loop-run>/
├── REPORT_EN.md
├── REPORT_ZH.md
├── summary.json
├── recordings/
│   └── <task-id>.json
└── runs/
    └── <task-id>/
        ├── observed/
        ├── all_features_on/
        └── without_<feature-id>/
```

Each runtime variant writes:

```text
logs/agent/
├── result.json
├── provenance.json
├── harness-core-trace.jsonl
├── trajectory.json
└── harness-core-memory.json
```

`trajectory.json` is the ATIF projection. The native trace remains the source
for Feature events and harness diagnostics. `provenance.json` binds the task,
Feature configuration, required artifacts, trace, and trajectory with hashes.

## Harbor handoff for Boyin

The Harbor-facing layer is intentionally thin. A Harbor registry agent should:

1. implement Harbor's `BaseAgent` lifecycle;
2. pass the task instruction, workspace mount, task artifacts, and timeout to
   the `agentscope-lab` command or `run_harbor_task()`;
3. publish `/logs/agent/trajectory.json` as a task artifact;
4. read `result.json` for `accepted`, verifier evidence, taxonomy version, and
   enabled/disabled Features.

The adapter should not reimplement the AgentScope loop, Feature semantics,
reasoning workflow, H-to-F policy, or ablation comparator. A completed agent
run may still have `accepted=false`; the process boundary reports execution
completion while `result.json` carries task acceptance.

Use Harbor's external `BaseAgent` wrapper around the pinned CLI. Multi-step
tasks can start a fresh Harness-core run for each step, but
`resume_trajectory` must remain false: local retry/batch reuse does not yet
implement Harbor's native cross-step session-resume contract.

Keep the task workspace and `/logs/agent` as distinct roots. The local
workspace guard is defense in depth; Harbor owns strong process, filesystem,
network, and resource containment.

UA, WS, and MA stress priorities are published in
[`../domain_profiles.json`](../domain_profiles.json). The file maps the eight
observed V2 prefixes to existing Feature IDs for coverage planning only. It
does not enable, disable, or redefine any Feature at runtime.

The exact handoff, schemas, validator command, and reference Harbor class are
in [`../harbor_adapter/BOYIN_HANDOFF.md`](../harbor_adapter/BOYIN_HANDOFF.md).

## Standalone Harbor package

The Harbor-facing runtime is installable without adding the Harness-core
repository to `PYTHONPATH`:

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/build_release_wheel.py \
  > /tmp/harness-wheel-receipt.json
python -m pip install \
  main_harness/candidates/agentscope-lab/dist/pawbench_agentscope_harness-0.2.0-py3-none-any.whl
agentscope-lab --help
```

The release script builds twice with a fixed source epoch and publishes only
byte-identical wheels. `dist/SHA256SUMS` is the delivery checksum;
`dist/build-receipt.json` records the toolchain and every wheel-member hash.

The wheel includes the Feature manifest, domain profiles, and portable
taxonomy/security contracts. Tests require the latter two to remain
byte-identical to the canonical `main_harness/scripts` implementations and
differentially check the portable H-to-Feature bridge. The 2026-07-17 reference
build was tested from outside the repository with Harbor `0.18.0` and
AgentScope `2.0.4.post1`.

Reference receipt:

- wheel SHA-256: `1cecdefbb2227eaea6224116ca56f58a4ed328c07eddc6eb7730975a564eb38d`;
- 309/309 current Harbor result/error directories contract-valid;
- 285/285 trajectories accepted by Harbor's official model (1,219 steps).

Harbor references:

- <https://github.com/harbor-framework/harbor/blob/main/AGENTS.md>
- <https://www.harborframework.com/docs/agents/trajectory-format>
- <https://www.harborframework.com/docs/tasks>

## Review an ablation proposal before spending model budget

The community boundary includes a strict experiment declaration, an example,
and a read-only semantic validator:

```bash
.venv/bin/python \
  main_harness/candidates/agentscope-lab/scripts/validate_ablation_experiment.py \
  main_harness/candidates/agentscope-lab/community_demo/ablation_experiment.example.json
```

Expected: `ok=true`, one `H1→F1.1` intervention, disjoint calibration /
validation / held-out task sets, a shared budget, and
`authority=validation_only`. The example is an illustrative proposal, not a
completed benchmark run.

The declaration freezes:

- model, harness, environment, Feature-manifest, and task-set identities;
- all-ON versus exactly one-Feature-OFF variants;
- trials, calls, tokens, and timeout limits shared by both variants;
- expected fixes, regression risks, and content-addressed evidence;
- matched task-level baseline and held-out gate for a generalization claim.

It also fixes four non-negotiable governance values: human approval is
required, core mutation is forbidden, reasoning is read-only, and shadow
analyzers are non-authoritative. The validator performs no execution and writes
no files. Its interoperable shape is published in
[`ablation_experiment.schema.json`](ablation_experiment.schema.json); the
validated example is
[`ablation_experiment.example.json`](ablation_experiment.example.json).

## Stress run

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/closed_loop_demo.py \
  --output harness_ablation_runs/agentscope/closed_loop_stress \
  --iterations 10 \
  --workers 6
```

This produces 30 task records and 20 independent single-Feature-OFF
comparisons. It is deterministic, supports parallel workers within one
invocation, and requires no API key.

The planted-fault matrix covers every H route plus negative controls:

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/closed_loop_fault_matrix.py \
  --output harness_ablation_runs/agentscope/closed_loop_fault_matrix \
  --iterations 1 \
  --workers 4
```

Expected: H1→F1.1, H2→F2.2, H3→F3.3, H4→F4.3, and H5→F5.1
are supported; passed, Ex-3-only, and M2-only controls schedule no ablation.

The repeated reference matrix uses ten trials per case:

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/closed_loop_fault_matrix.py \
  --output harness_ablation_runs/agentscope/closed_loop_fault_matrix_stress \
  --iterations 10 \
  --workers 6
```

Current result: 80 tasks, 50/50 supported comparisons, and 10/10 support for
each selected Feature.

The bridge error-envelope matrix covers every stable adapter code:

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/error_code_matrix.py \
  --output harness_ablation_runs/agentscope/error_code_matrix_20260716
```

Expected: 9/9 codes emitted, 9/9 directories contract-valid, with retryability
read from the machine field rather than inferred from error text.

The real runtime matrix additionally exercises preflight, AgentScope provider
exceptions, a generic runtime exception, and the completed native-timeout
boundary:

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/runtime_failure_matrix.py \
  --fresh
```

Current result: 10/10 routes matched, 10/10 contract-valid, and 10/10 shadow
audit expectations matched. Abnormal error endings are complete traces; the
native timeout intentionally retains an `empty_model_output` review signal.

For repeated or stochastic trials, summarize the already-recorded comparisons:

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/summarize_repeated_ablation.py \
  --closed-loop-root harness_ablation_runs/agentscope/closed_loop_fault_matrix_stress \
  --output harness_ablation_runs/agentscope/closed_loop_fault_matrix_reliability \
  --minimum-trials 5 \
  --fresh
```

This outer report adds support rates and 95% Wilson intervals. It never changes
the accepted attribution or schedules an experiment.

## Real-model stress and evidence-bound resume

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/agentic_stress.py \
  --models qwen3.7-max deepseek-v4-pro kimi-k2.6 \
  --tasks-per-model 20 \
  --per-model-concurrency 3 \
  --global-concurrency 9 \
  --max-iters 14 \
  --timeout-seconds 180 \
  --out-dir harness_ablation_runs/agentscope/local_task_stress_20260716 \
  --resume
```

Current result: 60/60 accepted, 60/60 verifier passes, 60/60 complete traces,
and 60/60 clean trajectory shadow audits. The resume proof reused all 60 local
records in 2.10 seconds with zero API reruns. Reuse requires matching trace,
shadow receipt, result, workspace, model identity, and artifact hashes.

When the judge itself needs an Ex-2 reliability check, run the optional shadow
probe from the grader skill. It tests formatting, paraphrase, verbosity, and a
flipped-ground-truth control without changing the official score or verdict:

```bash
.venv/bin/python skills/pawbench-v2-agentic-grader/scripts/judge_shadow_stress.py \
  --model qwen3.7-max \
  --repetitions 3 \
  --output harness_ablation_runs/agentscope/judge_shadow_stress_20260716/summary.json
```

## Extension points

- Replace the offline agent fixture with the existing DashScope model adapter.
- Feed official verifier scores into `RunObservation.score`.
- Keep task setup/reset outside the comparison so every variant starts from an
  identical workspace snapshot.
- Add task packages or Harbor registry metadata without changing Feature IDs.
- Add new diagnostics to the native trace; preserve the public closed-loop and
  ATIF schemas.

## Safety and interpretation

- An H code is a hypothesis until a controlled comparison supports it.
- An unchanged OFF run is `inconclusive`, not proof that the Feature is useless.
- An improved OFF run is `contradicted` and should trigger investigation.
- Score, trajectory, and artifacts are compared together; score alone is not
  accepted as causal evidence.
- Ex/M-only failures remain visible in reports and are never silently converted
  into Harness Feature work.
- Trajectory shadow flags are review leads only; they never modify the canonical
  score, verdict, or H/Ex/M attribution.
- The local backend is not an OS sandbox; containment claims require Harbor's
  recorded environment and network policy.
