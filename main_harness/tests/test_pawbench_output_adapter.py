from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from scripts import bridge_attribution_to_harness_core as bridge
from scripts import pawbench_output_adapter as adapter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRAJECTORY_ANALYSIS_PATH = PROJECT_ROOT / "main_reasoning" / "scripts" / "trajectory_analysis.py"


def load_trajectory_analysis():
    if not TRAJECTORY_ANALYSIS_PATH.is_file():
        pytest.skip("requires the sibling main_reasoning backend")
    spec = importlib.util.spec_from_file_location("trajectory_analysis_for_test", TRAJECTORY_ANALYSIS_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def make_checkpoint_fixture(root: Path, *, count: int = 2) -> Path:
    run_dir = root / "20260709_010101" / "pawbench" / "qwen3.7-max" / "qwenpaw"
    results = []
    for index in range(count):
        task_id = f"T{index:03d}_fixture"
        results.append(
            {
                "task_id": task_id,
                "task_name": f"Fixture {index}",
                "score": 0.0 if index % 2 else 1.0,
                "max_score": 1.0,
                "passed": index % 2 == 0,
                "grading_type": "hybrid",
                "breakdown": {"correctness": 0.0 if index % 2 else 1.0},
                "notes": "grader notes",
                "execution_time": 1.2,
                "status": "success",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "timed_out": False,
                "error": "",
                "anomaly": {"is_anomalous": False, "has_error": False, "has_api_error": False, "items": []},
                "labels": {"scenario": "fixture", "capabilities": ["tool_use"]},
            }
        )
        write_jsonl(
            run_dir / "transcripts" / f"{task_id}.jsonl",
            [
                {"type": "message", "message": {"role": "user", "content": "solve task"}},
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Timed out after maximum iterations"}],
                    },
                },
            ],
        )
    checkpoint = run_dir / "20260709_010102.json"
    write_json(
        checkpoint,
        {
            "benchmark": "pawbench",
            "model": "qwen3.7-max",
            "timestamp": "2026-07-09T01:01:02",
            "summary": {"total_runs": count, "avg_score": 0.5},
            "results": results,
        },
    )
    return checkpoint


def make_legacy_metrics_fixture(root: Path, *, count: int = 2) -> None:
    for index in range(count):
        task_id = f"T{index:03d}_legacy"
        output_dir = root / "deepseek-v4-pro" / "openclaw" / task_id / "output"
        write_json(
            output_dir / "metrics.json",
            {
                "instance_id": task_id,
                "task_score": 0.25,
                "passed": False,
                "grading_type": "automated",
                "breakdown": {"correctness": 0.0},
                "wall_time_s": 2.5,
                "status": "success",
                "exit_code": 0,
                "notes": "missing expected output file",
                "anomaly": {"is_anomalous": False, "has_error": False, "has_api_error": False, "items": []},
            },
        )
        write_jsonl(
            output_dir / "results" / "20260709_020202" / "pawbench" / "deepseek-v4-pro" / "openclaw" / "transcripts" / f"{task_id}.jsonl",
            [
                {"type": "message", "message": {"role": "user", "content": "write output file"}},
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "File not found and final artifact missing"}],
                    },
                },
            ],
        )


def test_adapter_reads_official_checkpoint_output(tmp_path: Path) -> None:
    make_checkpoint_fixture(tmp_path, count=3)
    records = adapter.collect_records(tmp_path, include_legacy=False)
    assert len(records) == 3
    first = records[0]
    assert first.source_format == "pawbench_checkpoint"
    assert first.run_group == "20260709_010101"
    assert first.model == "qwen3.7-max"
    assert first.harness == "qwenpaw"
    assert first.transcript_path and first.transcript_path.endswith(".jsonl")
    assert first.transcript_length == 2


def test_adapter_reads_legacy_metrics_tree(tmp_path: Path) -> None:
    make_legacy_metrics_fixture(tmp_path, count=2)
    records = adapter.collect_records(tmp_path, include_checkpoints=False)
    assert len(records) == 2
    row = adapter.record_to_score_matrix_row(records[0])
    assert row["source_format"] == "pawbench_legacy_metrics"
    assert row["model"] == "deepseek-v4-pro"
    assert row["harness"] == "openclaw"
    assert row["score"] == 0.25
    assert row["metrics_found"] is True
    assert row["transcript_found"] is True


def test_adapter_outputs_feed_reasoning_and_harness_bridge(tmp_path: Path) -> None:
    make_checkpoint_fixture(tmp_path, count=1)
    records = adapter.collect_records(tmp_path, include_legacy=False)
    out_dir = tmp_path / "normalized"
    summary = adapter.write_outputs(records, out_dir, tmp_path)
    assert summary["ready_for_reasoning"] is True

    trajectory_analysis = load_trajectory_analysis()
    normalized_rows = trajectory_analysis.load_normalized_rows(out_dir / "attribution_input_runs.jsonl")
    reasoned = trajectory_analysis.analyze_normalized_row(normalized_rows[0])
    assert "H3" in reasoned["codes"]

    manifest = {
        "taxonomy_version": "harness_core_v2_20260710",
        "candidate": "TestHarness",
        "_candidate_dir": "test",
        "features": {
            "F3.1": {"name": "Completion / Termination"},
            "F3.2": {"name": "Budget / Guards"},
            "F3.3": {"name": "Recovery / Resume"},
        },
        "h_code_mapping": {"H3": ["F3.1", "F3.2", "F3.3"]},
    }
    bridged = bridge.bridge_row(reasoned, manifest)
    assert bridged["h_codes"] == ["H3"]
    assert bridged["recommended_switch_keys"] == ["F3_2"]


def test_reasoning_emits_ex3_for_external_provider_failure() -> None:
    trajectory_analysis = load_trajectory_analysis()
    features = trajectory_analysis.collect_features(
        [
            {
                "message": {
                    "role": "toolResult",
                    "content": "Provider returned persistent 503 service unavailable responses.",
                }
            }
        ]
    )
    codes, evidence, confidence = trajectory_analysis.assign_codes(
        features,
        {"anomaly": [], "breakdown": {}, "notes": "", "score": None},
    )
    assert "Ex-3" in codes
    assert "H6" not in codes
    assert any(item.startswith("Ex-3:") for item in evidence)
    assert confidence == "HIGH"


def test_adapter_stress_collects_mixed_outputs(tmp_path: Path) -> None:
    make_checkpoint_fixture(tmp_path / "official", count=120)
    make_legacy_metrics_fixture(tmp_path / "legacy", count=80)
    official = adapter.collect_records(tmp_path / "official", include_legacy=False)
    legacy = adapter.collect_records(tmp_path / "legacy", include_checkpoints=False)
    records = official + legacy
    assert len(records) == 200

    out_dir = tmp_path / "ingest"
    summary = adapter.write_outputs(records, out_dir, tmp_path)
    assert summary["record_count"] == 200
    assert summary["missing_transcripts"] == 0
    assert (out_dir / "score_matrix_long.jsonl").exists()
    assert (out_dir / "attribution_input_runs.jsonl").exists()
