#!/usr/bin/env python3
"""Exercise every Harbor error route through real Harness/AgentScope seams."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from agentscope.model import ChatModelBase


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_ROOT / "src"))

from pawbench_agentscope._atomic_io import (  # noqa: E402
    atomic_write_text,
    prepare_marked_output,
    read_text_no_follow,
)
from pawbench_agentscope._portable_security import redact_sensitive_text  # noqa: E402
from pawbench_agentscope.error_codes import ERROR_CODES, classify_bridge_error  # noqa: E402
from pawbench_agentscope.harbor_bridge import (  # noqa: E402
    RESULT_SCHEMA_VERSION,
    build_feature_config,
    load_harbor_task_contract,
    main as bridge_main,
    run_harbor_task,
)
from pawbench_agentscope.harbor_contract import validate_contract_directory  # noqa: E402
from pawbench_agentscope.trajectory_audit import analyze_native_trace, load_native_trace  # noqa: E402


SCHEMA_VERSION = "harness-core-runtime-failure-matrix/v1"
MARKER = ".harness-core-runtime-failure-matrix"

RUNTIME_CASES: tuple[dict[str, Any], ...] = (
    {
        "task_id": "runtime-provider-model-not-found",
        "cause_type": "NotFoundError",
        "message": "HTTP 404: requested model does not exist",
        "expected_code": "HC_PROVIDER_MODEL_NOT_FOUND",
    },
    {
        "task_id": "runtime-provider-auth",
        "cause_type": "PermissionDeniedError",
        "message": "HTTP 403 forbidden: provider authorization rejected",
        "expected_code": "HC_PROVIDER_AUTH",
    },
    {
        "task_id": "runtime-provider-rate-limit",
        "cause_type": "RateLimitError",
        "message": "HTTP 429 rate limit; Retry-After=1",
        "expected_code": "HC_PROVIDER_RATE_LIMIT",
    },
    {
        "task_id": "runtime-provider-unavailable",
        "cause_type": "APIConnectionError",
        "message": "TLS connection reset by external provider",
        "expected_code": "HC_PROVIDER_UNAVAILABLE",
    },
    {
        "task_id": "runtime-deadline-exception",
        "cause_type": "ExecutionDeadlineExceeded",
        "message": "Task execution exceeded 300s runtime budget",
        "expected_code": "HC_RUNTIME_TIMEOUT",
    },
    {
        "task_id": "runtime-generic-error",
        "cause_type": "SchedulerInvariantError",
        "message": "unexpected internal scheduler state",
        "expected_code": "HC_RUNTIME_ERROR",
    },
)


class FailureModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self, cause_type: str, message: str) -> None:
        super().__init__(
            credential=None,
            model="runtime-failure-fixture",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )
        self._exception_type = type(cause_type, (RuntimeError,), {})
        self._message = message

    async def _call_api(self, *args: Any, **kwargs: Any):
        _ = args, kwargs
        raise self._exception_type(self._message)


class SleepingModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="runtime-timeout-fixture",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(self, *args: Any, **kwargs: Any):
        _ = args, kwargs
        await asyncio.sleep(10)
        raise AssertionError("sleeping fixture should have been cancelled")


def _atomic_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(
        read_text_no_follow(path, max_bytes=8 * 1024 * 1024),
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_nonfinite,
    )
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _prepare_output(root: Path, *, fresh: bool) -> None:
    prepare_marked_output(
        root,
        marker_name=MARKER,
        marker_text=SCHEMA_VERSION + "\n",
        replace=fresh,
    )


def _runtime_context(trace_path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not trace_path.exists():
        return {}, None
    rows, _ = load_native_trace(trace_path)
    runtime = next(
        (
            dict(row["payload"])
            for row in reversed(rows)
            if row.get("type") == "runtime_error" and isinstance(row.get("payload"), dict)
        ),
        {},
    )
    return runtime, analyze_native_trace(rows)


def _materialize_runtime_error(
    *,
    case: dict[str, Any],
    logs: Path,
    caught: Exception,
) -> dict[str, Any]:
    trace_path = logs / "harness-core-trace.jsonl"
    runtime_context, shadow = _runtime_context(trace_path)
    error = redact_sensitive_text(str(caught))
    classification = classify_bridge_error(
        error_type=type(caught).__name__,
        error=error,
        runtime_context=runtime_context,
    )
    envelope = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "task_id": case["task_id"],
        "success": False,
        "error_type": type(caught).__name__,
        "error": error,
        **classification.model_dump(mode="json"),
    }
    _atomic_json(logs / "result.json", envelope)
    if shadow is not None:
        _atomic_json(logs / "trajectory-shadow.json", shadow)
    flagged = shadow["summary"]["flagged_checks"] if shadow else []
    expected_flags: list[str] = []
    return {
        "task_id": case["task_id"],
        "mode": "real_agentscope_exception",
        "expected_code": case["expected_code"],
        "observed_code": classification.error_code,
        "matched": classification.error_code == case["expected_code"],
        "failure_scope": classification.failure_scope,
        "retryable": classification.retryable,
        "cause_type": classification.cause_type,
        "runtime_trace_cause_type": runtime_context.get("error_type"),
        "contract_valid": validate_contract_directory(logs)["ok"],
        "shadow_flagged_checks": flagged,
        "shadow_expected_flags": expected_flags,
        "shadow_expectation_matched": flagged == expected_flags,
    }


def _run_boundary_cli_cases(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    cases = (
        (
            "boundary-invalid-feature",
            "HC_CONFIG_INVALID_FEATURE",
            ["--instruction", "x", "--disable-feature", "F9.9"],
        ),
        (
            "boundary-missing-instruction",
            "HC_INPUT_CONTRACT_INVALID",
            ["--instruction-file", str(root / "does-not-exist.md")],
        ),
    )
    for task_id, expected_code, extra in cases:
        case_root = root / "cases" / task_id
        workspace = case_root / "workspace"
        logs = case_root / "logs" / "agent"
        workspace.mkdir(parents=True)
        exit_code = bridge_main(
            [
                "--task-id",
                task_id,
                "--workspace-root",
                str(workspace),
                "--logs-dir",
                str(logs),
                *extra,
            ]
        )
        envelope = _load_json_object(logs / "result.json")
        records.append(
            {
                "task_id": task_id,
                "mode": "real_bridge_cli_validation",
                "expected_code": expected_code,
                "observed_code": envelope.get("error_code"),
                "matched": exit_code == 1 and envelope.get("error_code") == expected_code,
                "failure_scope": envelope.get("failure_scope"),
                "retryable": envelope.get("retryable"),
                "cause_type": envelope.get("cause_type"),
                "contract_valid": validate_contract_directory(logs)["ok"],
                "shadow_flagged_checks": [],
                "shadow_expected_flags": [],
                "shadow_expectation_matched": True,
            }
        )
    return records


def _run_preflight_case(root: Path) -> dict[str, Any]:
    case = {
        "task_id": "runtime-preflight-failed",
        "expected_code": "HC_PREFLIGHT_FAILED",
    }
    case_root = root / "cases" / case["task_id"]
    workspace = case_root / "workspace"
    logs = case_root / "logs" / "agent"
    workspace.mkdir(parents=True)
    contract = load_harbor_task_contract(
        workspace_root=workspace,
        task_id=case["task_id"],
        instruction="exercise preflight",
    )
    contract.task.required_binaries = ["harness-core-definitely-missing-binary"]
    try:
        run_harbor_task(
            contract,
            workspace_root=workspace,
            logs_dir=logs,
            feature_config=build_feature_config(runtime_timeout_seconds=5),
            model=FailureModel("UnusedError", "must not reach model"),
            max_iters=2,
        )
    except Exception as exc:
        return _materialize_runtime_error(case=case, logs=logs, caught=exc)
    raise AssertionError("preflight fixture unexpectedly succeeded")


def _run_exception_case(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    case_root = root / "cases" / case["task_id"]
    workspace = case_root / "workspace"
    logs = case_root / "logs" / "agent"
    workspace.mkdir(parents=True)
    contract = load_harbor_task_contract(
        workspace_root=workspace,
        task_id=case["task_id"],
        instruction="exercise one runtime exception",
    )
    try:
        run_harbor_task(
            contract,
            workspace_root=workspace,
            logs_dir=logs,
            feature_config=build_feature_config(runtime_timeout_seconds=5),
            model=FailureModel(case["cause_type"], case["message"]),
            max_iters=2,
        )
    except Exception as exc:
        return _materialize_runtime_error(case=case, logs=logs, caught=exc)
    raise AssertionError(f"{case['task_id']} unexpectedly succeeded")


def _run_completed_timeout_case(root: Path) -> dict[str, Any]:
    task_id = "runtime-native-timeout-outcome"
    case_root = root / "cases" / task_id
    workspace = case_root / "workspace"
    logs = case_root / "logs" / "agent"
    workspace.mkdir(parents=True)
    contract = load_harbor_task_contract(
        workspace_root=workspace,
        task_id=task_id,
        instruction="exercise the native runtime timeout outcome",
    )
    result = run_harbor_task(
        contract,
        workspace_root=workspace,
        logs_dir=logs,
        feature_config=build_feature_config(runtime_timeout_seconds=1),
        model=SleepingModel(),
        max_iters=2,
    )
    rows, _ = load_native_trace(logs / "harness-core-trace.jsonl")
    shadow = analyze_native_trace(rows)
    _atomic_json(logs / "trajectory-shadow.json", shadow)
    completion = next(
        dict(row["payload"])
        for row in reversed(rows)
        if row.get("type") == "completion_decision"
    )
    flagged = shadow["summary"]["flagged_checks"]
    expected_flags = ["empty_model_output"]
    return {
        "task_id": task_id,
        "mode": "completed_native_timeout_outcome",
        "expected_code": None,
        "observed_code": None,
        "matched": (
            result.success is True
            and result.accepted is False
            and completion.get("stop_reason") == "runtime_timeout"
        ),
        "failure_scope": "evaluable_agent_outcome",
        "retryable": None,
        "cause_type": "runtime_timeout",
        "contract_valid": validate_contract_directory(logs)["ok"],
        "shadow_flagged_checks": flagged,
        "shadow_expected_flags": expected_flags,
        "shadow_expectation_matched": flagged == expected_flags,
        "completion_decision": completion,
    }


def run_matrix(output: Path, *, fresh: bool) -> dict[str, Any]:
    root = output.expanduser()
    _prepare_output(root, fresh=fresh)
    records = _run_boundary_cli_cases(root)
    records.append(_run_preflight_case(root))
    records.extend(_run_exception_case(root, dict(case)) for case in RUNTIME_CASES)
    records.append(_run_completed_timeout_case(root))
    expected_codes = {record["expected_code"] for record in records if record["expected_code"]}
    summary = {
        "schema_version": SCHEMA_VERSION,
        "case_count": len(records),
        "coded_failure_count": len(expected_codes),
        "all_stable_codes_exercised": expected_codes == ERROR_CODES,
        "matched_count": sum(bool(record["matched"]) for record in records),
        "contract_valid_count": sum(bool(record["contract_valid"]) for record in records),
        "shadow_expectation_matched_count": sum(
            bool(record["shadow_expectation_matched"]) for record in records
        ),
        "native_timeout_boundary": (
            "An internally handled runtime timeout is a completed, rejected agent outcome; "
            "it is not converted into a process-error code."
        ),
        "records": records,
    }
    _atomic_json(root / "summary.json", summary)
    rows = "\n".join(
        f"| `{record['task_id']}` | `{record['observed_code'] or 'none'}` | "
        f"{'yes' if record['matched'] else 'no'} | {'yes' if record['contract_valid'] else 'no'} | "
        f"{'yes' if record['shadow_expectation_matched'] else 'no'} |"
        for record in records
    )
    atomic_write_text(
        root / "REPORT_EN.md",
        "# Real Harness-side Failure Matrix\n\n"
        "These cases execute real bridge validation, preflight, AgentScope model-exception, "
        "and native timeout paths. The native timeout remains an evaluable rejected outcome.\n\n"
        "| Case | Code | Matched | Contract valid | Shadow expected |\n"
        "| --- | --- | ---: | ---: | ---: |\n"
        f"{rows}\n",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("harness_ablation_runs/agentscope/runtime_failure_matrix_20260716"),
    )
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run_matrix(args.output, fresh=args.fresh)
    except (OSError, ValueError, AssertionError, RuntimeError) as exc:
        parser.error(redact_sensitive_text(str(exc)))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if (
        summary["matched_count"] == summary["case_count"]
        and summary["contract_valid_count"] == summary["case_count"]
        and summary["shadow_expectation_matched_count"] == summary["case_count"]
        and summary["all_stable_codes_exercised"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
