"""Repeated-trial evidence summaries for stochastic Feature ablations.

This module is an optional outer layer. It aggregates already-materialized
paired comparisons and never changes a reasoning result or schedules a run.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field

from pawbench_agentscope.features import FEATURE_IDS


RELIABILITY_SCHEMA_VERSION = "harness-core-ablation-reliability/v1"


class RepeatedAblationEvidence(BaseModel):
    schema_version: str = RELIABILITY_SCHEMA_VERSION
    feature_id: str
    trial_count: int
    status: str
    status_counts: dict[str, int] = Field(default_factory=dict)
    supported_rate: float
    supported_rate_interval_95: list[float]
    contradicted_rate: float
    contradicted_rate_interval_95: list[float]
    mean_score_drop: float
    median_score_drop: float
    false_acceptance_regressions: int
    unique_task_ids: int
    task_ids: list[str] = Field(default_factory=list)
    decision_rule: str


def wilson_interval(successes: int, trials: int, *, z: float = 1.959963984540054) -> tuple[float, float]:
    """Return the Wilson score interval for a Bernoulli proportion."""

    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ValueError("trials must be positive")
    if (
        isinstance(successes, bool)
        or not isinstance(successes, int)
        or successes < 0
        or successes > trials
    ):
        raise ValueError("successes must be between zero and trials")
    if not isinstance(z, (int, float)) or isinstance(z, bool) or not math.isfinite(z) or z <= 0:
        raise ValueError("z must be a finite positive number")
    proportion = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    center = (proportion + z2 / (2.0 * trials)) / denominator
    margin = (
        z
        * math.sqrt(
            (proportion * (1.0 - proportion) + z2 / (4.0 * trials))
            / trials
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def aggregate_repeated_comparisons(
    comparisons: Sequence[Mapping[str, Any]],
    feature_id: str,
    *,
    minimum_trials: int = 5,
    decisive_lower_bound: float = 0.5,
) -> RepeatedAblationEvidence:
    """Aggregate repeated comparisons for one Feature with a conservative rule."""

    if feature_id not in FEATURE_IDS:
        raise ValueError(f"unknown Feature ID: {feature_id}")
    if isinstance(minimum_trials, bool) or not isinstance(minimum_trials, int) or minimum_trials < 1:
        raise ValueError("minimum_trials must be positive")
    if (
        isinstance(decisive_lower_bound, bool)
        or not isinstance(decisive_lower_bound, (int, float))
        or not math.isfinite(decisive_lower_bound)
        or not 0 <= decisive_lower_bound < 1
    ):
        raise ValueError("decisive_lower_bound must be finite and in [0, 1)")
    selected: list[Mapping[str, Any]] = []
    for index, item in enumerate(comparisons):
        if not isinstance(item, Mapping):
            raise ValueError(f"comparison {index} must be an object")
        candidate_feature = item.get("feature_id")
        if candidate_feature not in FEATURE_IDS:
            raise ValueError(
                f"comparison {index} has an unknown Feature ID: {candidate_feature!r}"
            )
        if candidate_feature == feature_id:
            selected.append(item)
    if not selected:
        raise ValueError(f"no comparisons for {feature_id}")
    allowed = {"supported", "contradicted", "inconclusive"}
    statuses: list[str] = []
    task_ids: list[str] = []
    score_drops: list[float] = []
    false_acceptance = 0
    for index, item in enumerate(selected):
        status = item.get("status")
        if status not in allowed:
            raise ValueError(f"comparison {index} has invalid status: {status!r}")
        score_drop = item.get("score_drop")
        if isinstance(score_drop, bool) or not isinstance(score_drop, (int, float)):
            raise ValueError(f"comparison {index} has invalid score_drop")
        numeric_drop = float(score_drop)
        if not math.isfinite(numeric_drop):
            raise ValueError(f"comparison {index} has non-finite score_drop")
        task_id = item.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(f"comparison {index} has invalid task_id")
        introduced_false_acceptance = item.get("introduced_false_acceptance", False)
        if not isinstance(introduced_false_acceptance, bool):
            raise ValueError(
                f"comparison {index} has invalid introduced_false_acceptance"
            )
        statuses.append(status)
        score_drops.append(numeric_drop)
        task_ids.append(task_id)
        false_acceptance += introduced_false_acceptance

    counts = Counter(statuses)
    trials = len(selected)
    supported = counts["supported"]
    contradicted = counts["contradicted"]
    supported_interval = wilson_interval(supported, trials)
    contradicted_interval = wilson_interval(contradicted, trials)
    if trials < minimum_trials:
        decision = "insufficient_trials"
    elif supported_interval[0] > decisive_lower_bound:
        decision = "supported"
    elif contradicted_interval[0] > decisive_lower_bound:
        decision = "contradicted"
    else:
        decision = "inconclusive"
    rule = (
        f"At least {minimum_trials} paired trials and the 95% Wilson lower bound "
        f"for one decisive direction must exceed {decisive_lower_bound:g}."
    )
    return RepeatedAblationEvidence(
        feature_id=feature_id,
        trial_count=trials,
        status=decision,
        status_counts=dict(sorted(counts.items())),
        supported_rate=supported / trials,
        supported_rate_interval_95=list(supported_interval),
        contradicted_rate=contradicted / trials,
        contradicted_rate_interval_95=list(contradicted_interval),
        mean_score_drop=statistics.fmean(score_drops),
        median_score_drop=statistics.median(score_drops),
        false_acceptance_regressions=false_acceptance,
        unique_task_ids=len(set(task_ids)),
        task_ids=sorted(set(task_ids)),
        decision_rule=rule,
    )


def aggregate_by_feature(
    comparisons: Sequence[Mapping[str, Any]],
    *,
    minimum_trials: int = 5,
) -> list[RepeatedAblationEvidence]:
    """Aggregate every known Feature present in a comparison collection."""

    feature_values: set[str] = set()
    for index, item in enumerate(comparisons):
        if not isinstance(item, Mapping):
            raise ValueError(f"comparison {index} must be an object")
        feature_id = item.get("feature_id")
        if feature_id not in FEATURE_IDS:
            raise ValueError(f"comparison {index} has an unknown Feature ID: {feature_id!r}")
        feature_values.add(str(feature_id))
    present = sorted(feature_values, key=FEATURE_IDS.index)
    return [
        aggregate_repeated_comparisons(
            comparisons,
            feature_id,
            minimum_trials=minimum_trials,
        )
        for feature_id in present
    ]


__all__ = [
    "RELIABILITY_SCHEMA_VERSION",
    "RepeatedAblationEvidence",
    "aggregate_by_feature",
    "aggregate_repeated_comparisons",
    "wilson_interval",
]
