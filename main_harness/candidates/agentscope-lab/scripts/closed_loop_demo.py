#!/usr/bin/env python3
"""Run an offline UA/WS/MA test → reasoning → Feature-ablation demo.

The demo uses the real AgentScope runtime, real Feature switches, stable
``main_reasoning.simple_v2`` validation, the existing passed-task Ex validator,
and the existing H-to-F bridge.  Only the model calls are deterministic local
fixtures so the community demo needs no API key.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CANDIDATE_ROOT.parents[2]
for value in (PROJECT_ROOT, PROJECT_ROOT / "main_harness", CANDIDATE_ROOT / "src"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from agentscope.message import TextBlock, ToolCallBlock  # noqa: E402
from agentscope.model import ChatModelBase, ChatResponse  # noqa: E402

from main_reasoning.simple_v2.workflow import run_workspace  # noqa: E402
from main_reasoning.simple_v2.workspace import SimpleWorkspace  # noqa: E402
from scripts.feature_taxonomy import (  # noqa: E402
    CODE_ORDER,
    CODE_TABLE,
    FEATURE_IDS,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
)
from pawbench_agentscope._atomic_io import atomic_write_text, prepare_marked_output  # noqa: E402
from pawbench_agentscope.closed_loop import (  # noqa: E402
    RunObservation,
    build_ablation_plan,
    execute_task_plan,
    load_reasoning_outcome,
    observation_from_harbor,
    write_closed_loop_run,
)
from pawbench_agentscope.domain_profiles import load_domain_profiles  # noqa: E402
from pawbench_agentscope.features import WORKSPACE_BINDING_PROMPT_MARKER  # noqa: E402
from pawbench_agentscope.harbor_bridge import (  # noqa: E402
    build_feature_config,
    load_harbor_task_contract,
    run_harbor_task,
)


EXPECTED = "closed loop expected"
WORKSPACE_MARKER = WORKSPACE_BINDING_PROMPT_MARKER
CONTEXT_MARKER = "V2_CONTEXT_MARKER"


@dataclass(frozen=True, slots=True)
class DemoCase:
    family: str
    observed_disabled_feature: str | None
    reason_code: str | None
    expected_feature: str | None
    required_marker: str | None
    harness_observation: str
    cause_scope: str = "harness"


CASES = (
    DemoCase(
        family="ua",
        observed_disabled_feature=None,
        reason_code=None,
        expected_feature=None,
        required_marker=None,
        harness_observation="The task, judge score, external resources, and output all passed.",
    ),
    DemoCase(
        family="ws",
        observed_disabled_feature="F1.1",
        reason_code="H1",
        expected_feature="F1.1",
        required_marker=WORKSPACE_MARKER,
        harness_observation=(
            "The workspace binding and artifact path map were absent, causing a wrong-root result."
        ),
    ),
    DemoCase(
        family="ma",
        observed_disabled_feature="F5.1",
        reason_code="H5",
        expected_feature="F5.1",
        required_marker=CONTEXT_MARKER,
        harness_observation=(
            "Context assembly omitted the required SKILL.md context source and skill injection marker."
        ),
    ),
)


def _has_tool_result(messages: list[Any]) -> bool:
    return any(
        any(getattr(block, "type", "") == "tool_result" for block in message.content)
        for message in messages
    )


class FeatureSensitiveModel(ChatModelBase):
    """Local fixture whose result depends on one real Feature-provided marker."""

    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self, required_marker: str | None, model_name: str) -> None:
        super().__init__(
            credential=None,
            model=model_name,
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )
        self.required_marker = required_marker

    async def _call_api(
        self,
        model: str,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        tool_choice=None,
        **kwargs: Any,
    ) -> ChatResponse:
        if _has_tool_result(messages):
            return ChatResponse(
                content=[TextBlock(text="Saved answer.txt and completed the task.")],
                is_last=True,
            )
        prompt = "\n".join(str(message) for message in messages)
        marker_present = self.required_marker is None or self.required_marker in prompt
        value = EXPECTED if marker_present else "wrong"
        command = f"printf {json.dumps(value)} > answer.txt"
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id=f"write-{model}-{len(messages)}",
                    name="Bash",
                    input=json.dumps(
                        {
                            "command": command,
                            "description": "write deterministic demo artifact",
                        }
                    ),
                )
            ],
            is_last=True,
        )


class DeterministicReasoner:
    """Three-call local fixture; all schema/evidence checks remain real."""

    def __init__(self, task_id: str, case: DemoCase, passed: bool) -> None:
        self.task_id = task_id
        self.case = case
        self.passed = passed

    def __call__(self, _prompt: str, *, stage: str, iteration: int) -> str:
        del iteration
        if stage == "s0":
            return json.dumps(
                {
                    "task_id": self.task_id,
                    "summary": "Offline closed-loop task with real runtime evidence.",
                }
            )
        if stage == "audit":
            return json.dumps(
                {
                    "status": "pass",
                    "reason": "The proposed result matches frozen evidence and rubric policy.",
                }
            )
        if stage != "s1":
            raise ValueError(f"unexpected reasoning stage: {stage}")
        if self.passed:
            payload = {
                "task_id": self.task_id,
                "reasoning_summary": "The reported pass is valid; no failure is attributable.",
                "reasoning_summary_en": "The reported pass is valid; no failure is attributable.",
                "reasoning_summary_zh": "该任务通过且校验一致，不存在可归因失败。",
                "attribution_status": "no_attributable_failure",
                "codes": [],
                "features": [],
                "cause_scopes": [],
                "reasoning_details": [],
                "reasoning_details_zh": [],
            }
            return json.dumps(payload, ensure_ascii=False)

        code = str(self.case.reason_code)
        feature = self.case.expected_feature
        rationale = self.case.harness_observation
        evidence = [
            {"section": "environment_setting", "source_path": "/harness_observation"},
            {"section": "agent_trajectory", "source_path": "/harness_observation"},
            {"section": "scoring_details", "source_path": "/final_observation"},
        ]
        detail = {
            "reasoning_code": code,
            "task_requirement": "Create answer.txt with the exact expected content.",
            "task_input": rationale,
            "scoring_rubric": "The connected verifier checks answer.txt exact content.",
            "proof": "The controlled harness omission precedes the failed verifier observation.",
            "reasoning_verdict": f"{code} owns the evidenced harness mechanism.",
        }
        detail_zh = {
            "reasoning_code": code,
            "task_requirement": "生成内容完全正确的 answer.txt。",
            "task_input": rationale,
            "scoring_rubric": "已连接的验证器检查 answer.txt 的精确内容。",
            "proof": "可控的 harness 缺失先于验证失败，因果链完整。",
            "reasoning_verdict": f"该机制属于 {code}。",
        }
        return json.dumps(
            {
                "task_id": self.task_id,
                "reasoning_summary": rationale,
                "reasoning_summary_en": rationale,
                "reasoning_summary_zh": f"归因结论：{rationale}",
                "attribution_status": "coded_failure",
                "codes": [{"code": code, "rationale": rationale, "evidence": evidence}],
                "features": [feature] if feature else [],
                "cause_scopes": [self.case.cause_scope],
                "reasoning_details": [detail],
                "reasoning_details_zh": [detail_zh],
            },
            ensure_ascii=False,
        )


def _write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _fresh_workspace(root: Path, case: DemoCase) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    if case.family == "ma":
        (root / "SKILL.md").write_text(
            f"# Demo skill\n\nRequired scientific context: {CONTEXT_MARKER}\n",
            encoding="utf-8",
        )
    return root


def _run_variant(
    output: Path,
    task_id: str,
    case: DemoCase,
    variant: str,
    disabled_feature: str | None,
) -> RunObservation:
    root = output / "runs" / task_id / variant
    workspace = _fresh_workspace(root / "workspace", case)
    logs = root / "logs" / "agent"
    instruction = "Create answer.txt containing exactly: closed loop expected"
    contract = load_harbor_task_contract(
        workspace_root=workspace,
        task_id=task_id,
        instruction=instruction,
        required_artifacts=["/app/answer.txt", "/logs/agent/trajectory.json"],
    )
    contract.task.required_tools = ["run_shell"]
    contract.task.hidden_contract["artifact_text"] = {"answer.txt": EXPECTED}
    disabled = [disabled_feature] if disabled_feature else []
    config = build_feature_config(
        disabled_features=disabled,
        runtime_timeout_seconds=20.0,
    )
    result = run_harbor_task(
        contract,
        workspace_root=workspace,
        logs_dir=logs,
        feature_config=config,
        model=FeatureSensitiveModel(
            case.required_marker,
            f"closed-loop-{case.family}-{variant}",
        ),
        model_name=f"offline-{case.family}",
        max_iters=4,
    )
    return observation_from_harbor(
        result,
        variant=variant,
        workspace_root=workspace,
        disabled_features=disabled,
    )


def _taxonomy() -> dict[str, Any]:
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "code_order": list(CODE_ORDER),
        "codes": [
            {"code": code, "family": CODE_TABLE[code].family}
            for code in CODE_ORDER
        ],
        "features": [{"feature_id": feature_id} for feature_id in FEATURE_IDS],
        "h_to_features": {
            code: list(feature_ids) for code, feature_ids in H_TO_FEATURES.items()
        },
    }


def _atif_to_reasoning_trajectory(
    trajectory_path: str | None,
    observed: RunObservation,
    harness_observation: str,
) -> dict[str, Any]:
    atif = _read_json(Path(str(trajectory_path))) if trajectory_path else {"steps": []}
    steps: list[dict[str, Any]] = []
    for raw in atif.get("steps", []):
        if not isinstance(raw, Mapping):
            continue
        step: dict[str, Any] = {
            "source": raw.get("source"),
            "message": raw.get("message", ""),
        }
        if raw.get("tool_calls"):
            step["tool_calls"] = raw["tool_calls"]
        observation = raw.get("observation")
        if isinstance(observation, Mapping):
            results = observation.get("results")
            if isinstance(results, list):
                step["tool_results"] = [
                    {"content": str(item.get("content", ""))}
                    for item in results
                    if isinstance(item, Mapping)
                ]
        steps.append(step)
    if not steps:
        steps.append({"source": "agent", "message": observed.final_text})
    if observed.passed:
        steps.append(
            {
                "source": "agent",
                "message": observed.final_text or "Task completed.",
                "tool_results": [
                    {"content": "Successfully wrote answer.txt; verifier confirmed it."}
                ],
            }
        )
    return {
        "steps": steps,
        "harness_observation": harness_observation,
        "native_trace": observed.trace_path,
        "atif_trajectory": observed.trajectory_path,
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _reasoning_document(
    task_id: str,
    case: DemoCase,
    observed: RunObservation,
) -> dict[str, Any]:
    instruction = "Create answer.txt containing exactly: closed loop expected"
    score = observed.score
    passed = observed.passed
    reward = {"artifact_exact": score}
    code_to_scope = {
        code: ("external" if entry.family == "Ex" else "harness" if entry.family == "H" else "model")
        for code, entry in CODE_TABLE.items()
    }
    return {
        "schema_version": "pawbench-v2-four-section/v1",
        "task_id": task_id,
        "environment_setting": {
            "working_area": "/app" if case.family == "ma" else "/home/node/workspace",
            "harness_observation": case.harness_observation,
            "multi_turn_execution": {
                "intended_user_turns": [{"artifact_names": ["answer.txt"]}]
            },
        },
        "rubrics_setting": {
            "attribution_taxonomy": _taxonomy(),
            "authority_model": {
                "agent_obligation": "runtime_visible_instruction",
                "scoring_facts": "resolved_executable_evaluator_wiring",
            },
            "runtime_visible_instruction": {"content": instruction},
            "evaluator_wiring": {
                "resolution_status": "resolved",
                "parsed_test_commands": ["verify answer.txt exact content"],
                "authoritative_sources": [
                    {"content": "answer.txt must contain exactly closed loop expected"}
                ],
                "parsed_reward_config": {
                    "reward": [{"aggregation": "threshold", "threshold": 1.0}]
                },
            },
            "conclusion_contract": {
                "cause_scope_policy": {
                    "code_to_scope": code_to_scope,
                    "scope_order": ["external", "harness", "model"],
                },
                "code_assignment_policy": {
                    "score_is_not_causality": "A failed score alone never proves a code."
                },
                "outcome_binding": {
                    "observed_passed": passed,
                    "required_attribution_status": (
                        "no_attributable_failure" if passed else "coded_failure"
                    ),
                },
            },
        },
        "scoring_details": {
            "original_verifier_model": "offline-exact-verifier",
            "final_observation": {
                "passed": passed,
                "score": score,
                "status": "success" if passed else "failed",
                "exception_info": None,
                "breakdown": reward,
                "verifier_reward": reward,
            },
        },
        "agent_trajectory": _atif_to_reasoning_trajectory(
            observed.trajectory_path,
            observed,
            case.harness_observation,
        ),
    }


def _write_reasoning_recording(
    source_root: Path,
    reasoning_root: Path,
    task_id: str,
    case: DemoCase,
    observed: RunObservation,
) -> dict[str, Any]:
    document = _reasoning_document(task_id, case, observed)
    input_path = source_root / "workspaces" / task_id / "input" / "x.json"
    _write_json(input_path, document)
    workspace = SimpleWorkspace.load(source_root, task_id)
    result = run_workspace(
        workspace,
        DeterministicReasoner(task_id, case, observed.passed),
        "offline-reasoner",
    )
    public = {
        "schema_version": "simple-v2-public-recording/v1",
        "task_id": task_id,
        "task_family": workspace.task_family,
        "working_area": workspace.working_area,
        "passed": workspace.passed,
        "score": workspace.score,
        "attribution_required": workspace.attribution_required,
        "status": result["status"],
        "accepted": result["accepted"],
        "audit_status": result.get("audit", {}).get("status"),
        "result": result.get("result"),
    }
    _write_json(reasoning_root / "recordings" / f"{task_id}.json", public)
    return public


def _run_case(output: Path, source: Path, reasoning: Path, case: DemoCase, iteration: int) -> dict[str, Any]:
    task_id = f"{case.family}-closed-loop-{iteration:03d}"
    observed = _run_variant(
        output,
        task_id,
        case,
        "observed",
        case.observed_disabled_feature,
    )
    reasoning_recording = _write_reasoning_recording(
        source,
        reasoning,
        task_id,
        case,
        observed,
    )
    if reasoning_recording.get("accepted") is not True:
        raise RuntimeError(f"reasoning validation rejected {task_id}: {reasoning_recording}")
    outcome = load_reasoning_outcome(reasoning, source, task_id)
    plan = build_ablation_plan(outcome)

    def executor(variant: str, feature_id: str | None) -> RunObservation:
        return _run_variant(output, task_id, case, variant, feature_id)

    receipt = execute_task_plan(outcome, plan, observed, executor)
    expected_features = [case.expected_feature] if case.expected_feature else []
    if plan.get("recommended_feature_ids") != expected_features:
        raise RuntimeError(
            f"unexpected H-to-F plan for {task_id}: {plan.get('recommended_feature_ids')}"
        )
    if case.expected_feature:
        statuses = [item.get("status") for item in receipt.get("comparisons", [])]
        if statuses != ["supported"]:
            raise RuntimeError(f"ablation did not support {task_id}: {statuses}")
    else:
        validation = outcome.get("pass_validation") or {}
        if validation.get("status") != "validated_pass":
            raise RuntimeError(f"pass validation failed for {task_id}: {validation}")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "harness_ablation_runs" / "agentscope" / "closed_loop_demo",
    )
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--workers", type=int, default=3)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.iterations < 1 or args.workers < 1:
        raise SystemExit("--iterations and --workers must be positive")
    requested_output = args.output.expanduser()
    try:
        output = prepare_marked_output(
            requested_output,
            marker_name=".closed-loop-demo",
            marker_text="generated offline demo\n",
            replace=True,
        ).resolve()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    source = output / "reasoning-source"
    reasoning = output / "reasoning"

    jobs = [
        (case, iteration)
        for iteration in range(1, args.iterations + 1)
        for case in CASES
    ]
    receipts: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs))) as executor:
        futures = {
            executor.submit(_run_case, output, source, reasoning, case, iteration): (
                case,
                iteration,
            )
            for case, iteration in jobs
        }
        for future in as_completed(futures):
            receipts.append(future.result())

    summary = write_closed_loop_run(
        output,
        receipts,
        run_name=f"AgentScope-closed-loop-demo-{args.iterations}x",
    )
    domain_catalog = load_domain_profiles()
    demo_receipt = {
        "schema_version": "harness-core-closed-loop-demo/v1",
        "offline": True,
        "iterations": args.iterations,
        "workers": args.workers,
        "task_count": len(receipts),
        "expected_task_count": args.iterations * len(CASES),
        "reasoning_system_modified": False,
        "domain_profile_semantics": "coverage_prioritization_only",
        "domain_profiles": [
            {
                "code": profile.code,
                "known_v2_prefixes": profile.known_v2_prefixes,
                "priority_feature_ids": list(profile.priority_feature_ids),
            }
            for profile in domain_catalog.profiles
        ],
        "real_components": [
            "AgentScope runtime",
            "Feature switches",
            "Harbor filesystem bridge",
            "main_reasoning.simple_v2 validators",
            "passed-task Ex validator",
            "H-to-F evidence bridge",
            "score/trajectory/artifact comparator",
        ],
        "fixture_components": ["agent model", "reasoner model", "exact-content judge"],
        "summary": summary,
        "ok": (
            len(receipts) == args.iterations * len(CASES)
            and summary["comparison_status_counts"].get("supported", 0)
            == args.iterations * 2
            and all(summary["policy_checks"].values())
        ),
    }
    _write_json(output / "demo_receipt.json", demo_receipt)
    print(
        json.dumps(
            {
                "output": str(output),
                "task_count": summary["task_count"],
                "experiment_count": summary["experiment_count"],
                "comparison_status_counts": summary["comparison_status_counts"],
                "policy_checks": summary["policy_checks"],
                "ok": demo_receipt["ok"],
            },
            indent=2,
        )
    )
    return 0 if demo_receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
