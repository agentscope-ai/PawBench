# Boyin Handoff: Harness-core AgentScope-Lab → Harbor

Status: integration-ready boundary, 2026-07-16. Updated 2026-07-21.
Breaking rename (2026-07-21): the harness is now **AgentScope-Lab**. CLI command and
`agent.name` changed `harness-core-agentscope` → `agentscope-lab`, version `0.1.2` → `0.2.0`,
folder `candidates/agentscope` → `candidates/agentscope-lab`, and the wheel/schema pins below
were regenerated. Harbor side must re-pin the CLI name, expected agent name/version, and wheel SHA-256.
Ownership: Boyin owns the Harbor class/registry/package changes. Harness-core owns the CLI, Feature semantics, native trace, result envelope, and ATIF projection.

## What to integrate

Use the reference wrapper in [`reference_harbor_agent.py`](reference_harbor_agent.py) as a starting point inside the pinned Harbor checkout. It follows the current Harbor `BaseAgent` contract:

```python
async def setup(environment) -> None
async def run(instruction, environment, context) -> None
```

The wrapper must declare `SUPPORTS_ATIF = True`. Do not move Harbor imports into `pawbench_agentscope`; the CLI boundary is deliberate.

The wrapper deliberately uses Harbor's external `BaseAgent`, not
`BaseInstalledAgent`: Harbor owns the environment lifecycle and invokes one
already-installed, pinned CLI inside that environment. This avoids a second
agent loop or a copy of another PawBench agent's lifecycle.

The reference uploads the instruction through Harbor's `upload_file()` API and
passes `--instruction-file`; task text is not exposed in the process argument
list. It also copies the structured error code and `retryable` flag into
`AgentContext.metadata` before raising a runtime exception.

## Runtime command

The container must provide the `agentscope-lab` executable. A minimal invocation is:

```bash
agentscope-lab \
  --task-id "$SAFE_TRIAL_ID" \
  --instruction-file /tmp/harness-core-instruction.md \
  --workspace-root "$TASK_WORKDIR" \
  --logs-dir /logs/agent \
  --model qwen3.7-max
```

Feature ablation adds exactly one switch:

```bash
--disable-feature F2.2 --ablation-target F2.2=run_shell
```

All other Features remain ON. The wrapper should not infer H codes or select a Feature; that decision belongs to the accepted attribution/ablation plan.

## Input contract

| Input | Required | Meaning |
| --- | --- | --- |
| `instruction` | yes | Harbor's current task instruction passed to `run()` |
| `workspace-root` | yes | Current task workdir resolved inside the environment |
| `logs-dir` | yes | `/logs/agent`, mounted and synchronized by Harbor |
| `model` | yes in production | AgentScope/DashScope model name, without a provider prefix |
| `task-id` | yes | One safe path component; the reference sanitizes `session_id` |
| repeated `--artifact` | optional | Workspace artifacts for the internal verifier; Harbor's external verifier remains authoritative |
| `--disable-feature` | optional | Zero or one ID in a controlled comparison |

Environment credentials are passed through Harbor's agent environment mechanism. Never put API keys in command arguments or output files.

Keep `workspace-root` and `logs-dir` disjoint. Workspace snapshots and the
internal verifier cover task artifacts; `/logs/agent` carries bridge evidence
and must not become a task artifact root. The current input limits are 512 KiB
for the instruction and 256 UTF-8 bytes for the model name. Result, trajectory,
and provenance outputs are bounded by the portable CLI before publication.

## Output contract

Every completed process writes:

```text
/logs/agent/
├── result.json                  # process/result envelope
├── provenance.json              # input/config/output hashes
├── harness-core-trace.jsonl     # native Feature-aware trace
├── trajectory.json              # ATIF-v1.7 projection
└── harness-core-memory.json      # local memory state when used
```

The machine-readable schemas are in [`schemas/`](schemas/). Run the local semantic validator with:

```bash
python scripts/validate_harbor_contract.py /path/to/logs/agent
```

Harbor must still run its official ATIF validator. The local validator is an early compatibility check, not a fork of ATIF.

For the pinned checkout, the official validator can be invoked directly:

