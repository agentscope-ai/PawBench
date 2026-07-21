from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "runtime_failure_matrix.py"
SPEC = importlib.util.spec_from_file_location("pawbench_runtime_failure_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def matrix(tmp_path_factory: pytest.TempPathFactory) -> dict:
    output = tmp_path_factory.mktemp("runtime-failure-matrix") / "run"
    return MODULE.run_matrix(output, fresh=False)


def test_real_runtime_failure_matrix_exercises_all_stable_codes(matrix: dict) -> None:
    assert matrix["case_count"] == 10
    assert matrix["coded_failure_count"] == 9
    assert matrix["all_stable_codes_exercised"] is True
    assert matrix["matched_count"] == 10
    assert matrix["contract_valid_count"] == 10
    assert matrix["shadow_expectation_matched_count"] == 10


def test_native_timeout_remains_completed_rejected_outcome(matrix: dict) -> None:
    record = next(
        item for item in matrix["records"] if item["task_id"] == "runtime-native-timeout-outcome"
    )
    assert record["observed_code"] is None
    assert record["shadow_flagged_checks"] == ["empty_model_output"]
    assert record["shadow_expectation_matched"] is True
    assert record["completion_decision"] == {
        "completion_ok": False,
        "stop_reason": "runtime_timeout",
        "verification_gated": True,
        "verifier_ok": True,
        "accepted": False,
    }
