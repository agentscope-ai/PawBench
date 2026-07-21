#!/usr/bin/env python3
"""Turn an existing final reasoning run into an evidence-gated ablation plan."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CANDIDATE_ROOT.parents[2]
for value in (PROJECT_ROOT, PROJECT_ROOT / "main_harness", CANDIDATE_ROOT / "src"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from pawbench_agentscope.closed_loop import (  # noqa: E402
    RunObservation,
    build_ablation_plan,
    load_reasoning_outcome,
    write_closed_loop_run,
)


def _task_ids(reasoning_root: Path, requested: Sequence[str] | None) -> list[str]:
    available = sorted(path.stem for path in (reasoning_root / "recordings").glob("*.json"))
    if not available:
        raise ValueError(f"no reasoning recordings found under {reasoning_root}")
    if not requested:
        return available
    missing = [task_id for task_id in requested if task_id not in available]
    if missing:
        raise ValueError("requested tasks are absent: " + ", ".join(missing))
    return list(dict.fromkeys(requested))


def build_plan_recording(outcome: dict[str, Any]) -> dict[str, Any]:
    plan = build_ablation_plan(outcome)
    passed = outcome.get("passed") is True
    observed = RunObservation(
        task_id=str(outcome["task_id"]),
        variant="reported_outcome",
        passed=passed,
        accepted=passed,
        verifier_ok=passed,
        score=float(outcome.get("score", 0.0)),
    )
    return {
        "schema_version": "harness-core-closed-loop/v1",
        "task_id": outcome["task_id"],
        "outcome": outcome,
        "ablation_plan": plan,
        "observed": observed.model_dump(mode="json"),
        "execution_status": "planned" if plan.get("experiments") else "not_applicable",
        "baseline": None,
        "feature_off_runs": [],
        "comparisons": [],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reasoning-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task-id", action="append")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    reasoning = args.reasoning_root.expanduser().resolve()
    source = args.source_root.expanduser().resolve()
    output = args.output.expanduser().absolute()
    if output.exists():
        raise SystemExit(f"output already exists: {output}")
    try:
        task_ids = _task_ids(reasoning, args.task_id)
        recordings = [
            build_plan_recording(
                load_reasoning_outcome(reasoning, source, task_id)
            )
            for task_id in task_ids
        ]
        summary = write_closed_loop_run(
            output,
            recordings,
            run_name=f"{reasoning.name}-closed-loop-plan",
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"planned {summary['planned_experiment_count']} experiment(s) for "
        f"{summary['task_count']} task(s) -> {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