```bash
python -m harbor.utils.trajectory_validator \
  /logs/agent/trajectory.json \
  --no-validate-images
```

## Exit-code semantics

| CLI exit | Meaning | Harbor action |
| --- | --- | --- |
| `0` | Agent execution completed. The task may still have failed verification. | Continue to Harbor verifier; inspect `accepted` and `verifier.ok` as metadata only. |
| nonzero | Bridge/model/runtime error. `result.json` contains `success=false`. | Raise a runtime error and apply Harbor's configured retry policy. |

Do not convert `accepted=false` into a process failure. It is an evaluable agent outcome, not an adapter crash.

For nonzero exits, use `error_code` and `retryable` from
[`ERROR_CODES.md`](ERROR_CODES.md). Do not infer retryability by parsing the
human-readable error string.

## Version pins

The handoff currently identifies:

- agent: `agentscope-lab` `0.2.0`;
- result: `harness-core-harbor-result/v1`;
- provenance: `harness-core-provenance/v1`;
- taxonomy: `harness_core_v2_20260710`;
- trajectory: `ATIF-v1.7`.

Boyin should pin the Harbor commit used for the wrapper. The Harbor agent API and ATIF version are external compatibility surfaces and must not be silently upgraded.

Validated reference pin on 2026-07-16:

- Harbor package: `0.18.0`;
- Harbor commit: `d3e606d9f7d1e111bb22d3d820ebed03ec300eb3`;
- AgentScope package: `2.0.4.post1`;
- Harness-core wheel: `pawbench_agentscope_harness-0.2.0-py3-none-any.whl`;
- wheel SHA-256: `1cecdefbb2227eaea6224116ca56f58a4ed328c07eddc6eb7730975a564eb38d`;
- isolated import/CLI: passed with no Harness-core repository path in `sys.path`;
- official ATIF model/validator: 285/285 generated trajectories accepted (1,219 steps);
- local contract validator: 309/309 current envelopes valid, comprising 285
  provenance-bound successes and 24 structured errors;
- wrapper contract simulation: valid success/error accepted; path traversal,
  forged retryability, and partial coded-error metadata rejected;
- instruction text remains absent from process arguments.

This is a compatibility pin for handoff, not a Harbor dependency added to
Harness-core. Re-run both validators whenever Boyin upgrades the pin.

## Multi-step and resume boundary

The reference wrapper explicitly declares `SUPPORTS_RESUME = False`. Harbor
multi-step tasks may call `run()` once per step with fresh agent logs, but the
integration must not set `agent.resume_trajectory=true`. Harness-core's local
recovery and evidence-bound batch reuse are not the same contract as Harbor
native-session continuation.

If Harbor-native continuation is added later, version it as a separate handoff:
prove session identity, preserved logs, ATIF continuation semantics, and
cross-step memory isolation first. Do not advertise resume by merely retaining
`harness-core-memory.json`.

## Harbor-side checklist

1. Put the pinned Harness-core wheel in the image or implement installation in Harbor `setup()`.
2. Copy the reference wrapper into Harbor's installed agents area.
3. Register the name in Harbor's current agent registry/enum.
4. Forward DashScope credentials through Harbor agent env, not shell arguments.
5. Run one passing task and one verifier-failing task; both processes should exit `0`.
6. Run one deliberately invalid model/credential case; it should exit nonzero with a redacted error result.
7. Run Harbor's ATIF validator on `trajectory.json`.
8. Confirm the Harbor verifier executes after the agent and remains authoritative.
9. Record the Harbor commit, Harness-core wheel hash, model name, and schema versions in the integration PR.
10. Confirm task workspace and `/logs/agent` mounts are distinct and record the
    Harbor container, network, and resource policy used by the trial.
11. For multi-step tasks, keep `resume_trajectory=false`; verify each step's
    archived agent directory independently.

## Deliberate non-goals

- No copy of QwenPaw lifecycle code.
- No Harbor dependency in the Harness-core package.
- No reasoning prompt or rubric changes.
- No new task-specific Feature IDs.
- No automatic code repair from an attribution result.

The local AgentScope backend plus workspace middleware is not an OS sandbox.
Strong filesystem, process, network, and resource containment remains Harbor's
environment responsibility.
