#!/usr/bin/env python3
"""Materialize the complete Harbor boundary error-code matrix offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CANDIDATE_ROOT.parents[2]
for value in (PROJECT_ROOT / "main_harness", CANDIDATE_ROOT / "src"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from pawbench_agentscope.error_codes import classify_bridge_error
from pawbench_agentscope._atomic_io import atomic_write_text, prepare_marked_output
from pawbench_agentscope.harbor_bridge import RESULT_SCHEMA_VERSION
from pawbench_agentscope.harbor_contract import validate_result_contract


MATRIX_SCHEMA_VERSION = "harness-core-error-code-matrix/v1"
MARKER = ".harness-core-error-code-matrix"

CASES: tuple[dict[str, Any], ...] = (
    {
        "task_id": "error-config-invalid-feature",
        "error_type": "ValueError",
        "error": "unknown Feature IDs: ['F9.9']",
        "expected_code": "HC_CONFIG_INVALID_FEATURE",
    },
    {
        "task_id": "error-input-contract",
        "error_type": "FileNotFoundError",
        "error": "instruction file is absent",
        "expected_code": "HC_INPUT_CONTRACT_INVALID",
    },
    {
        "task_id": "error-preflight",
        "error_type": "RuntimeError",
        "error": "Preflight failed: workspace is not writable",
        "expected_code": "HC_PREFLIGHT_FAILED",
    },
    {
        "task_id": "error-provider-model",
        "error_type": "RuntimeError",
        "error": "AgentScope runtime failed",
        "runtime_context": {
            "error_type": "NotFoundError",
            "error": "Error code: 404; code=model_not_found",
        },
        "expected_code": "HC_PROVIDER_MODEL_NOT_FOUND",
    },
    {
        "task_id": "error-provider-auth",
        "error_type": "PermissionDeniedError",
        "error": "Error code: 403 forbidden",
        "expected_code": "HC_PROVIDER_AUTH",
    },
    {
        "task_id": "error-provider-rate",
        "error_type": "RateLimitError",
        "error": "Error code: 429 rate limit",
        "expected_code": "HC_PROVIDER_RATE_LIMIT",
    },
    {
        "task_id": "error-provider-unavailable",
        "error_type": "APIConnectionError",
        "error": "connection reset",
        "expected_code": "HC_PROVIDER_UNAVAILABLE",
    },
    {
        "task_id": "error-runtime-timeout",
        "error_type": "TimeoutError",
        "error": "runtime timeout",
        "expected_code": "HC_RUNTIME_TIMEOUT",
    },
    {
        "task_id": "error-runtime-generic",
        "error_type": "RuntimeError",
        "error": "unexpected scheduler state",
        "expected_code": "HC_RUNTIME_ERROR",
    },
)


def _prepare_output(root: Path) -> None:
    prepare_marked_output(
        root,
        marker_name=MARKER,
        marker_text=MATRIX_SCHEMA_VERSION + "\n",
        replace=False,
    )


def run_matrix(output: Path) -> dict[str, Any]:
    # Preserve the final path component until ownership is checked. Resolving
    # first would turn an output symlink into its target and bypass the guard.
    root = output.expanduser().absolute()
    _prepare_output(root)
    root = root.resolve()
    records: list[dict[str, Any]] = []
    for case in CASES:
        classification = classify_bridge_error(
            error_type=case["error_type"],
            error=case["error"],
            runtime_context=case.get("runtime_context"),
        )
        if classification.error_code != case["expected_code"]:
            raise AssertionError(
                f"{case['task_id']}: expected {case['expected_code']}, "
                f"got {classification.error_code}"
            )
        envelope = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "task_id": case["task_id"],
            "success": False,
            "error_type": case["error_type"],
            "error": case["error"],
            **classification.model_dump(mode="json"),
        }
        errors = validate_result_contract(envelope)
        if errors:
            raise AssertionError(f"{case['task_id']}: invalid envelope: {errors}")
        case_dir = root / "cases" / classification.error_code
        case_dir.mkdir(parents=True, exist_ok=True)
        result_path = case_dir / "result.json"
        atomic_write_text(
            result_path,
            json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
        )
        records.append(
            {
                "task_id": case["task_id"],
                "error_code": classification.error_code,
                "failure_scope": classification.failure_scope,
                "retryable": classification.retryable,
                "cause_type": classification.cause_type,
                "result": str(result_path.relative_to(root)),
                "contract_valid": True,
            }
        )

    summary = {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "case_count": len(records),
        "all_codes_covered": len({record["error_code"] for record in records}) == 9,
        "contract_valid_count": sum(record["contract_valid"] for record in records),
        "retryable_count": sum(record["retryable"] for record in records),
        "records": records,
    }
    atomic_write_text(
        root / "summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    rows = "\n".join(
        f"| `{record['error_code']}` | {record['failure_scope']} | "
        f"{'yes' if record['retryable'] else 'no'} | `{record['cause_type']}` |"
        for record in records
    )
    report = (
        "# Harness-core Harbor Error-Code Matrix\n\n"
        "All nine boundary codes were materialized as contract-valid error "
        "envelopes. This is an offline routing test; it does not claim that "
        "every external provider failure was reproduced live.\n\n"
        "| Code | Scope | Retry | Cause |\n"
        "| --- | --- | --- | --- |\n"
        f"{rows}\n"
    )
    atomic_write_text(root / "REPORT_EN.md", report)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("harness_ablation_runs/agentscope/error_code_matrix_20260716"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    summary = run_matrix(args.output)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
