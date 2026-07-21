# PawBench AgentScope Harness

Harness-core's compact AgentScope backend. AgentScope owns the agent/model/tool
loop; Harness-core adds Feature switches, workspace guards, native trace,
verification, Harbor artifacts, and the evidence-gated ablation loop.

The core design is fixed: 15 shared Features, one AgentScope loop, and the
read-only `main_reasoning/simple_v2` S0 → S1 → Audit workflow.

## Feature catalog

| H code | Existing Features |
| --- | --- |
| H1 Environment / Workspace | F1.1 Workspace Binding, F1.2 Readiness / Reset, F1.3 Isolation / Permissions |
| H2 Tool Contract | F2.1 Action Contract, F2.2 Tool Availability, F2.3 Result / Error Feedback |
| H3 Runtime / Loop | F3.1 Completion / Termination, F3.2 Budget / Guards, F3.3 Recovery / Resume |
| H4 Observability / Acceptance | F4.1 Diagnostic Trace, F4.2 State / Artifact Deltas, F4.3 Verification |
| H5 Context / Memory | F5.1 Context Assembly, F5.2 Persistent Memory, F5.3 Context Compaction |

`Ex-*` and `M*` remain reportable but never trigger Feature ablation by
themselves. UA, WS, and MA profiles change stress priority only; they do not
add IDs or change runtime behavior.

## Reproduce the local evidence

Run from the Harness-core repository root:

```bash
.venv/bin/python -m pytest main_harness/candidates/agentscope-lab/tests -q

.venv/bin/python main_harness/candidates/agentscope-lab/scripts/closed_loop_demo.py \
  --iterations 1 --workers 3

.venv/bin/python main_harness/candidates/agentscope-lab/scripts/closed_loop_fault_matrix.py \
  --iterations 1 --workers 4

.venv/bin/python main_harness/candidates/agentscope-lab/scripts/v2_ablation_matrix.py

.venv/bin/python main_harness/candidates/agentscope-lab/scripts/runtime_failure_matrix.py \
  --fresh
```

Current reference results: candidate tests 422/422; Feature-OFF matrix 15/15;
community loop 3/3; H1–H5 matrix 8/8; runtime routes 10/10.

## Real-model stress and resume

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

The current receipt contains 60/60 accepted runs and 60/60 clean trajectory
shadow audits. Resume is evidence-bound: it reuses a task only when the local
trace, shadow audit, result record, workspace, model identity, and artifact
hashes still agree. The reference replay reused 60/60 in 2.10 seconds with no
API calls.

## Closed loop and community demo

The complete test → pass validation / failure attribution → H-only Feature
mapping → one-Feature-OFF comparison workflow is documented in
[`community_demo/README.md`](community_demo/README.md).

Boyin's Harbor contract, schemas, error semantics, and reference wrapper are in
[`harbor_adapter/BOYIN_HANDOFF.md`](harbor_adapter/BOYIN_HANDOFF.md).

## Standalone Harbor wheel

```bash
.venv/bin/python main_harness/candidates/agentscope-lab/scripts/build_release_wheel.py \
  > /tmp/harness-wheel-receipt.json

python -m pip install \
  main_harness/candidates/agentscope-lab/dist/pawbench_agentscope_harness-0.2.0-py3-none-any.whl

agentscope-lab --help
```

The build runs twice under a fixed source epoch and publishes only
byte-identical wheels. The current wheel SHA-256 is
`1cecdefbb2227eaea6224116ca56f58a4ed328c07eddc6eb7730975a564eb38d`.

## Safety boundary

Workspace middleware, bounded I/O, redaction, strict JSON, verification, and
provenance are defense in depth. This local backend is not an OS sandbox.
Harbor remains responsible for strong process, filesystem, network, and
resource containment, and its external verifier remains authoritative.
