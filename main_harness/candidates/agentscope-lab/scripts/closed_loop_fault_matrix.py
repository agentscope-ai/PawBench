#!/usr/bin/env python3
"""Stress the full H1-H5 closed loop with planted local faults.

The matrix reuses the real AgentScope runtime, Feature switches, Harbor bridge,
stable reasoning validators, H-to-F evidence bridge, and comparator. Only the
agent/reasoner models are deterministic fixtures. Passed, Ex-only, and M-only
cases are negative controls and must schedule no Feature experiment.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CANDIDATE_ROOT.parents[2]
for value in (
    PROJECT_ROOT,
    PROJECT_ROOT / "main_harness",
    CANDIDATE_ROOT / "src",
    Path(__file__).resolve().parent,
):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from agentscope.message import TextBlock, ToolCallBlock  # noqa: E402
from agentscope.model import ChatModelBase, ChatResponse  # noqa: E402

import closed_loop_demo as demo  # noqa: E402
from pawbench_agentscope._atomic_io import prepare_marked_output  # noqa: E402
from pawbench_agentscope.closed_loop import (  # noqa: E402
    RunObservation,
    build_ablation_plan,
    execute_task_plan,
    load_reasoning_outcome,
    observation_from_harbor,
    write_closed_loop_run,
)
from pawbench_agentscope.harbor_bridge import (  # noqa: E402
    build_feature_config,
    load_harbor_task_contract,
    run_harbor_task,
)
from pawbench_agentscope.harbor_contract import (  # noqa: E402
    validate_contract_directory,
)


@dataclass(frozen=True, slots=True)
class FaultCase:
    key: str
    demo_case: demo.DemoCase
    model_mode: str
    ablation_target: str | None = None


CASES = (
    FaultCase(
        key="pass_control",
        demo_case=demo.DemoCase(
            family="ua",
            observed_disabled_feature=None,
            reason_code=None,
            expected_feature=None,
            required_marker=None,
            harness_observation=(
                "The task, judge score, external resources, and output all passed."
            ),
        ),
        model_mode="correct",
    ),
    FaultCase(
        key="h1_workspace_binding",
        demo_case=demo.DemoCase(
            family="ws",
            observed_disabled_feature="F1.1",
            reason_code="H1",
            expected_feature="F1.1",
            required_marker=demo.WORKSPACE_MARKER,
            harness_observation=(
                "Workspace binding and the artifact path map were absent; the run "
                "used the wrong root."
            ),
        ),
        model_mode="marker",
    ),
    FaultCase(
        key="h2_tool_availability",
        demo_case=demo.DemoCase(
            family="ws",
            observed_disabled_feature="F2.2",
            reason_code="H2",
            expected_feature="F2.2",
            required_marker=None,
            harness_observation=(
                "The required run_shell tool was unavailable and hidden from the "
                "tool registry activation result."
            ),
        ),
        model_mode="correct",
        ablation_target="run_shell",
    ),
    FaultCase(
        key="h3_recovery_resume",
        demo_case=demo.DemoCase(
            family="ws",
            observed_disabled_feature="F3.3",
            reason_code="H3",
            expected_feature="F3.3",
            required_marker=None,
            harness_observation=(
                "The first recoverable verifier failure stopped without the bounded "
                "retry and repair attempt."
            ),
        ),
        model_mode="recover_on_second_attempt",
    ),
    FaultCase(
        key="h4_verification_gate",
        demo_case=demo.DemoCase(
            family="ws",
            observed_disabled_feature="F4.3",
            reason_code="H4",
            expected_feature="F4.3",
            required_marker=None,
            harness_observation=(
                "The verifier reported a content mismatch, but the disabled "
                "verification gate produced false-success acceptance."
            ),
        ),
        model_mode="always_wrong",
    ),
    FaultCase(
        key="h5_context_assembly",
        demo_case=demo.DemoCase(
            family="ma",
            observed_disabled_feature="F5.1",
            reason_code="H5",
            expected_feature="F5.1",
            required_marker=demo.CONTEXT_MARKER,
            harness_observation=(
                "Context assembly omitted the required SKILL.md context source and "
                "skill injection marker."
            ),
        ),
        model_mode="marker",
    ),
    FaultCase(
        key="ex3_external_control",
        demo_case=demo.DemoCase(
            family="ws",
            observed_disabled_feature=None,
            reason_code="Ex-3",
            expected_feature=None,
            required_marker=None,
            harness_observation=(
                "The external provider returned a persistent 503 service-unavailable "
                "record while the harness boundary remained healthy."
            ),
            cause_scope="external",
        ),
        model_mode="always_wrong",
    ),
    FaultCase(
        key="m2_model_control",
        demo_case=demo.DemoCase(
            family="ua",
            observed_disabled_feature=None,
            reason_code="M2",
            expected_feature=None,
            required_marker=None,
            harness_observation=(
                "The exact output instruction remained visible, but the model wrote "
                "the wrong content."
            ),
            cause_scope="model",
        ),
        model_mode="always_wrong",
    ),
)


def _has_tool_result(messages: list[Any]) -> bool:
    return any(
        any(getattr(block, "type", "") == "tool_result" for block in message.content)
        for message in messages
    )


class FaultMatrixModel(ChatModelBase):
    """Deterministic model that plants one controlled runtime condition."""

    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(
        self,
        *,
        mode: str,
        required_marker: str | None,
        model_name: str,
    ) -> None:
        super().__init__(
            credential=None,
            model=model_name,
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )
        self.mode = mode
        self.required_marker = required_marker
        self.agent_attempts = 0

    async def _call_api(
        self,
        model: str,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        tool_choice=None,
        **kwargs: Any,
    ) -> ChatResponse:
        del tools, tool_choice, kwargs
        if _has_tool_result(messages):
            return ChatResponse(
                content=[TextBlock(text="Recorded the artifact and stopped.")],
                is_last=True,
            )
        self.agent_attempts += 1
        prompt = "\n".join(str(message) for message in messages)
        if self.mode == "always_wrong":
            value = "wrong"
        elif self.mode == "recover_on_second_attempt":
            value = demo.EXPECTED if self.agent_attempts >= 2 else "wrong"
        elif self.mode == "marker":
            marker_present = (
                self.required_marker is None or self.required_marker in prompt
            )
            value = demo.EXPECTED if marker_present else "wrong"
        else:
            value = demo.EXPECTED
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id=f"fault-{model}-{self.agent_attempts}",
                    name="Bash",
                    input=json.dumps(
                        {
                            "command": (
                                f"printf {json.dumps(value)} > answer.txt"
                            ),
                            "description": "write planted fault-matrix artifact",
                        }
                    ),
                )
            ],
            is_last=True,
        )


def _run_variant(
    output: Path,
    task_id: str,
    case: FaultCase,
    variant: str,
    disabled_feature: str | None,
) -> RunObservation:
    root = output / "runs" / task_id / variant
    workspace = demo._fresh_workspace(root / "workspace", case.demo_case)
    logs = root / "logs" / "agent"
    contract = load_harbor_task_contract(
        workspace_root=workspace,
        task_id=task_id,
        instruction="Create answer.txt containing exactly: closed loop expected",
        required_artifacts=["/app/answer.txt", "/logs/agent/trajectory.json"],
    )
    contract.task.required_tools = ["run_shell"]
    contract.task.hidden_contract["artifact_text"] = {
        "answer.txt": demo.EXPECTED
    }
    disabled = [disabled_feature] if disabled_feature else []
    targets = (
        {disabled_feature: case.ablation_target}
        if disabled_feature and case.ablation_target
        else {}
    )
    config = build_feature_config(
        disabled_features=disabled,
        ablation_targets=targets,
        runtime_timeout_seconds=20.0,
    )
    result = run_harbor_task(
        contract,
        workspace_root=workspace,
        logs_dir=logs,
        feature_config=config,
        model=FaultMatrixModel(
            mode=case.model_mode,
            required_marker=case.demo_case.required_marker,
            model_name=f"fault-matrix-{case.key}-{variant}",
        ),
        model_name=f"offline-{case.key}",
        max_iters=4,
    )
    contract_receipt = validate_contract_directory(logs)
    if not contract_receipt["ok"]:
        raise RuntimeError(
            f"Harbor output contract failed for {task_id}/{variant}: "
            f"{contract_receipt['errors']}"
        )
    return observation_from_harbor(
        result,
        variant=variant,
        workspace_root=workspace,
        disabled_features=disabled,
    )


def _run_case(
    output: Path,
    source: Path,
    reasoning: Path,
    case: FaultCase,
    iteration: int,
) -> dict[str, Any]:
    family = case.demo_case.family
    task_id = f"{family}-{case.key.replace('_', '-')}-{iteration:03d}"
    observed = _run_variant(
        output,
        task_id,
        case,
        "observed",
        case.demo_case.observed_disabled_feature,
    )
    reasoning_recording = demo._write_reasoning_recording(
        source,
        reasoning,
        task_id,
        case.demo_case,
        observed,
    )
    if reasoning_recording.get("accepted") is not True:
        raise RuntimeError(
            f"reasoning validation rejected {task_id}: {reasoning_recording}"
        )
    outcome = load_reasoning_outcome(reasoning, source, task_id)
    plan = build_ablation_plan(outcome)

    def executor(variant: str, feature_id: str | None) -> RunObservation:
        return _run_variant(output, task_id, case, variant, feature_id)

    receipt = execute_task_plan(outcome, plan, observed, executor)
    expected_feature = case.demo_case.expected_feature
    expected_features = [expected_feature] if expected_feature else []
    if plan.get("recommended_feature_ids") != expected_features:
        raise RuntimeError(
            f"unexpected H-to-F plan for {task_id}: "
            f"{plan.get('recommended_feature_ids')}"
        )
    if expected_feature:
        statuses = [item.get("status") for item in receipt.get("comparisons", [])]
        if statuses != ["supported"]:
            raise RuntimeError(f"ablation did not support {task_id}: {statuses}")
    elif case.demo_case.reason_code is None:
        validation = outcome.get("pass_validation") or {}
        if validation.get("status") != "validated_pass":
            raise RuntimeError(f"pass validation failed for {task_id}: {validation}")
    else:
        if receipt.get("execution_status") != "not_applicable":
            raise RuntimeError(f"non-H control scheduled an experiment: {task_id}")
        if outcome.get("route") != "non_h_failure_no_ablation":
            raise RuntimeError(f"non-H control used wrong route: {task_id}")
    receipt["fault_ground_truth"] = {
        "case": case.key,
        "reason_code": case.demo_case.reason_code,
        "feature_id": expected_feature,
        "observed_disabled_feature": case.demo_case.observed_disabled_feature,
        "cause_scope": case.demo_case.cause_scope,
        "model_mode": case.model_mode,
    }
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            PROJECT_ROOT
            / "harness_ablation_runs"
            / "agentscope"
            / "closed_loop_fault_matrix"
        ),
    )
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.iterations < 1 or args.workers < 1:
        raise SystemExit("--iterations and --workers must be positive")
    requested_output = args.output.expanduser()
    try:
        output = prepare_marked_output(
            requested_output,
            marker_name=".closed-loop-fault-matrix",
            marker_text="generated offline fault matrix\n",
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
    recordings: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs))) as executor:
        futures = {
            executor.submit(
                _run_case,
                output,
                source,
                reasoning,
                case,
                iteration,
            ): (case, iteration)
            for case, iteration in jobs
        }
        for future in as_completed(futures):
            recordings.append(future.result())

    summary = write_closed_loop_run(
        output,
        recordings,
        run_name=f"AgentScope-closed-loop-fault-matrix-{args.iterations}x",
    )
    supported = summary["comparison_status_counts"].get("supported", 0)
    expected_supported = args.iterations * 5
    expected_tasks = args.iterations * len(CASES)
    control_tasks = [
        task
        for task in summary["tasks"]
        if not task["features"]
    ]
    receipt = {
        "schema_version": "harness-core-closed-loop-fault-matrix/v1",
        "offline": True,
        "iterations": args.iterations,
        "workers": args.workers,
        "task_count": summary["task_count"],
        "expected_task_count": expected_tasks,
        "h_codes_covered": ["H1", "H2", "H3", "H4", "H5"],
        "negative_controls": ["pass", "Ex-3", "M2"],
        "experiment_count": summary["experiment_count"],
        "supported_count": supported,
        "expected_supported_count": expected_supported,
        "control_task_count": len(control_tasks),
        "reasoning_system_modified": False,
        "summary": summary,
        "ok": (
            summary["task_count"] == expected_tasks
            and summary["experiment_count"] == expected_supported
            and supported == expected_supported
            and len(control_tasks) == args.iterations * 3
            and all(summary["policy_checks"].values())
        ),
    }
    demo._write_json(output / "fault_matrix_receipt.json", receipt)
    print(
        json.dumps(
            {
                "output": str(output),
                "task_count": receipt["task_count"],
                "h_codes_covered": receipt["h_codes_covered"],
                "negative_controls": receipt["negative_controls"],
                "experiment_count": receipt["experiment_count"],
                "supported_count": receipt["supported_count"],
                "policy_checks": summary["policy_checks"],
                "ok": receipt["ok"],
            },
            indent=2,
        )
    )
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
