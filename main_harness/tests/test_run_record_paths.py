from __future__ import annotations

import pytest

from scripts.paths import (
    AGENTSCOPE_RUNS_ROOT,
    BUG_TICKETS_ROOT,
    ENGINEERING_RECORDS_ROOT,
    HARNESS_ABLATION_RUNS_ROOT,
    HARNESS_WORK_ROOT,
    PROJECT_ROOT,
    REASONING_WORK_ROOT,
    RUN_RECORDS_ROOT,
)
from scripts import (
    analyze_openclaw_v2,
    pawbench_output_adapter,
    stress_test_real_v1_hf,
    stress_test_reasoning_v2,
)


def test_run_records_is_the_flat_final_result_root() -> None:
    assert RUN_RECORDS_ROOT == PROJECT_ROOT / "run_records"
    if not RUN_RECORDS_ROOT.is_dir():
        pytest.skip("requires local attribution run records")
    result_dirs = [path for path in RUN_RECORDS_ROOT.iterdir() if path.is_dir()]
    assert result_dirs
    for path in result_dirs:
        parts = path.name.rsplit("-", 1)
        assert len(parts) == 2
        assert len(parts[1]) == 8 and parts[1].isdigit()


def test_non_reasoning_records_use_separate_roots() -> None:
    expected = {
        HARNESS_ABLATION_RUNS_ROOT: PROJECT_ROOT / "harness_ablation_runs",
        HARNESS_WORK_ROOT: ENGINEERING_RECORDS_ROOT / "main_harness",
        REASONING_WORK_ROOT: ENGINEERING_RECORDS_ROOT / "main_reasoning",
        AGENTSCOPE_RUNS_ROOT: HARNESS_ABLATION_RUNS_ROOT / "agentscope",
        BUG_TICKETS_ROOT: PROJECT_ROOT / "backup" / "project_history" / "bug_tickets",
    }
    for path, expected_path in expected.items():
        assert path == expected_path
    if not all(path.is_dir() for path in expected):
        pytest.skip("requires local Harness-core runtime directories")


def test_engineering_producer_defaults_stay_out_of_reasoning_results() -> None:
    engineering_defaults = (
        pawbench_output_adapter.DEFAULT_OUT,
        stress_test_reasoning_v2.DEFAULT_OUT,
    )
    for path in engineering_defaults:
        assert path.is_relative_to(HARNESS_WORK_ROOT)


def test_final_attribution_defaults_follow_dataset_model_date_rule() -> None:
    defaults = (
        stress_test_real_v1_hf.DEFAULT_OUT,
        analyze_openclaw_v2.DEFAULT_OUT,
    )
    for path in defaults:
        assert path.parent == RUN_RECORDS_ROOT
        dataset_model, date = path.name.rsplit("-", 1)
        assert dataset_model
        assert len(date) == 8 and date.isdigit()
