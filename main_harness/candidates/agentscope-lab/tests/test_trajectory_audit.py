from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pawbench_agentscope import trajectory_audit


def _rows(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (event_type, payload) in enumerate(events, start=1):
        rows.append(
            {
                "run_id": "run-1",
                "task_id": "task-1",
                "event_index": index,
                "event_id": f"run-1:{index}",
                "parent_event_id": None if index == 1 else f"run-1:{index - 1}",
                "timestamp": f"2026-07-16T00:00:{index:02d}+00:00",
                "type": event_type,
                "payload": payload,
            }
        )
    return rows


def _agent(event_type: str, **payload: Any) -> tuple[str, dict[str, Any]]:
    return "agentscope_event", {"type": event_type, **payload}


def _complete_events(*middle: tuple[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("run_start", {}),
        *middle,
        ("verifier_result", {"ok": True}),
        ("completion_decision", {"accepted": True}),
    ]


def test_clean_trace_has_no_shadow_anomalies() -> None:
    rows = _rows(
        _complete_events(
            _agent(
                "MODEL_CALL_START",
                reply_id="reply-1",
                created_at="2026-07-16T00:00:01+00:00",
            ),
            _agent("TEXT_BLOCK_DELTA", reply_id="reply-1", delta="done"),
            _agent(
                "MODEL_CALL_END",
                reply_id="reply-1",
                created_at="2026-07-16T00:00:02+00:00",
                finished_reason="stop",
            ),
        )
    )

    receipt = trajectory_audit.analyze_native_trace(rows)

    assert receipt["summary"]["flagged_checks"] == []
    assert receipt["authority"] == {
        "mode": "shadow_non_authoritative",
        "canonical_score_modified": False,
        "canonical_verdict_modified": False,
        "causal_attribution_allowed": False,
    }


def test_preflight_abort_is_a_complete_abnormal_trace() -> None:
    rows = _rows(
        [
            ("preflight_result", {"ready": False}),
            ("run_abort", {"reason": "preflight_failed", "status": "failed"}),
        ]
    )

    receipt = trajectory_audit.analyze_native_trace(rows)

    assert receipt["source"]["terminal_mode"] == "preflight_abort"
    assert receipt["summary"]["flagged_checks"] == []


def test_runtime_error_closes_inflight_calls_without_hiding_them() -> None:
    rows = _rows(
        [
            ("run_start", {}),
            _agent(
                "MODEL_CALL_START",
                reply_id="reply-1",
                created_at="2026-07-16T00:00:01+00:00",
            ),
            ("runtime_error", {"error_type": "APIConnectionError"}),
        ]
    )

    receipt = trajectory_audit.analyze_native_trace(rows)

    assert receipt["source"]["terminal_mode"] == "runtime_error"
    assert receipt["summary"]["flagged_checks"] == []
    lifecycle = receipt["checks"]["event_lifecycle"]["evidence"]
    assert lifecycle["open_model_calls"] == []
    assert lifecycle["terminated_model_calls"] == [2]


def test_conflicting_terminal_events_are_flagged() -> None:
    rows = _rows(
        [
            ("run_start", {}),
            ("runtime_error", {"error_type": "RuntimeError"}),
            ("verifier_result", {"ok": False}),
            ("completion_decision", {"accepted": False}),
        ]
    )

    receipt = trajectory_audit.analyze_native_trace(rows)
    kinds = {
        item["kind"]
        for item in receipt["checks"]["trace_integrity"]["evidence"]
    }
    assert "conflicting_terminal_events" in kinds


def test_audit_flags_truncation_empty_output_and_latency() -> None:
    rows = _rows(
        _complete_events(
            _agent(
                "MODEL_CALL_START",
                reply_id="reply-1",
                created_at="2026-07-16T00:00:00+00:00",
            ),
            _agent(
                "MODEL_CALL_END",
                reply_id="reply-1",
                created_at="2026-07-16T00:02:01+00:00",
                finished_reason="max_tokens",
            ),
        )
    )

    receipt = trajectory_audit.analyze_native_trace(rows, latency_threshold_seconds=120)

    assert receipt["checks"]["model_truncation"]["flagged"] is True
    assert receipt["checks"]["empty_model_output"]["flagged"] is True
    assert receipt["checks"]["model_latency_spike"]["flagged"] is True


def test_audit_flags_repeated_tool_calls_and_missing_results() -> None:
    events: list[tuple[str, dict[str, Any]]] = [
        _agent("MODEL_CALL_START", reply_id="reply-1", created_at="2026-07-16T00:00:00+00:00")
    ]
    for index in range(3):
        call_id = f"call-{index}"
        events.extend(
            [
                _agent(
                    "TOOL_CALL_START",
                    reply_id="reply-1",
                    tool_call_id=call_id,
                    tool_call_name="Read",
                ),
                _agent("TOOL_CALL_DELTA", reply_id="reply-1", tool_call_id=call_id, delta='{"file":"a"}'),
                _agent("TOOL_CALL_END", reply_id="reply-1", tool_call_id=call_id),
            ]
        )
    events.append(
        _agent(
            "MODEL_CALL_END",
            reply_id="reply-1",
            created_at="2026-07-16T00:00:05+00:00",
            finished_reason="tool_calls",
        )
    )
    rows = _rows(_complete_events(*events))

    receipt = trajectory_audit.analyze_native_trace(rows)

    assert receipt["checks"]["consecutive_tool_repetition"]["flagged"] is True
    assert receipt["checks"]["event_lifecycle"]["flagged"] is True
    assert receipt["checks"]["event_lifecycle"]["evidence"]["tool_calls_without_result"] == [
        "call-0",
        "call-1",
        "call-2",
    ]


def test_reused_tool_call_id_cannot_hide_a_later_missing_result() -> None:
    rows = _rows(
        _complete_events(
            _agent("TOOL_CALL_START", tool_call_id="call-1", tool_call_name="Read"),
            _agent("TOOL_CALL_END", tool_call_id="call-1"),
            _agent("TOOL_RESULT_START", tool_call_id="call-1"),
            _agent("TOOL_RESULT_END", tool_call_id="call-1", state="success"),
            _agent("TOOL_CALL_START", tool_call_id="call-1", tool_call_name="Read"),
            _agent("TOOL_CALL_END", tool_call_id="call-1"),
        )
    )

    receipt = trajectory_audit.analyze_native_trace(rows)

    assert receipt["checks"]["event_lifecycle"]["flagged"] is True
    assert receipt["checks"]["event_lifecycle"]["evidence"][
        "tool_calls_without_result"
    ] == ["call-1:2"]


def test_audit_flags_broken_trace_chain_and_open_model_call() -> None:
    rows = _rows(
        _complete_events(
            _agent("MODEL_CALL_START", reply_id="reply-1", created_at="2026-07-16T00:00:00+00:00"),
        )
    )
    rows[1]["parent_event_id"] = "wrong"

    receipt = trajectory_audit.analyze_native_trace(rows)

    assert receipt["checks"]["trace_integrity"]["flagged"] is True
    assert receipt["checks"]["event_lifecycle"]["flagged"] is True


def test_audit_flags_duplicate_event_id_and_rejects_non_object_row() -> None:
    rows = _rows(_complete_events())
    rows[1]["event_id"] = rows[0]["event_id"]
    receipt = trajectory_audit.analyze_native_trace(rows)
    kinds = {
        item["kind"]
        for item in receipt["checks"]["trace_integrity"]["evidence"]
    }
    assert "duplicate_event_id" in kinds

    with pytest.raises(ValueError, match="trace row 1 must be an object"):
        trajectory_audit.analyze_native_trace([None])  # type: ignore[list-item]


def test_audit_binds_required_trace_metadata_and_event_identity() -> None:
    rows = _rows(_complete_events())
    rows[0]["run_id"] = ""
    rows[0]["task_id"] = None
    rows[0]["event_index"] = True
    rows[0]["timestamp"] = "not-a-timestamp"
    rows[1]["event_id"] = "forged"
    rows[2]["type"] = None
    rows[2]["payload"] = None

    receipt = trajectory_audit.analyze_native_trace(rows)
    kinds = {
        item["kind"]
        for item in receipt["checks"]["trace_integrity"]["evidence"]
    }

    assert {
        "event_id_binding",
        "event_index",
        "event_type",
        "payload",
        "run_id",
        "task_id",
        "timestamp",
    } <= kinds


def test_audit_flags_overlapping_reuse_of_active_tool_call_id() -> None:
    rows = _rows(
        _complete_events(
            _agent("TOOL_CALL_START", tool_call_id="call-1", tool_call_name="Read"),
            _agent("TOOL_CALL_END", tool_call_id="call-1"),
            _agent("TOOL_CALL_START", tool_call_id="call-1", tool_call_name="Read"),
            _agent("TOOL_CALL_END", tool_call_id="call-1"),
        )
    )

    receipt = trajectory_audit.analyze_native_trace(rows)
    kinds = {
        item["kind"]
        for item in receipt["checks"]["trace_integrity"]["evidence"]
    }

    assert "overlapping_tool_call_id" in kinds


def test_trace_file_loader_is_bounded_and_refuses_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = tmp_path / "external.jsonl"
    external.write_text(json.dumps(_rows([("run_start", {})])[0]) + "\n", encoding="utf-8")
    link = tmp_path / "trace.jsonl"
    link.symlink_to(external)
    with pytest.raises(ValueError, match="must not be a symlink"):
        trajectory_audit.load_native_trace(link)

    monkeypatch.setattr(trajectory_audit, "MAX_TRACE_BYTES", 2)
    with pytest.raises(ValueError, match="trace exceeds"):
        trajectory_audit.load_native_trace(external)


@pytest.mark.parametrize(
    "row",
    [
        '{"event_id":"one","event_id":"two"}',
        '{"event_index":NaN}',
    ],
)
def test_trace_file_loader_rejects_ambiguous_json(tmp_path: Path, row: str) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(row + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid trace JSON"):
        trajectory_audit.load_native_trace(path)


@pytest.mark.parametrize(
    ("latency", "repetition", "message"),
    [
        (float("nan"), 3, "finite and positive"),
        (True, 3, "finite and positive"),
        (120.0, 1, "at least 2"),
        (120.0, True, "at least 2"),
    ],
)
def test_audit_rejects_invalid_thresholds(latency: float, repetition: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        trajectory_audit.analyze_native_trace(
            [],
            latency_threshold_seconds=latency,
            consecutive_tool_threshold=repetition,
        )
