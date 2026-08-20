# AgentScope Feature implementation contract

## Input

The host supplies one `agentscope-opt-feature-development/v1` request with:

- `evidence_role: optimization`;
- one accepted H1-H5 `h_code` and one Feature allowed by that code;
- the enabled Feature set, attribution evidence paths, and Feature contract;
- frozen boundaries and an exact admission-receipt path.

Fail closed when source taxonomy conflicts or edit guidance is from a holdout.
Holdout may evaluate an admitted implementation later, but can never guide it.

## Required gates

Record every boolean below under `gates`; admission requires all to be true.

1. `boundary` - only the selected harness seam changed.
2. `off_equivalence` - OFF preserves the frozen baseline.
3. `spatial_contract` - dependencies, writes, conflicts, and absence behavior are explicit.
4. `temporal_contract` - apply, trace, cleanup, retry, cancellation, and resume behavior are valid.
5. `safety` - absolute guards, Harbor authority, and the outer audit remain intact.
6. `causal_pair` - the frozen paired intervention meets its development target.
7. `holdout` - untouched evaluation evidence satisfies the declared gate.
8. `compatibility` - config, trace, artifact, CLI, and package contracts remain valid.
9. `benchmark_matrix` - all required Feature rows exist and blocked cells are not called passes.

## Admission receipt

Write strict JSON at the exact request path. Use workspace-relative paths and
never include credentials, raw environment values, hidden reasoning, or
unbounded process output.

```json
{
  "schema_version": "agentscope-opt-feature-admission/v1",
  "status": "admitted",
  "h_code": "H1",
  "feature_id": "F1.1",
  "coding_agent": {"model": "qwen3.8-max", "harness": "claude-code"},
  "skill_id": "implement-attributed-feature",
  "changed_files": ["relative/path.py"],
  "validation_runs": [{"command": "python -m pytest focused.py -q", "exit_code": 0, "result": "passed"}],
  "gates": {"boundary": true, "off_equivalence": true, "spatial_contract": true, "temporal_contract": true, "safety": true, "causal_pair": true, "holdout": true, "compatibility": true, "benchmark_matrix": true},
  "notes": "Evidence-bound summary without secrets."
}
```
