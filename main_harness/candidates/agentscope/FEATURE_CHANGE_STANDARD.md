# AgentScope Feature Change Standard

This is the normative procedure for adding, strengthening, disabling, or
removing an AgentScope Feature. A Feature is admitted only when its OFF path,
runtime seams, evidence, and release gates are all explicit.

## Boundary

A Feature is a harness-owned, independently switchable intervention around one
fixed AgentScope model and tool loop. It may change workspace policy, tool
contract, runtime policy, observability, or context and memory at its declared
seam. It must not change model identity or sampling, benchmark task or split,
Harbor authority, external verifier, accepted attribution evidence, or an
unrelated Feature.

## Required contract

Before editing, bind the request to:

- stable Feature ID, English and Chinese names, and one H1-H5 owner;
- causal mechanism, spatial dependencies, writes, conflicts, and OFF behavior;
- apply, trace, cleanup, cancellation, retry, and resume phases;
- a Feature-independent observable and Feature-owned trace event;
- safety floors that remain when the Feature is OFF;
- compatibility for config, state, trace, artifacts, CLI, and package;
- a frozen paired experiment and holdout gate;
- retirement/rollback behavior.

Active display names must not use `/` to join concepts. Historical aliases may
remain only as explicit compatibility provenance.

## Admission workflow

1. The attribution bridge accepts an optimization-arm H-to-F decision.
2. `agentscope-develop-feature prepare` persists a request and a compiled
   repository-skill receipt.
3. `agentscope-develop-feature run --execute` invokes only Qwen3.8-Max through
   Claude Code with strict empty MCP configuration and the local skill.
4. The coding agent implements and tests OFF before ON, then writes the exact
   `FEATURE_ADMISSION_RECEIPT.json`.
5. `agentscope-develop-feature verify` independently validates the request and
   receipt. Only then may the caller activate the nominated Feature.

## Mandatory gates

The receipt must contain exactly these passing gates:

`boundary`, `off_equivalence`, `spatial_contract`, `temporal_contract`,
`safety`, `causal_pair`, `holdout`, `compatibility`, and `benchmark_matrix`.

For every claimed benchmark, retain a one-Feature paired row for all 15
Features. A blocked or mechanism-not-observed row is not a causal pass, and an
absent row blocks a support claim. Published runs and frozen inputs are never
rewritten to make an admission pass.

## Removal symmetry

Removal uses the same Feature identity, seams, OFF/absence behavior, frozen
comparison, trace, safety, holdout, compatibility, package, and matrix gates.
Deleting a call site alone is not removal: registrations, consumers, state,
configuration, tests, documentation, package exports, and old artifact readers
must remain valid.
