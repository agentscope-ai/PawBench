from __future__ import annotations

import math

import pytest

from pawbench_agentscope.reliability import (
    aggregate_by_feature,
    aggregate_repeated_comparisons,
    wilson_interval,
)


def _comparison(index: int, *, status: str = "supported", feature: str = "F1.1") -> dict:
    return {
        "task_id": f"trial-{index:02d}",
        "feature_id": feature,
        "status": status,
        "score_drop": 1.0 if status == "supported" else -1.0 if status == "contradicted" else 0.0,
        "introduced_false_acceptance": False,
    }


def test_wilson_interval_rejects_nonfinite_or_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="trials must be positive"):
        wilson_interval(0, True)
    with pytest.raises(ValueError, match="successes must be"):
        wilson_interval(True, 1)
    for value in (0.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="z must be"):
            wilson_interval(1, 1, z=value)


def test_repeated_evidence_supports_only_decisive_sufficient_trials() -> None:
    supported = aggregate_repeated_comparisons(
        [_comparison(index) for index in range(10)],
        "F1.1",
        minimum_trials=5,
    )
    insufficient = aggregate_repeated_comparisons(
        [_comparison(index) for index in range(4)],
        "F1.1",
        minimum_trials=5,
    )

    assert supported.status == "supported"
    assert supported.supported_rate == 1.0
    assert supported.supported_rate_interval_95[0] > 0.5
    assert insufficient.status == "insufficient_trials"


@pytest.mark.parametrize("value", [-0.1, 1.0, float("nan"), float("inf")])
def test_repeated_evidence_rejects_invalid_decision_threshold(value: float) -> None:
    with pytest.raises(ValueError, match="decisive_lower_bound"):
        aggregate_repeated_comparisons(
            [_comparison(index) for index in range(5)],
            "F1.1",
            decisive_lower_bound=value,
        )


def test_aggregate_by_feature_fails_closed_on_unknown_or_malformed_comparison() -> None:
    with pytest.raises(ValueError, match="unknown Feature ID"):
        aggregate_by_feature([_comparison(1, feature="F9.9")])
    with pytest.raises(ValueError, match="must be an object"):
        aggregate_by_feature([None])  # type: ignore[list-item]


def test_direct_repeated_aggregation_fails_closed_on_unselected_bad_rows() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        aggregate_repeated_comparisons(
            [_comparison(1), None],  # type: ignore[list-item]
            "F1.1",
        )
    with pytest.raises(ValueError, match="unknown Feature ID"):
        aggregate_repeated_comparisons(
            [_comparison(1), _comparison(2, feature="F9.9")],
            "F1.1",
        )


def test_repeated_evidence_rejects_nonfinite_score_drop() -> None:
    comparison = _comparison(1)
    comparison["score_drop"] = math.nan
    with pytest.raises(ValueError, match="non-finite"):
        aggregate_repeated_comparisons([comparison], "F1.1")


def test_repeated_evidence_rejects_nonboolean_false_acceptance() -> None:
    comparison = _comparison(1)
    comparison["introduced_false_acceptance"] = "false"
    with pytest.raises(ValueError, match="invalid introduced_false_acceptance"):
        aggregate_repeated_comparisons([comparison], "F1.1")
