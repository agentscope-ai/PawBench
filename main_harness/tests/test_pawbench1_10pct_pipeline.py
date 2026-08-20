from __future__ import annotations

from scripts.run_pawbench1_10pct_pipeline import (
    sample_fraction_stratified,
    score_summary,
)


def test_sample_fraction_stratified_hits_target_and_keeps_groups() -> None:
    rows = [
        {"run_group": "g", "model": "m1", "harness": "h1", "task_id": f"a{i}"}
        for i in range(10)
    ] + [
        {"run_group": "g", "model": "m2", "harness": "h2", "task_id": f"b{i}"}
        for i in range(10)
    ]
    sampled = sample_fraction_stratified(rows, fraction=0.1, seed=1)
    assert len(sampled) == 2
    assert {row["harness"] for row in sampled} == {"h1", "h2"}


def test_score_summary_computes_basic_stats() -> None:
    rows = [
        {"harness": "h1", "model": "m1", "score": 1.0, "passed": True, "status": "success", "metrics_found": True},
        {"harness": "h1", "model": "m2", "score": 0.0, "passed": False, "status": "failed", "metrics_found": True},
    ]
    summary = score_summary(rows)
    assert summary["row_count"] == 2
    assert summary["avg_score"] == 0.5
    assert summary["pass_rate"] == 0.5
    assert summary["by_harness"]["h1"]["runs"] == 2
