from __future__ import annotations

import importlib.util
import argparse
import asyncio
import json
import stat
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agentic_stress.py"
SPEC = importlib.util.spec_from_file_location("pawbench_agentic_stress", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_small_sample_p95_uses_nearest_rank_not_lower_sample() -> None:
    tasks = [
        MODULE.StressTask(
            task_id=task_id,
            category="test",
            instruction="test",
            fixtures={},
            required_artifacts=("answer.txt",),
        )
        for task_id in ("sample-a", "sample-b")
    ]
    records = [
        {
            "model": "model-a",
            "task_id": task_id,
            "duration_seconds": duration,
            "accepted": True,
            "verifier_ok": True,
            "completion_ok": True,
            "status": "completed",
            "trace_metrics": {"trace_complete": True, "model_names_observed": ["model-a"]},
        }
        for task_id, duration in zip(("sample-a", "sample-b"), (10.0, 20.0), strict=True)
    ]

    summary = MODULE.summarize(records, ["model-a"], tasks)

    assert summary["overall"]["median_duration_seconds"] == 15.0
    assert summary["overall"]["p95_duration_seconds"] == 20.0


def test_summary_surfaces_non_authoritative_shadow_audit() -> None:
    tasks = [
        MODULE.StressTask(
            task_id=task_id,
            category="test",
            instruction="test",
            fixtures={},
            required_artifacts=("answer.txt",),
        )
        for task_id in ("clean", "flagged")
    ]
    records = []
    for task_id, flags in (("clean", []), ("flagged", ["model_truncation"])):
        records.append(
            {
                "model": "model-a",
                "task_id": task_id,
                "category": "test",
                "duration_seconds": 1.0,
                "accepted": True,
                "verifier_ok": True,
                "completion_ok": True,
                "status": "completed",
                "trace_metrics": {
                    "trace_complete": True,
                    "model_names_observed": ["model-a"],
                },
                "trajectory_shadow": {
                    "summary": {
                        "flagged_checks": flags,
                        "anomaly_count": len(flags),
                        "manual_review_recommended": bool(flags),
                    }
                },
            }
        )

    summary = MODULE.summarize(records, ["model-a"], tasks)
    shadow = summary["trajectory_shadow_audit"]

    assert shadow["audited_runs"] == 2
    assert shadow["clean_runs"] == 1
    assert shadow["flagged_runs"] == 1
    assert shadow["flagged_check_counts"] == {"model_truncation": 1}
    assert shadow["authority"]["canonical_verdict_modified"] is False
    assert summary["readiness"]["ready"] is True


def test_fresh_output_refuses_unmarked_directory(tmp_path: Path) -> None:
    output = tmp_path / "important"
    output.mkdir()
    victim = output / "keep.txt"
    victim.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to delete unmarked"):
        MODULE.prepare_output_root(output, fresh=True)

    assert victim.read_text(encoding="utf-8") == "keep"


def test_fresh_output_allows_only_own_marker(tmp_path: Path) -> None:
    output = tmp_path / "stress"
    MODULE.prepare_output_root(output, fresh=False)
    (output / "old.json").write_text("{}", encoding="utf-8")

    MODULE.prepare_output_root(output, fresh=True)

    assert (output / MODULE.OUTPUT_MARKER).read_text(encoding="utf-8").strip() == MODULE.OUTPUT_SCHEMA
    assert not (output / "old.json").exists()


def test_fresh_output_refuses_symlink_marker(tmp_path: Path) -> None:
    output = tmp_path / "stress"
    output.mkdir()
    external = tmp_path / "external-marker"
    external.write_text(MODULE.OUTPUT_SCHEMA + "\n", encoding="utf-8")
    (output / MODULE.OUTPUT_MARKER).symlink_to(external)
    victim = output / "keep.txt"
    victim.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to delete unmarked"):
        MODULE.prepare_output_root(output, fresh=True)

    assert victim.read_text(encoding="utf-8") == "keep"


def test_output_root_refuses_directory_symlink(tmp_path: Path) -> None:
    real_output = tmp_path / "real"
    real_output.mkdir()
    output_link = tmp_path / "linked"
    output_link.symlink_to(real_output, target_is_directory=True)

    with pytest.raises(ValueError, match="output directory must not be a symlink"):
        MODULE.prepare_output_root(output_link, fresh=False)


def test_model_slug_is_stable_safe_and_collision_resistant() -> None:
    assert MODULE.model_slug("qwen3.7-max") == "qwen3.7-max"
    slash_name = MODULE.model_slug("org/model")
    literal_name = MODULE.model_slug("org__model")
    assert "/" not in slash_name
    assert slash_name != literal_name
    assert len(MODULE.model_slug("x" * 500)) <= 91


def test_stress_error_text_is_redacted_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MODULE, "MAX_STRESS_ERROR_CHARS", 128)
    secret = "sk-super-secret-12345678"
    message = MODULE.bounded_error_text(
        f"OPENAI_API_KEY={secret} " + "x" * 1_000
    )
    assert secret not in message
    assert "truncated stress error" in message
    assert len(message) <= 128


