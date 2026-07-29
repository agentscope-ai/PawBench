#!/usr/bin/env python3
"""Opt compatible workspace (``ws-*``) tasks into OpenJudge."""

from __future__ import annotations

import argparse
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DATASET = Path("data/pawbenchv2_data_v1_0717/data_v1.0")
GENERATED_MARKER = (
    "# Generated from checklist.jsonl by scripts/convert_openjudge_graders.py."
)


@dataclass(frozen=True)
class AuditResult:
    task: str
    eligible: bool
    reason: str
    criteria: tuple[dict[str, Any], ...] = ()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_checklist(path: Path) -> tuple[dict[str, Any], ...]:
    criteria: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        item = json.loads(raw_line)
        if not isinstance(item, dict):
            raise ValueError(f"line {line_number} is not a JSON object")
        criterion_id = str(item.get("id", "")).strip()
        description = str(item.get("item", "")).strip()
        if not criterion_id or not description:
            raise ValueError(f"line {line_number} lacks id or item")
        if criterion_id in seen_ids:
            raise ValueError(f"duplicate criterion id {criterion_id!r}")
        seen_ids.add(criterion_id)
        weight = float(item.get("weight", 1.0))
        if weight <= 0:
            raise ValueError(f"criterion {criterion_id!r} has non-positive weight")
        criteria.append(
            {
                "id": criterion_id,
                "name": criterion_id.replace("_", " ").strip().title(),
                "description": description,
                "weight": weight,
            }
        )
    if not criteria:
        raise ValueError("checklist is empty")
    return tuple(criteria)


def audit_task(task_dir: Path) -> AuditResult:
    if not task_dir.name.startswith("ws-"):
        return AuditResult(task_dir.name, False, "not a workspace task")

    quality_dir = task_dir / "tests" / "quality"
    contract_path = quality_dir / "evaluation_contract.json"
    checklist_path = quality_dir / "checklist.jsonl"

    if not contract_path.is_file():
        return AuditResult(task_dir.name, False, "no LLM evaluation contract")
    try:
        contract = _load_json(contract_path)
    except (OSError, json.JSONDecodeError) as exc:
        return AuditResult(task_dir.name, False, f"invalid evaluation contract: {exc}")
    if not isinstance(contract, dict) or contract.get("eval_type") != "llm_judge":
        return AuditResult(task_dir.name, False, "evaluation is not llm_judge")
    if not checklist_path.is_file():
        return AuditResult(
            task_dir.name,
            False,
            "no checkpoint checklist; conversion would change grading semantics",
        )
    for required in ("prompt.txt", "expected_behavior.txt"):
        path = quality_dir / required
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            return AuditResult(task_dir.name, False, f"missing or empty {required}")
    try:
        criteria = _load_checklist(checklist_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return AuditResult(task_dir.name, False, f"invalid checklist: {exc}")
    return AuditResult(task_dir.name, True, "checkpoint-based LLM judge", criteria)


def _toml_string(value: str) -> str:
    # JSON strings use the same escaping needed by the TOML basic strings used here.
    return json.dumps(value, ensure_ascii=False)


def render_config(result: AuditResult) -> str:
    if not result.eligible:
        raise ValueError(f"{result.task} is not eligible: {result.reason}")
    lines = [
        "# Generated from checklist.jsonl by scripts/convert_openjudge_graders.py.",
        "# The source checklist remains the grading-contract source of truth.",
        "",
        "[judge]",
        'framework = "openjudge"',
        'judge = "claude-code"',
        'prompt_template = "prompt.txt"',
        'reference = "expected_behavior.txt"',
        "timeout = 900",
        "",
        "[scoring]",
        'aggregation = "weighted_mean"',
        "threshold = 0.8",
    ]
    for criterion in result.criteria:
        lines.extend(
            [
                "",
                "[[criterion]]",
                f'id = {_toml_string(criterion["id"])}',
                f'name = {_toml_string(criterion["name"])}',
                f'description = {_toml_string(criterion["description"])}',
                f'weight = {criterion["weight"]:g}',
            ]
        )
    rendered = "\n".join(lines) + "\n"
    tomllib.loads(rendered)
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", nargs="?", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write agent_judge.toml files; otherwise only audit",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing agent_judge.toml",
    )
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    task_dirs = sorted(path.parent for path in dataset.glob("*/task.toml"))
    results = [audit_task(task_dir) for task_dir in task_dirs]
    eligible = [result for result in results if result.eligible]
    excluded = [result for result in results if not result.eligible]
    written = 0
    removed = 0

    if args.apply:
        by_name = {task_dir.name: task_dir for task_dir in task_dirs}
        eligible_names = {result.task for result in eligible}
        for task_dir in task_dirs:
            destination = task_dir / "tests" / "quality" / "agent_judge.toml"
            if task_dir.name in eligible_names or not destination.is_file():
                continue
            existing_text = destination.read_text(encoding="utf-8")
            if existing_text.startswith(GENERATED_MARKER):
                destination.unlink()
                removed += 1
        for result in eligible:
            destination = (
                by_name[result.task] / "tests" / "quality" / "agent_judge.toml"
            )
            if destination.exists() and not args.overwrite:
                existing = tomllib.loads(destination.read_text(encoding="utf-8"))
                framework = str((existing.get("judge") or {}).get("framework", ""))
                if framework.strip().lower() != "openjudge":
                    raise RuntimeError(
                        f"refusing to replace non-OpenJudge config: {destination}"
                    )
                continue
            destination.write_text(render_config(result), encoding="utf-8")
            written += 1

    reason_counts: dict[str, int] = {}
    for result in excluded:
        reason_counts[result.reason] = reason_counts.get(result.reason, 0) + 1
    report = {
        "dataset": str(dataset),
        "tasks": len(results),
        "eligible": len(eligible),
        "excluded": len(excluded),
        "written": written,
        "removed": removed,
        "excluded_reason_counts": reason_counts,
        "excluded_tasks": [
            {"task": result.task, "reason": result.reason} for result in excluded
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
