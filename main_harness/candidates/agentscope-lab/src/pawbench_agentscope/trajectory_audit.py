"""Deterministic, replayable shadow audit for Harness-core native traces.

The audit produces anomaly leads only.  It cannot change a benchmark score,
verifier result, completion decision, or H/Ex/M attribution.
"""

from __future__ import annotations

import hashlib
import json
import math
import stat
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from pawbench_agentscope._atomic_io import atomic_write_text, read_text_no_follow


AUDIT_SCHEMA_VERSION = "harness-core-trajectory-shadow/v1"
MAX_TRACE_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_ITEMS = 50
TRUNCATION_REASONS = {"length", "max_tokens", "max_output_tokens"}


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signature(tool_name: str, arguments: str) -> str:
    value: Any = arguments.strip()
    try:
        value = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        pass
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{tool_name}\0{canonical}".encode("utf-8")).hexdigest()


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def load_native_trace(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load a bounded regular JSONL trace without following a final symlink."""

    if path.is_symlink():
        raise ValueError(f"trace path must not be a symlink: {path}")
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"trace path must be a regular file: {path}")
    if metadata.st_size > MAX_TRACE_BYTES:
        raise ValueError(f"trace exceeds {MAX_TRACE_BYTES} bytes: {path}")
    text = read_text_no_follow(path, max_bytes=MAX_TRACE_BYTES)
    encoded = text.encode("utf-8")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(
                line,
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_nonfinite,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid trace JSON at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"trace row must be an object at {path}:{line_number}")
        rows.append(value)
    return rows, {
        "path": str(path),
        "size": len(encoded),
        "sha256": _sha256_bytes(encoded),
    }


def analyze_native_trace(
    rows: Sequence[Mapping[str, Any]],
    *,
    latency_threshold_seconds: float = 120.0,
    consecutive_tool_threshold: int = 3,
) -> dict[str, Any]:
    """Return a non-authoritative anomaly receipt for native trace rows."""

    if (
        isinstance(latency_threshold_seconds, bool)
        or not isinstance(latency_threshold_seconds, (int, float))
        or not math.isfinite(latency_threshold_seconds)
        or latency_threshold_seconds <= 0
    ):
        raise ValueError("latency_threshold_seconds must be finite and positive")
    if (
        isinstance(consecutive_tool_threshold, bool)
        or not isinstance(consecutive_tool_threshold, int)
        or consecutive_tool_threshold < 2
    ):
        raise ValueError("consecutive_tool_threshold must be at least 2")

    integrity_issues: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    task_ids: set[str] = set()
    outer_types: list[str] = []
    outer_positions: dict[str, list[int]] = {}
    previous_event_id: str | None = None
    seen_event_ids: set[str] = set()
    expected_index = 1

    active_models: dict[str, dict[str, Any]] = {}
    model_calls: list[dict[str, Any]] = []
    tool_calls: dict[str, dict[str, Any]] = {}
    tool_call_counts: dict[str, int] = {}
    active_tool_call_ids: dict[str, str] = {}
    tool_results_started: set[str] = set()
    tool_results_finished: set[str] = set()
    orphan_model_ends: list[int] = []
    orphan_tool_ends: list[int] = []
    orphan_result_starts: list[int] = []
    orphan_result_ends: list[int] = []

    for position, raw_row in enumerate(rows, start=1):
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"trace row {position} must be an object")
        row = dict(raw_row)
        event_index = row.get("event_index")
        event_id = row.get("event_id")
        parent_event_id = row.get("parent_event_id")
        if (
            isinstance(event_index, bool)
            or not isinstance(event_index, int)
            or event_index != expected_index
        ):
            integrity_issues.append(
                {"kind": "event_index", "position": position, "observed": event_index, "expected": expected_index}
            )
        expected_index += 1
        if not isinstance(event_id, str) or not event_id:
            integrity_issues.append({"kind": "event_id", "position": position})
        elif event_id in seen_event_ids:
            integrity_issues.append(
                {"kind": "duplicate_event_id", "position": position, "event_id": event_id}
            )
        else:
            seen_event_ids.add(event_id)
        run_id = row.get("run_id")
        task_id = row.get("task_id")
        if not isinstance(run_id, str) or not run_id:
            integrity_issues.append({"kind": "run_id", "position": position})
        else:
            run_ids.add(run_id)
            if isinstance(event_index, int) and not isinstance(event_index, bool):
                expected_event_id = f"{run_id}:{event_index}"
                if event_id != expected_event_id:
                    integrity_issues.append(
                        {
                            "kind": "event_id_binding",
                            "position": position,
                            "observed": event_id,
                            "expected": expected_event_id,
                        }
                    )
        if not isinstance(task_id, str) or not task_id:
            integrity_issues.append({"kind": "task_id", "position": position})
        else:
            task_ids.add(task_id)
        if _timestamp(row.get("timestamp")) is None:
            integrity_issues.append({"kind": "timestamp", "position": position})
        if position == 1:
            if parent_event_id is not None:
                integrity_issues.append({"kind": "first_parent", "position": position})
        elif parent_event_id != previous_event_id:
            integrity_issues.append({"kind": "parent_chain", "position": position})
        previous_event_id = event_id if isinstance(event_id, str) else None

        raw_outer_type = row.get("type")
        if not isinstance(raw_outer_type, str) or not raw_outer_type:
            integrity_issues.append({"kind": "event_type", "position": position})
            outer_type = ""
        else:
            outer_type = raw_outer_type
        outer_types.append(outer_type)
        outer_positions.setdefault(outer_type, []).append(position)
        if not isinstance(row.get("payload"), Mapping):
            integrity_issues.append({"kind": "payload", "position": position})
        if outer_type != "agentscope_event":
            continue
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            integrity_issues.append({"kind": "agentscope_payload", "position": position})
            continue
        event_type = str(payload.get("type") or "")
        reply_id = str(payload.get("reply_id") or "")

        if event_type == "MODEL_CALL_START":
            if not reply_id:
                integrity_issues.append({"kind": "model_start_without_reply_id", "position": position})
                continue
            if reply_id in active_models:
                integrity_issues.append({"kind": "overlapping_model_call", "position": position})
            active_models[reply_id] = {
                "start_position": position,
                "started_at": _timestamp(payload.get("created_at")) or _timestamp(row.get("timestamp")),
                "output_chars": 0,
                "tool_call_ids": [],
            }
        elif event_type.endswith("_BLOCK_DELTA"):
            active = active_models.get(reply_id)
            if active is not None:
                active["output_chars"] += len(str(payload.get("delta") or ""))
        elif event_type == "TOOL_CALL_START":
            raw_call_id = str(payload.get("tool_call_id") or "")
            if not raw_call_id:
                integrity_issues.append({"kind": "tool_start_without_call_id", "position": position})
                continue
            previous_call_id = active_tool_call_ids.get(raw_call_id)
            if (
                previous_call_id in tool_calls
                and previous_call_id not in tool_results_finished
            ):
                integrity_issues.append(
                    {
                        "kind": "overlapping_tool_call_id",
                        "position": position,
                        "tool_call_id": raw_call_id,
                        "previous_call_id": previous_call_id,
                    }
                )
            occurrence = tool_call_counts.get(raw_call_id, 0) + 1
            tool_call_counts[raw_call_id] = occurrence
            call_id = raw_call_id if occurrence == 1 else f"{raw_call_id}:{occurrence}"
            active_tool_call_ids[raw_call_id] = call_id
            tool_calls[call_id] = {
                "start_position": position,
                "reply_id": reply_id,
                "name": str(payload.get("tool_call_name") or ""),
                "arguments": "",
                "ended": False,
            }
            if reply_id in active_models:
                active_models[reply_id]["tool_call_ids"].append(call_id)
        elif event_type == "TOOL_CALL_DELTA":
            raw_call_id = str(payload.get("tool_call_id") or "")
            call_id = active_tool_call_ids.get(raw_call_id, "")
            if call_id in tool_calls:
                tool_calls[call_id]["arguments"] += str(payload.get("delta") or "")
        elif event_type == "TOOL_CALL_END":
            raw_call_id = str(payload.get("tool_call_id") or "")
            call_id = active_tool_call_ids.get(raw_call_id, "")
            if call_id in tool_calls:
                tool_calls[call_id]["ended"] = True
                tool_calls[call_id]["end_position"] = position
            else:
                orphan_tool_ends.append(position)
        elif event_type == "TOOL_RESULT_START":
            raw_call_id = str(payload.get("tool_call_id") or "")
            call_id = active_tool_call_ids.get(raw_call_id, "")
            if call_id in tool_calls:
                tool_results_started.add(call_id)
            else:
                orphan_result_starts.append(position)
        elif event_type == "TOOL_RESULT_END":
            raw_call_id = str(payload.get("tool_call_id") or "")
            call_id = active_tool_call_ids.get(raw_call_id, "")
            if call_id in tool_results_started:
                tool_results_finished.add(call_id)
            else:
                orphan_result_ends.append(position)
        elif event_type == "MODEL_CALL_END":
            active = active_models.pop(reply_id, None)
            if active is None:
                orphan_model_ends.append(position)
                continue
            finished_at = _timestamp(payload.get("created_at")) or _timestamp(row.get("timestamp"))
            started_at = active.get("started_at")
            duration = None
            if isinstance(started_at, datetime) and isinstance(finished_at, datetime):
                try:
                    duration = max(0.0, (finished_at - started_at).total_seconds())
                except TypeError:
                    duration = None
            model_calls.append(
                {
                    "start_position": active["start_position"],
                    "end_position": position,
                    "finished_reason": str(payload.get("finished_reason") or ""),
                    "output_chars": int(active["output_chars"]),
                    "tool_call_count": len(active["tool_call_ids"]),
                    "duration_seconds": duration,
                }
            )

    if len(run_ids) > 1:
        integrity_issues.append({"kind": "multiple_run_ids", "count": len(run_ids)})
    if len(task_ids) > 1:
        integrity_issues.append({"kind": "multiple_task_ids", "count": len(task_ids)})
    has_run_abort = "run_abort" in outer_positions
    has_runtime_error = "runtime_error" in outer_positions
    has_normal_completion = bool(
        {"verifier_result", "completion_decision"} & set(outer_positions)
    )
    if has_run_abort and has_runtime_error:
        integrity_issues.append(
            {"kind": "conflicting_terminal_events", "events": ["run_abort", "runtime_error"]}
        )
    if (has_run_abort or has_runtime_error) and has_normal_completion:
        integrity_issues.append(
            {
                "kind": "conflicting_terminal_events",
                "events": sorted(
                    {"run_abort", "runtime_error", "verifier_result", "completion_decision"}
                    & set(outer_positions)
                ),
            }
        )

    if has_run_abort and not has_runtime_error and not has_normal_completion:
        terminal_mode = "preflight_abort"
        required_outer = {"run_abort"}
        terminal_position = outer_positions["run_abort"][-1]
    elif has_runtime_error and not has_normal_completion:
        terminal_mode = "runtime_error"
        required_outer = {"run_start", "runtime_error"}
        terminal_position = outer_positions["runtime_error"][-1]
    else:
        terminal_mode = "completed"
        required_outer = {"run_start", "verifier_result", "completion_decision"}
        terminal_position = None
    missing_outer = sorted(required_outer - set(outer_types))
    if missing_outer:
        integrity_issues.append({"kind": "missing_outer_events", "events": missing_outer})

    truncated = [
        {
            "end_position": item["end_position"],
            "finished_reason": item["finished_reason"],
        }
        for item in model_calls
        if item["finished_reason"].strip().lower() in TRUNCATION_REASONS
    ]
    empty_calls = [
        {"end_position": item["end_position"]}
        for item in model_calls
        if item["output_chars"] == 0 and item["tool_call_count"] == 0
    ]
    latency_spikes = [
        {
            "end_position": item["end_position"],
            "duration_seconds": round(float(item["duration_seconds"]), 3),
        }
        for item in model_calls
        if item["duration_seconds"] is not None
        and float(item["duration_seconds"]) > latency_threshold_seconds
    ]

    ordered_tool_calls = sorted(tool_calls.items(), key=lambda item: int(item[1]["start_position"]))
    signatures = [
        (_signature(str(value["name"]), str(value["arguments"])), call_id, value)
        for call_id, value in ordered_tool_calls
    ]
    repeated_runs: list[dict[str, Any]] = []
    start = 0
    while start < len(signatures):
        end = start + 1
        while end < len(signatures) and signatures[end][0] == signatures[start][0]:
            end += 1
        if end - start >= consecutive_tool_threshold:
            repeated_runs.append(
                {
                    "tool_name": signatures[start][2]["name"],
                    "signature_sha256": signatures[start][0],
                    "count": end - start,
                    "start_position": signatures[start][2]["start_position"],
                    "call_ids": [item[1] for item in signatures[start:end]][:MAX_EVIDENCE_ITEMS],
                }
            )
        start = end

    def terminated_before_outer(position: int) -> bool:
        return terminal_position is not None and position < terminal_position

    open_model_positions = [item["start_position"] for item in active_models.values()]
    open_tool_items = {
        call_id: value for call_id, value in tool_calls.items() if not value.get("ended")
    }
    unfinished_results = tool_results_started - tool_results_finished
    calls_without_result = set(tool_calls) - tool_results_finished
    aborted_model_calls = sorted(
        position for position in open_model_positions if terminated_before_outer(int(position))
    )
    aborted_tool_calls = sorted(
        call_id
        for call_id, value in open_tool_items.items()
        if terminated_before_outer(int(value["start_position"]))
    )
    aborted_tool_results = sorted(
        call_id
        for call_id in unfinished_results
        if terminated_before_outer(int(tool_calls[call_id]["start_position"]))
    )
    aborted_calls_without_result = sorted(
        call_id
        for call_id in calls_without_result
        if terminated_before_outer(int(tool_calls[call_id]["start_position"]))
    )

    lifecycle_evidence = {
        "open_model_calls": sorted(set(open_model_positions) - set(aborted_model_calls)),
        "open_tool_calls": sorted(
            value["start_position"]
            for call_id, value in open_tool_items.items()
            if call_id not in set(aborted_tool_calls)
        ),
        "tool_results_without_end": sorted(unfinished_results - set(aborted_tool_results)),
        "tool_calls_without_result": sorted(calls_without_result - set(aborted_calls_without_result)),
        "orphan_model_ends": orphan_model_ends,
        "orphan_tool_ends": orphan_tool_ends,
        "orphan_result_starts": orphan_result_starts,
        "orphan_result_ends": orphan_result_ends,
        "terminated_model_calls": aborted_model_calls,
        "terminated_tool_calls": aborted_tool_calls,
        "terminated_tool_results": aborted_tool_results,
        "terminated_calls_without_result": aborted_calls_without_result,
    }
    lifecycle_flagged = any(
        bool(lifecycle_evidence[name])
        for name in (
            "open_model_calls",
            "open_tool_calls",
            "tool_results_without_end",
            "tool_calls_without_result",
            "orphan_model_ends",
            "orphan_tool_ends",
            "orphan_result_starts",
            "orphan_result_ends",
        )
    )

    checks = {
        "trace_integrity": {
            "flagged": bool(integrity_issues),
            "issue_count": len(integrity_issues),
            "evidence": integrity_issues[:MAX_EVIDENCE_ITEMS],
        },
        "model_truncation": {
            "flagged": bool(truncated),
            "count": len(truncated),
            "evidence": truncated[:MAX_EVIDENCE_ITEMS],
        },
        "empty_model_output": {
            "flagged": bool(empty_calls),
            "count": len(empty_calls),
            "evidence": empty_calls[:MAX_EVIDENCE_ITEMS],
        },
        "consecutive_tool_repetition": {
            "flagged": bool(repeated_runs),
            "count": len(repeated_runs),
            "evidence": repeated_runs[:MAX_EVIDENCE_ITEMS],
        },
        "model_latency_spike": {
            "flagged": bool(latency_spikes),
            "count": len(latency_spikes),
            "evidence": latency_spikes[:MAX_EVIDENCE_ITEMS],
        },
        "event_lifecycle": {
            "flagged": lifecycle_flagged,
            "evidence": lifecycle_evidence,
        },
    }
    flagged_checks = sorted(name for name, value in checks.items() if value["flagged"])
    finished_reason_counts = Counter(
        str(item["finished_reason"] or "unknown") for item in model_calls
    )
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "authority": {
            "mode": "shadow_non_authoritative",
            "canonical_score_modified": False,
            "canonical_verdict_modified": False,
            "causal_attribution_allowed": False,
        },
        "source": {
            "run_id": next(iter(run_ids), None),
            "task_id": next(iter(task_ids), None),
            "event_count": len(rows),
            "terminal_mode": terminal_mode,
        },
        "configuration": {
            "latency_threshold_seconds": latency_threshold_seconds,
            "consecutive_tool_threshold": consecutive_tool_threshold,
        },
        "metrics": {
            "model_call_count": len(model_calls),
            "tool_call_count": len(tool_calls),
            "finished_reason_counts": dict(sorted(finished_reason_counts.items())),
        },
        "checks": checks,
        "summary": {
            "flagged_checks": flagged_checks,
            "anomaly_count": len(flagged_checks),
            "manual_review_recommended": bool(flagged_checks),
        },
    }


def audit_trace_file(
    trace_path: Path,
    *,
    output_path: Path | None = None,
    latency_threshold_seconds: float = 120.0,
    consecutive_tool_threshold: int = 3,
) -> dict[str, Any]:
    rows, receipt = load_native_trace(trace_path)
    audit = analyze_native_trace(
        rows,
        latency_threshold_seconds=latency_threshold_seconds,
        consecutive_tool_threshold=consecutive_tool_threshold,
    )
    audit["source"].update(receipt)
    if output_path is not None:
        atomic_write_text(
            output_path,
            json.dumps(
                audit,
                ensure_ascii=False,
                indent=2,
                default=str,
                allow_nan=False,
            )
            + "\n",
        )
    return audit


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "MAX_TRACE_BYTES",
    "analyze_native_trace",
    "audit_trace_file",
    "load_native_trace",
]
