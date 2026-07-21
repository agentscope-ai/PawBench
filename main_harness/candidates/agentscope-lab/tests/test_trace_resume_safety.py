from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pawbench_agentscope.tracing import TraceWriter
import pawbench_agentscope.tracing as tracing_module


def test_trace_resume_preserves_chain_and_run_id(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    first = TraceWriter(path, task_id="resume-task")
    first_row = first.append("run_start", {"status": "started"})

    resumed = TraceWriter(path, task_id="resume-task", append=True)
    second_row = resumed.append("run_end", {"status": "complete"})

    assert resumed.run_id == first.run_id
    assert second_row["event_index"] == 2
    assert second_row["parent_event_id"] == first_row["event_id"]
    assert len(resumed.read_events()) == 2


def test_trace_writer_serializes_concurrent_threads(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceWriter(path, task_id="threaded-task", run_id="threaded-run")

    def emit(worker: int) -> None:
        for index in range(20):
            trace.append("thread_event", {"worker": worker, "index": index})

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(emit, range(16)))

    rows = trace.read_events()
    assert len(rows) == 320
    assert [row["event_index"] for row in rows] == list(range(1, 321))
    assert [row["event_id"] for row in rows] == [
        f"threaded-run:{index}" for index in range(1, 321)
    ]
    assert [row["parent_event_id"] for row in rows] == [
        None,
        *(f"threaded-run:{index}" for index in range(1, 320)),
    ]


def test_trace_resume_rejects_task_or_run_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceWriter(path, task_id="original-task", run_id="original-run")
    trace.append("run_start", {})

    with pytest.raises(ValueError, match="task_id mismatch"):
        TraceWriter(path, task_id="other-task", append=True)
    with pytest.raises(ValueError, match="requested run_id"):
        TraceWriter(path, task_id="original-task", run_id="other-run", append=True)


def test_trace_resume_rejects_broken_parent_chain(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceWriter(path, task_id="chain-task")
    trace.append("one", {})
    rows = trace.read_events()
    rows[0]["parent_event_id"] = "forged"
    path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="parent chain"):
        TraceWriter(path, task_id="chain-task", append=True)


def test_trace_resume_rejects_malformed_event_surface(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceWriter(path, task_id="surface-task")
    trace.append("one", {})
    row = trace.read_events()[0]
    row["payload"] = "not-an-object"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="trace payload is invalid"):
        TraceWriter(path, task_id="surface-task", append=True)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("event_id", "forged:1", "event_id is inconsistent"),
        ("timestamp", "not-a-timestamp", "timestamp is invalid"),
    ],
)
def test_trace_resume_rejects_forged_identity_or_time(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceWriter(path, task_id="forged-surface", run_id="run-a")
    trace.append("one", {})
    row = trace.read_events()[0]
    row[field] = value
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        TraceWriter(path, task_id="forged-surface", append=True)


def test_trace_resume_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        '{"run_id":"run-a","run_id":"run-b","task_id":"task-a"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate trace JSON key"):
        TraceWriter(path, task_id="task-a", append=True)


def test_trace_append_rejects_nonfinite_json_without_advancing_state(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceWriter(path, task_id="finite-trace")

    with pytest.raises(ValueError, match="Out of range float values"):
        trace.append("bad", {"latency": float("nan")})

    assert trace.event_index == 0
    assert trace.last_event_id is None
    assert path.read_bytes() == b""


def test_trace_append_is_size_bounded_and_failure_does_not_advance_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "trace.jsonl"
    trace = TraceWriter(path, task_id="bounded-append")
    monkeypatch.setattr(tracing_module, "MAX_TRACE_BYTES", 32)

    with pytest.raises(ValueError, match="trace exceeds 32 bytes"):
        trace.append("large", {"text": "x" * 100})

    assert trace.event_index == 0
    assert trace.last_event_id is None
    assert path.read_bytes() == b""


def test_trace_resume_is_size_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text("x" * 32, encoding="utf-8")
    monkeypatch.setattr(tracing_module, "MAX_TRACE_BYTES", 16)

    with pytest.raises(ValueError, match="trace exceeds 16 bytes"):
        TraceWriter(path, task_id="large-task", append=True)
