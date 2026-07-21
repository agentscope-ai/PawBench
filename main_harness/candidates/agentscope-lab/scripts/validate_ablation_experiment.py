#!/usr/bin/env python3
"""Validate a read-only community Feature-ablation experiment declaration."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = CANDIDATE_ROOT / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from pawbench_agentscope._portable_taxonomy import (  # noqa: E402
    FEATURE_IDS,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
)
from pawbench_agentscope._atomic_io import read_text_no_follow  # noqa: E402


SPEC_SCHEMA_VERSION = "harness-core-ablation-experiment/v1"
VALIDATION_SCHEMA_VERSION = "harness-core-ablation-experiment-validation/v1"
MAX_SPEC_BYTES = 2 * 1024 * 1024
MAX_TASKS = 10_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPERIMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")

TOP_FIELDS = {
    "schema_version",
    "experiment_id",
    "status",
    "claim_level",
    "taxonomy_version",
    "hypothesis",
    "task_set",
    "runtime_identity",
    "budget",
    "variants",
    "evaluation",
    "governance",
}
HYPOTHESIS_FIELDS = {
    "attribution_code",
    "feature_id",
    "statement",
    "evidence_receipts",
    "expected_fix_task_ids",
    "regression_risk_task_ids",
}
TASK_SET_FIELDS = {
    "snapshot_sha256",
    "calibration_task_ids",
    "validation_task_ids",
    "held_out_task_ids",
    "domain_by_task_id",
}
RUNTIME_FIELDS = {
    "harness_name",
    "harness_version",
    "feature_manifest_sha256",
    "model_provider",
    "model_id",
    "model_config_sha256",
    "environment",
    "environment_config_sha256",
}
BUDGET_FIELDS = {
    "trials_per_task_variant",
    "max_model_calls_per_trial",
    "max_input_tokens_per_trial",
    "max_output_tokens_per_trial",
    "timeout_seconds_per_trial",
}
VARIANT_FIELDS = {"id", "enabled_feature_ids", "disabled_feature_ids"}
EVALUATION_FIELDS = {
    "primary_metric",
    "minimum_trials",
    "decisive_wilson_lower_bound",
    "matched_task_level_baseline",
    "require_zero_false_acceptance_regressions",
}
GOVERNANCE_FIELDS = {
    "human_approval_required",
    "approval_receipt_sha256",
    "core_mutation_allowed",
    "reasoning_read_only",
    "analyzers_authoritative",
}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _as_object(value: Any, field: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{field} must be an object")
        return None
    return value


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], field: str, errors: list[str]
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append(f"{field} is missing fields: {missing}")
    if extra:
        errors.append(f"{field} has unknown fields: {extra}")


def _string(
    value: Any,
    field: str,
    errors: list[str],
    *,
    maximum: int = 512,
) -> str | None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        errors.append(f"{field} must be a non-empty string of at most {maximum} characters")
        return None
    return value


def _sha256(value: Any, field: str, errors: list[str]) -> str | None:
    text = _string(value, field, errors, maximum=64)
    if text is not None and not SHA256_RE.fullmatch(text):
        errors.append(f"{field} must be a lowercase SHA-256 digest")
        return None
    return text


def _positive_int(
    value: Any,
    field: str,
    errors: list[str],
    *,
    maximum: int,
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        errors.append(f"{field} must be an integer in [1, {maximum}]")
        return None
    return value


def _string_list(
    value: Any,
    field: str,
    errors: list[str],
    *,
    maximum_items: int = MAX_TASKS,
    item_maximum: int = 256,
) -> list[str] | None:
    if not isinstance(value, list) or len(value) > maximum_items:
        errors.append(f"{field} must be an array with at most {maximum_items} items")
        return None
    if not all(isinstance(item, str) and 0 < len(item) <= item_maximum for item in value):
        errors.append(f"{field} contains an invalid string")
        return None
    if len(value) != len(set(value)):
        errors.append(f"{field} contains duplicate values")
        return None
    return value


def _validate_hypothesis(value: Any, errors: list[str]) -> dict[str, Any]:
    obj = _as_object(value, "hypothesis", errors)
    if obj is None:
        return {}
    _exact_fields(obj, HYPOTHESIS_FIELDS, "hypothesis", errors)
    code = obj.get("attribution_code")
    feature = obj.get("feature_id")
    if code not in H_TO_FEATURES:
        errors.append("hypothesis.attribution_code must be one of H1-H5")
    if feature not in FEATURE_IDS:
        errors.append("hypothesis.feature_id must be a current Feature ID")
    if code in H_TO_FEATURES and feature in FEATURE_IDS and feature not in H_TO_FEATURES[code]:
        errors.append(f"hypothesis.feature_id {feature} is not owned by {code}")
    _string(obj.get("statement"), "hypothesis.statement", errors, maximum=2_000)

    receipts = obj.get("evidence_receipts")
    if not isinstance(receipts, list) or not 1 <= len(receipts) <= 100:
        errors.append("hypothesis.evidence_receipts must contain 1-100 receipts")
    else:
        seen: set[tuple[str, str]] = set()
        for index, receipt in enumerate(receipts):
            field = f"hypothesis.evidence_receipts[{index}]"
            item = _as_object(receipt, field, errors)
            if item is None:
                continue
            _exact_fields(item, {"kind", "sha256"}, field, errors)
            kind = item.get("kind")
            if kind not in {"reasoning", "trace", "judge", "artifact", "prior_experiment"}:
                errors.append(f"{field}.kind is invalid")
            digest = _sha256(item.get("sha256"), f"{field}.sha256", errors)
            if isinstance(kind, str) and digest is not None:
                identity = (kind, digest)
                if identity in seen:
                    errors.append(f"{field} duplicates an earlier receipt")
                seen.add(identity)

    expected = _string_list(
        obj.get("expected_fix_task_ids"),
        "hypothesis.expected_fix_task_ids",
        errors,
    )
    risks = _string_list(
        obj.get("regression_risk_task_ids"),
        "hypothesis.regression_risk_task_ids",
        errors,
    )
    return {
        "code": code,
        "feature": feature,
        "expected": expected or [],
        "risks": risks or [],
    }


def _validate_task_set(value: Any, errors: list[str]) -> dict[str, Any]:
    obj = _as_object(value, "task_set", errors)
    if obj is None:
        return {"calibration": [], "validation": [], "held_out": [], "domains": {}}
    _exact_fields(obj, TASK_SET_FIELDS, "task_set", errors)
    _sha256(obj.get("snapshot_sha256"), "task_set.snapshot_sha256", errors)
    groups = {
        "calibration": _string_list(
            obj.get("calibration_task_ids"), "task_set.calibration_task_ids", errors
        )
        or [],
        "validation": _string_list(
            obj.get("validation_task_ids"), "task_set.validation_task_ids", errors
        )
        or [],
        "held_out": _string_list(
            obj.get("held_out_task_ids"), "task_set.held_out_task_ids", errors
        )
        or [],
    }
    owners: dict[str, str] = {}
    for group, task_ids in groups.items():
        for task_id in task_ids:
            if task_id in owners:
                errors.append(
                    f"task {task_id!r} appears in both {owners[task_id]} and {group}"
                )
            owners[task_id] = group
    if not owners:
        errors.append("task_set must contain at least one task")
    domain_value = obj.get("domain_by_task_id")
    domains: dict[str, str] = {}
    if not isinstance(domain_value, Mapping) or len(domain_value) > MAX_TASKS:
        errors.append(f"task_set.domain_by_task_id must be an object with at most {MAX_TASKS} entries")
    else:
        for task_id, domain in domain_value.items():
            if not isinstance(task_id, str) or not 0 < len(task_id) <= 256:
                errors.append("task_set.domain_by_task_id has an invalid task ID")
                continue
            if domain not in {"UA", "WS", "MA"}:
                errors.append(f"task_set.domain_by_task_id[{task_id!r}] has an invalid domain")
                continue
            domains[task_id] = domain
            expected_domain = next(
                (name for prefix, name in (("ua-", "UA"), ("ws-", "WS"), ("ma-", "MA")) if task_id.startswith(prefix)),
                None,
            )
            if expected_domain is not None and domain != expected_domain:
                errors.append(
                    f"task_set.domain_by_task_id[{task_id!r}] conflicts with its V2 prefix"
                )
        missing_domains = sorted(set(owners) - set(domains))
        extra_domains = sorted(set(domains) - set(owners))
        if missing_domains:
            errors.append(f"task_set.domain_by_task_id is missing tasks: {missing_domains}")
        if extra_domains:
            errors.append(f"task_set.domain_by_task_id has unknown tasks: {extra_domains}")
    return {**groups, "domains": domains}


def _validate_runtime(value: Any, errors: list[str]) -> None:
    obj = _as_object(value, "runtime_identity", errors)
    if obj is None:
        return
    _exact_fields(obj, RUNTIME_FIELDS, "runtime_identity", errors)
    if obj.get("harness_name") != "AgentScope-Lab":
        errors.append("runtime_identity.harness_name must be AgentScope")
    for field, maximum in (
        ("harness_version", 64),
        ("model_provider", 128),
        ("model_id", 256),
    ):
        _string(obj.get(field), f"runtime_identity.{field}", errors, maximum=maximum)
    for field in (
        "feature_manifest_sha256",
        "model_config_sha256",
        "environment_config_sha256",
    ):
        _sha256(obj.get(field), f"runtime_identity.{field}", errors)
    if obj.get("environment") not in {"local", "harbor"}:
        errors.append("runtime_identity.environment must be local or harbor")

    manifest = CANDIDATE_ROOT / "feature_manifest.json"
    current_digest = _sha256_bytes(manifest.read_bytes())
    declared_digest = obj.get("feature_manifest_sha256")
    if SHA256_RE.fullmatch(str(declared_digest or "")) and declared_digest != current_digest:
        errors.append("runtime_identity.feature_manifest_sha256 does not match this checkout")


def _validate_budget(value: Any, errors: list[str]) -> dict[str, Any]:
    obj = _as_object(value, "budget", errors)
    if obj is None:
        return {}
    _exact_fields(obj, BUDGET_FIELDS, "budget", errors)
    parsed = {
        "trials": _positive_int(
            obj.get("trials_per_task_variant"),
            "budget.trials_per_task_variant",
            errors,
            maximum=1_000,
        ),
        "calls": _positive_int(
            obj.get("max_model_calls_per_trial"),
            "budget.max_model_calls_per_trial",
            errors,
            maximum=10_000,
        ),
        "input_tokens": _positive_int(
            obj.get("max_input_tokens_per_trial"),
            "budget.max_input_tokens_per_trial",
            errors,
            maximum=10_000_000,
        ),
        "output_tokens": _positive_int(
            obj.get("max_output_tokens_per_trial"),
            "budget.max_output_tokens_per_trial",
            errors,
            maximum=10_000_000,
        ),
    }
    timeout = obj.get("timeout_seconds_per_trial")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 < timeout <= 86_400
    ):
        errors.append("budget.timeout_seconds_per_trial must be finite and in (0, 86400]")
    return parsed


def _validate_variants(value: Any, target: Any, errors: list[str]) -> None:
    if not isinstance(value, list) or len(value) != 2:
        errors.append("variants must contain exactly all_features_on and single_feature_off")
        return
    variants: dict[str, Mapping[str, Any]] = {}
    for index, variant in enumerate(value):
        field = f"variants[{index}]"
        obj = _as_object(variant, field, errors)
        if obj is None:
            continue
        _exact_fields(obj, VARIANT_FIELDS, field, errors)
        variant_id = obj.get("id")
        if variant_id not in {"all_features_on", "single_feature_off"}:
            errors.append(f"{field}.id is invalid")
            continue
        if variant_id in variants:
            errors.append(f"variants contains duplicate id {variant_id}")
        variants[str(variant_id)] = obj

    if set(variants) != {"all_features_on", "single_feature_off"}:
        errors.append("variants must use both canonical variant IDs")
        return
    feature_set = set(FEATURE_IDS)
    expected = {
        "all_features_on": (feature_set, set()),
        "single_feature_off": (feature_set - {target}, {target}),
    }
    for variant_id, obj in variants.items():
        enabled = _string_list(
            obj.get("enabled_feature_ids"),
            f"variants.{variant_id}.enabled_feature_ids",
            errors,
            maximum_items=len(FEATURE_IDS),
            item_maximum=8,
        )
        disabled = _string_list(
            obj.get("disabled_feature_ids"),
            f"variants.{variant_id}.disabled_feature_ids",
            errors,
            maximum_items=len(FEATURE_IDS),
            item_maximum=8,
        )
        if enabled is None or disabled is None:
            continue
        unknown = (set(enabled) | set(disabled)) - feature_set
        if unknown:
            errors.append(f"variants.{variant_id} has unknown Feature IDs: {sorted(unknown)}")
        expected_enabled, expected_disabled = expected[variant_id]
        if set(enabled) != expected_enabled or set(disabled) != expected_disabled:
            errors.append(
                f"variants.{variant_id} does not match the canonical one-Feature intervention"
            )


def _validate_evaluation(value: Any, errors: list[str]) -> dict[str, Any]:
    obj = _as_object(value, "evaluation", errors)
    if obj is None:
        return {}
    _exact_fields(obj, EVALUATION_FIELDS, "evaluation", errors)
    if obj.get("primary_metric") not in {"task_acceptance", "judge_score", "verifier_score"}:
        errors.append("evaluation.primary_metric is invalid")
    minimum = _positive_int(
        obj.get("minimum_trials"), "evaluation.minimum_trials", errors, maximum=1_000
    )
    threshold = obj.get("decisive_wilson_lower_bound")
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not 0 <= threshold < 1
    ):
        errors.append("evaluation.decisive_wilson_lower_bound must be finite and in [0, 1)")
    baseline = obj.get("matched_task_level_baseline")
    if baseline not in {"none", "parallel_sampling", "sequential_refinement"}:
        errors.append("evaluation.matched_task_level_baseline is invalid")
    if obj.get("require_zero_false_acceptance_regressions") is not True:
        errors.append("evaluation.require_zero_false_acceptance_regressions must be true")
    return {"minimum": minimum, "baseline": baseline}


def _validate_governance(value: Any, status: Any, errors: list[str]) -> None:
    obj = _as_object(value, "governance", errors)
    if obj is None:
        return
    _exact_fields(obj, GOVERNANCE_FIELDS, "governance", errors)
    expected = {
        "human_approval_required": True,
        "core_mutation_allowed": False,
        "reasoning_read_only": True,
        "analyzers_authoritative": False,
    }
    for field, expected_value in expected.items():
        if obj.get(field) is not expected_value:
            errors.append(f"governance.{field} must be {str(expected_value).lower()}")
    approval = obj.get("approval_receipt_sha256")
    if status == "proposed":
        if approval is not None:
            errors.append("governance.approval_receipt_sha256 must be null while proposed")
    elif status == "approved":
        if _sha256(
            approval,
            "governance.approval_receipt_sha256",
            errors,
        ) is None:
            errors.append("approved status requires an external approval receipt")


def validate_spec(payload: Any) -> tuple[list[str], dict[str, Any]]:
    """Return semantic validation errors and a compact, non-authoritative summary."""

    errors: list[str] = []
    obj = _as_object(payload, "document", errors)
    if obj is None:
        return errors, {}
    _exact_fields(obj, TOP_FIELDS, "document", errors)
    if obj.get("schema_version") != SPEC_SCHEMA_VERSION:
        errors.append(f"schema_version must be {SPEC_SCHEMA_VERSION}")
    experiment_id = obj.get("experiment_id")
    if not isinstance(experiment_id, str) or not EXPERIMENT_ID_RE.fullmatch(experiment_id):
        errors.append("experiment_id must match ^[a-z0-9][a-z0-9._-]{2,127}$")
    if obj.get("status") not in {"proposed", "approved"}:
        errors.append("status must be proposed or approved")
    claim_level = obj.get("claim_level")
    if claim_level not in {"contract", "reproduction", "reliability", "generalization"}:
        errors.append("claim_level is invalid")
    if obj.get("taxonomy_version") != TAXONOMY_VERSION:
        errors.append(f"taxonomy_version must be {TAXONOMY_VERSION}")

    hypothesis = _validate_hypothesis(obj.get("hypothesis"), errors)
    tasks = _validate_task_set(obj.get("task_set"), errors)
    _validate_runtime(obj.get("runtime_identity"), errors)
    budget = _validate_budget(obj.get("budget"), errors)
    _validate_variants(obj.get("variants"), hypothesis.get("feature"), errors)
    evaluation = _validate_evaluation(obj.get("evaluation"), errors)
    _validate_governance(obj.get("governance"), obj.get("status"), errors)

    all_tasks = set(tasks["calibration"] + tasks["validation"] + tasks["held_out"])
    expected = set(hypothesis.get("expected", []))
    risks = set(hypothesis.get("risks", []))
    if expected - all_tasks:
        errors.append(f"expected-fix tasks are absent from task_set: {sorted(expected - all_tasks)}")
    if risks - all_tasks:
        errors.append(f"regression-risk tasks are absent from task_set: {sorted(risks - all_tasks)}")
    if expected & set(tasks["held_out"]):
        errors.append("expected-fix tasks may not disclose the held-out task set")
    if claim_level in {"reproduction", "reliability", "generalization"} and not expected:
        errors.append(f"claim_level {claim_level} requires at least one expected-fix task")
    if claim_level in {"reliability", "generalization"}:
        if (budget.get("trials") or 0) < 5 or (evaluation.get("minimum") or 0) < 5:
            errors.append(f"claim_level {claim_level} requires at least five trials")
    if (
        budget.get("trials") is not None
        and evaluation.get("minimum") is not None
        and budget["trials"] < evaluation["minimum"]
    ):
        errors.append("budget.trials_per_task_variant is below evaluation.minimum_trials")
    if claim_level == "generalization":
        if not tasks["validation"] or not tasks["held_out"]:
            errors.append("generalization requires non-empty validation and held-out task sets")
        if evaluation.get("baseline") == "none":
            errors.append("generalization requires a matched task-level baseline")

    summary = {
        "experiment_id": experiment_id,
        "status": obj.get("status"),
        "claim_level": claim_level,
        "attribution_code": hypothesis.get("code"),
        "feature_id": hypothesis.get("feature"),
        "task_counts": {
            key: len(tasks[key]) for key in ("calibration", "validation", "held_out")
        },
        "domain_counts": {
            domain: sum(value == domain for value in tasks["domains"].values())
            for domain in ("UA", "WS", "MA")
        },
        "trials_per_task_variant": budget.get("trials"),
        "matched_task_level_baseline": evaluation.get("baseline"),
        "authority": "validation_only",
    }
    return errors, summary


def load_and_validate(path: Path) -> dict[str, Any]:
    """Load one bounded JSON declaration and return a machine-readable receipt."""

    if path.is_symlink() or not path.is_file():
        raise ValueError(f"spec must be a regular, non-symlink file: {path}")
    # Open and verify the same descriptor so a path swap cannot redirect the
    # validator to a symlink, FIFO, or device after the initial path check.
    raw = read_text_no_follow(path, max_bytes=MAX_SPEC_BYTES).encode("utf-8")
    payload = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_nonfinite,
    )
    errors, summary = validate_spec(payload)
    schema_path = CANDIDATE_ROOT / "community_demo" / "ablation_experiment.schema.json"
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "ok": not errors,
        "spec_sha256": _sha256_bytes(raw),
        "schema_sha256": _sha256_bytes(schema_path.read_bytes()),
        **summary,
        "checks": {
            "strict_json": True,
            "current_taxonomy": not any(
                "taxonomy_version" in error or "feature_manifest_sha256" in error
                for error in errors
            ),
            "single_feature_intervention": not any(
                error.startswith("variants") or "one-Feature" in error for error in errors
            ),
            "disjoint_task_partitions": not any("appears in both" in error for error in errors),
            "matched_budget_declared": not any(
                error.startswith("budget.")
                or error.startswith("evaluation.minimum_trials")
                or "matched task-level baseline" in error
                for error in errors
            ),
            "fixed_core_governance": not any(error.startswith("governance.") for error in errors),
        },
        "errors": errors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="experiment JSON to validate (read only)")
    args = parser.parse_args(argv)
    try:
        receipt = load_and_validate(args.spec.expanduser().absolute())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        receipt = {
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "ok": False,
            "authority": "validation_only",
            "errors": [str(exc)],
        }
    print(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