def test_safe_run_root_rejects_symlinked_model_directory(tmp_path: Path) -> None:
    output = tmp_path / "stress"
    output.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (output / MODULE.model_slug("model-a")).symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="must not contain a symlink"):
        MODULE.safe_run_root(output, "model-a", "01_sum_numbers")

    assert not (external / "01_sum_numbers").exists()


def test_load_results_ignores_only_truncated_final_record(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text('{"task_id":"ok"}\n{"task_id":', encoding="utf-8")

    assert MODULE.load_results(path) == [{"task_id": "ok"}]


def test_load_results_rejects_corrupt_middle_record(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text('{"task_id":"one"}\nnot-json\n{"task_id":"two"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="corrupt JSONL record"):
        MODULE.load_results(path)


def test_load_results_rejects_complete_corrupt_final_record(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text('{"task_id":"one"}\nnot-json\n', encoding="utf-8")

    with pytest.raises(ValueError, match="corrupt JSONL record"):
        MODULE.load_results(path)


@pytest.mark.parametrize(
    "payload",
    [
        '{"task_id":"one","task_id":"two"}\n',
        '{"task_id":"one","task_id":"two"}',
        '{"duration_seconds":NaN}\n',
        '{"duration_seconds":NaN}',
    ],
)
def test_load_results_rejects_ambiguous_json(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="corrupt JSONL record"):
        MODULE.load_results(path)


def test_load_results_enforces_file_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text('{"task_id":"one"}\n', encoding="utf-8")
    monkeypatch.setattr(MODULE, "MAX_STRESS_RESULTS_BYTES", 4)

    with pytest.raises(ValueError, match="input exceeds 4 bytes"):
        MODULE.load_results(path)


def test_artifact_hashes_refuses_symlink_and_bounds_large_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("secret", encoding="utf-8")
    (workspace / "linked.txt").symlink_to(external)
    (workspace / "large.txt").write_text("0123456789", encoding="utf-8")
    monkeypatch.setattr(MODULE, "MAX_STRESS_ARTIFACT_HASH_BYTES", 4)

    receipts = MODULE.artifact_hashes(workspace, ("linked.txt", "large.txt"))

    assert receipts["linked.txt"] == {"hash_status": "skipped_symlink"}
    assert receipts["large.txt"]["hash_status"] == "skipped_too_large"
    assert receipts["large.txt"]["sha256"] is None


def test_artifact_hashes_bounds_file_that_grows_after_size_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / "growing.txt"
    artifact.write_text("0123456789", encoding="utf-8")
    monkeypatch.setattr(MODULE, "MAX_STRESS_ARTIFACT_HASH_BYTES", 4)
    monkeypatch.setattr(
        MODULE.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG, st_size=1),
    )

    receipt = MODULE.artifact_hashes(workspace, ("growing.txt",))["growing.txt"]

    assert receipt["hash_status"] == "skipped_too_large"
    assert receipt["sha256"] is None
    assert receipt["size"] == 10


def _valid_resume_record() -> dict[str, object]:
    return {
        "model": "model-a",
        "task_id": "task-a",
        "status": "completed",
        "accepted": True,
        "completion_ok": True,
        "verifier_ok": True,
        "verifier": {"ok": True},
        "duration_seconds": 1.0,
        "trace_metrics": {
            "trace_complete": True,
            "model_names_observed": ["model-a"],
        },
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("accepted", "yes", "accepted must be boolean"),
        ("duration_seconds", float("nan"), "duration_seconds is invalid"),
        ("status", "done", "invalid status"),
    ],
)
def test_resume_record_rejects_malformed_completed_rows(
    field: str,
    value: object,
    message: str,
) -> None:
    record = _valid_resume_record()
    record[field] = value

    with pytest.raises(ValueError, match=message):
        MODULE.validate_resume_record(
            record,
            expected_keys={("model-a", "task-a")},
        )


def test_resume_record_rejects_inconsistent_acceptance() -> None:
    record = _valid_resume_record()
    record["verifier_ok"] = False

    with pytest.raises(ValueError, match="acceptance is inconsistent"):
        MODULE.validate_resume_record(
            record,
            expected_keys={("model-a", "task-a")},
        )


def test_resume_requires_bound_local_trace_shadow_result_and_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "stress"
    MODULE.prepare_output_root(output, fresh=False)
    task = MODULE.StressTask(
        task_id="task-a",
        category="test",
        instruction="write expected",
        fixtures={},
        required_artifacts=("answer.txt",),
    )
    run_root = MODULE.safe_run_root(output, "model-a", task.task_id)
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "answer.txt").write_text("expected", encoding="utf-8")
    trace_path = run_root / "trace.jsonl"
    events = [
        ("run_start", {}),
        (
            "agentscope_event",
            {
                "type": "MODEL_CALL_START",
                "reply_id": "reply-a",
                "model_name": "model-a",
            },
        ),
        ("verifier_result", {"ok": True}),
        ("completion_decision", {"accepted": True}),
    ]
    rows = [
        {
            "run_id": "run-a",
            "task_id": "task-a",
            "event_index": index,
            "event_id": f"run-a:{index}",
            "parent_event_id": None if index == 1 else f"run-a:{index - 1}",
            "timestamp": f"2026-07-16T00:00:0{index}+00:00",
            "type": event_type,
            "payload": payload,
        }
        for index, (event_type, payload) in enumerate(events, start=1)
    ]
    trace_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    shadow_path = run_root / "trajectory-shadow.json"
    shadow = MODULE.analyze_native_trace(rows)
    shadow_path.write_text(json.dumps(shadow), encoding="utf-8")
    record = _valid_resume_record()
    record.update(
        {
            "category": "test",
            "trace_path": str(trace_path),
            "workspace_root": str(workspace),
            "trace_metrics": MODULE.trace_metrics(rows),
            "artifacts": MODULE.artifact_hashes(workspace, task.required_artifacts),
            "trajectory_shadow": {
                "path": str(shadow_path),
                "authority": shadow["authority"],
                "summary": shadow["summary"],
            },
        }
    )
    result_path = run_root / "result.json"
    result_path.write_text(json.dumps(record), encoding="utf-8")

    assert MODULE.resume_record_has_evidence(record, out_root=output, task=task) is True

    monkeypatch.setattr(
        MODULE,
        "redact_sensitive_text",
        lambda value: value.replace(str(output), "$OUTPUT"),
    )
    record["trace_path"] = str(record["trace_path"]).replace(str(output), "$OUTPUT")
    record["workspace_root"] = str(record["workspace_root"]).replace(str(output), "$OUTPUT")
    record["trajectory_shadow"]["path"] = str(  # type: ignore[index]
        record["trajectory_shadow"]["path"]  # type: ignore[index]
    ).replace(str(output), "$OUTPUT")
    result_path.write_text(json.dumps(record), encoding="utf-8")

    assert MODULE.resume_record_has_evidence(record, out_root=output, task=task) is True

    trace_path.unlink()
    assert MODULE.resume_record_has_evidence(record, out_root=output, task=task) is False


def test_resume_rejects_changed_semantic_configuration(tmp_path: Path) -> None:
    output = tmp_path / "stress"
    MODULE.prepare_output_root(output, fresh=False)
    (output / "manifest.json").write_text(
        json.dumps({"configuration_classification": {"semantic_sha256": "0" * 64}}),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        out_dir=str(output),
        fresh=False,
        resume=True,
        models=["model-a"],
        task_ids=["01_sum_numbers"],
        tasks_per_model=1,
        per_model_concurrency=1,
        global_concurrency=1,
        max_iters=2,
        timeout_seconds=10.0,
    )

    with pytest.raises(ValueError, match="semantic configuration differs"):
        asyncio.run(MODULE.execute(args))


def test_execute_rejects_invalid_programmatic_limits_before_creating_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "stress"
    args = argparse.Namespace(
        out_dir=str(output),
        fresh=False,
        resume=False,
        models=["model-a"],
        task_ids=["01_sum_numbers"],
        tasks_per_model=1,
        per_model_concurrency=0,
        global_concurrency=1,
        max_iters=2,
        timeout_seconds=10.0,
    )

    with pytest.raises(ValueError, match="per-model-concurrency"):
        asyncio.run(MODULE.execute(args))

    assert not output.exists()


@pytest.mark.parametrize(
    ("extra_args", "message"),
    [
        (["--models", "same", "same"], "must not contain duplicates"),
        (["--timeout-seconds", "nan"], "finite and positive"),
        (["--timeout-seconds", "0"], "finite and positive"),
        (["--max-iters", "0"], "must be positive"),
    ],
)
def test_cli_rejects_unsafe_execution_arguments(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    message: str,
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *extra_args])

    with pytest.raises(SystemExit, match=message):
        MODULE.main()
