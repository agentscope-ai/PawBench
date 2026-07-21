"""Evidence-gated test, attribution, and Feature-ablation orchestration.

This module is intentionally layered *around* the stable ``main_reasoning``
workflow.  It reads accepted reasoning artifacts, reuses the existing passed-
score validator and H-to-F bridge, and executes one controlled Feature-OFF
comparison at a time.  It never changes attribution prompts, validators,
rubrics, or public reasoning output formats.
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
import stat
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, Field, model_validator

from pawbench_agentscope._atomic_io import atomic_write_text, read_text_no_follow

PROJECT_ROOT = Path(__file__).resolve().parents[5]
MAIN_HARNESS_ROOT = PROJECT_ROOT / "main_harness"

from pawbench_agentscope._portable_attribution_bridge import (
    bridge_row,
    load_manifests,
)
from pawbench_agentscope._portable_taxonomy import (
    CODE_TABLE,
    FEATURE_IDS,
    FEATURES,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
)
from pawbench_agentscope._portable_security import redact_sensitive_text, redact_sensitive_value
from pawbench_agentscope.features import snapshot_workspace
from pawbench_agentscope.trajectory_audit import load_native_trace


SCHEMA_VERSION = "harness-core-closed-loop/v1"
OBSERVATION_SCHEMA_VERSION = "harness-core-run-observation/v1"
COMPARISON_SCHEMA_VERSION = "harness-core-ablation-comparison/v1"
SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
H_CODES = tuple(H_TO_FEATURES)
EX_CODES = tuple(code for code, entry in CODE_TABLE.items() if entry.family == "Ex")
M_CODES = tuple(code for code, entry in CODE_TABLE.items() if entry.family == "M")
MAX_REASONING_JSON_BYTES = 32 * 1024 * 1024


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


class RunObservation(BaseModel):
    """Comparable result from one baseline or one single-Feature-OFF run."""

    schema_version: str = OBSERVATION_SCHEMA_VERSION
    task_id: str
    variant: str
    disabled_features: list[str] = Field(default_factory=list)
    passed: bool
    accepted: bool
    verifier_ok: bool
    score: float
    trajectory_path: str | None = None
    trace_path: str | None = None
    event_counts: dict[str, int] = Field(default_factory=dict)
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    artifact_sizes: dict[str, int] = Field(default_factory=dict)
    final_text: str = ""

    @model_validator(mode="after")
    def validate_observation(self) -> "RunObservation":
        _validate_task_id(self.task_id)
        if not self.variant or len(self.variant) > 256:
            raise ValueError("observation variant must be non-empty and at most 256 characters")
        if not math.isfinite(self.score):
            raise ValueError("observation score must be finite")
        if self.passed != (self.accepted and self.verifier_ok):
            raise ValueError("observation passed must equal accepted and verifier_ok")
        if len(self.disabled_features) != len(set(self.disabled_features)):
            raise ValueError("observation disabled_features must not contain duplicates")
        unknown = sorted(set(self.disabled_features) - set(FEATURE_IDS))
        if unknown:
            raise ValueError(f"observation has unknown disabled Features: {unknown}")
        if any(value < 0 for value in self.event_counts.values()):
            raise ValueError("observation event counts must be nonnegative")
        if any(value < 0 for value in self.artifact_sizes.values()):
            raise ValueError("observation artifact sizes must be nonnegative")
        return self


class AblationComparison(BaseModel):
    """Evidence receipt for one all-ON versus one Feature-OFF comparison."""

    schema_version: str = COMPARISON_SCHEMA_VERSION
    task_id: str
    feature_id: str
    status: str
    baseline_score: float
    feature_off_score: float
    score_drop: float
    baseline_passed: bool
    feature_off_passed: bool
    reproduced_observed_failure: bool
    introduced_false_acceptance: bool = False
    changed_artifacts: list[str] = Field(default_factory=list)
    missing_artifacts_when_off: list[str] = Field(default_factory=list)
    event_count_delta: dict[str, int] = Field(default_factory=dict)
    rationale: str


ExperimentExecutor = Callable[[str, str | None], RunObservation]


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"JSON input must not be a symlink: {path}")
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"JSON input must be a regular file: {path}")
    if metadata.st_size > MAX_REASONING_JSON_BYTES:
        raise ValueError(f"JSON input exceeds {MAX_REASONING_JSON_BYTES} bytes: {path}")
    value = json.loads(
        read_text_no_follow(path, max_bytes=MAX_REASONING_JSON_BYTES),
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_nonfinite_json,
    )
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must contain one object: {path}")
    return value


def _atomic_json(path: Path, payload: Any) -> None:
    target = redact_sensitive_value(payload)
    atomic_write_text(
        path,
        json.dumps(
            target,
            ensure_ascii=False,
            indent=2,
            default=str,
            allow_nan=False,
        )
        + "\n",
    )


def _atomic_text(path: Path, text: str) -> None:
    atomic_write_text(path, redact_sensitive_text(text))


def _validate_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not SAFE_TASK_ID.fullmatch(task_id):
        raise ValueError("task_id must be one safe path component")


def _validated_h_codes(
    outcome: Mapping[str, Any],
    *,
    context: str,
) -> list[str]:
    """Return a strict, duplicate-free list of accepted H codes."""

    raw = outcome.get("h_codes", [])
    if not isinstance(raw, list):
        raise ValueError(f"{context} h_codes must be an array")
    if any(not isinstance(code, str) or code not in H_CODES for code in raw):
        raise ValueError(f"{context} h_codes must contain only known H codes")
    if len(raw) != len(set(raw)):
        raise ValueError(f"{context} h_codes must not contain duplicates")
    return list(raw)


def _validated_experiment_features(
    outcome: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    context: str,
) -> list[str]:
    """Validate the H-only, one-Feature-OFF experiment contract."""

    experiments = plan.get("experiments", [])
    if not isinstance(experiments, list):
        raise ValueError(f"{context} experiments must be an array")
    accepted_h_codes = set(_validated_h_codes(outcome, context=context))
    feature_ids: list[str] = []
    for index, experiment in enumerate(experiments):
        if not isinstance(experiment, Mapping):
            raise ValueError(f"{context} experiment {index} must be an object")
        feature_id = experiment.get("feature_id")
        if feature_id not in FEATURE_IDS:
            raise ValueError(f"{context} experiment {index} has an unknown Feature")
        if feature_id in feature_ids:
            raise ValueError(f"{context} experiment {index} duplicates Feature {feature_id}")
        owners = experiment.get("owned_by_h_codes")
        if (
            not isinstance(owners, list)
            or not owners
            or len(owners) != len(set(owners))
            or any(not isinstance(owner, str) or owner not in accepted_h_codes for owner in owners)
            or not any(feature_id in H_TO_FEATURES[owner] for owner in owners)
        ):
            raise ValueError(
                f"{context} experiment {index} is not owned by accepted H evidence"
            )
        if experiment.get("switch") != "OFF" or experiment.get("all_other_features") != "ON":
            raise ValueError(
                f"{context} experiment {index} is not a single-Feature-OFF contract"
            )
        feature_ids.append(str(feature_id))

    if "recommended_feature_ids" in plan:
        recommended = plan.get("recommended_feature_ids")
        if (
            not isinstance(recommended, list)
            or any(feature_id not in FEATURE_IDS for feature_id in recommended)
            or len(recommended) != len(set(recommended))
            or recommended != feature_ids
        ):
            raise ValueError(
                f"{context} recommended_feature_ids must exactly match experiments"
            )
    return feature_ids


def _normalized_code_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if isinstance(raw, str):
            code = raw
            entry: dict[str, Any] = {"code": code}
        elif isinstance(raw, Mapping):
            code = raw.get("code")
            entry = dict(raw)
        else:
            continue
        if not isinstance(code, str) or code not in CODE_TABLE or code in seen:
            continue
        seen.add(code)
        entry["code"] = code
        entries.append(entry)
    rank = {code: index for index, code in enumerate(CODE_TABLE)}
    entries.sort(key=lambda item: rank[item["code"]])
    return entries


def _load_pass_validator() -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    path = (
        PROJECT_ROOT
        / "skills"
        / "pawbench-v2-agentic-grader"
        / "scripts"
        / "validate_passed_score.py"
    )
    spec = importlib.util.spec_from_file_location(
        "harness_core_existing_pass_validator", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load existing passed-score validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    validator = getattr(module, "validate_passed_score", None)
    if not callable(validator):
        raise RuntimeError("existing passed-score validator exposes no callable")
    return validator


def _workspace_document(source_root: Path, task_id: str) -> dict[str, Any]:
    return _read_json(source_root / "workspaces" / task_id / "input" / "x.json")


def _existing_pass_validation(
    reasoning_root: Path,
    source_root: Path,
    task_id: str,
) -> tuple[dict[str, Any], str]:
    receipt_path = (
        reasoning_root / "agentic-audit" / "pass-validations" / f"{task_id}.json"
    )
    if receipt_path.is_file():
        return _read_json(receipt_path), str(receipt_path)
    receipt = _load_pass_validator()(_workspace_document(source_root, task_id))
    return receipt, "existing_pass_validator"


def _pass_receipt_is_clear(receipt: Mapping[str, Any], task_id: str) -> bool:
    """Fail closed on stale, partial, or internally inconsistent pass receipts."""

    if receipt.get("task_id") != task_id or receipt.get("status") != "validated_pass":
        return False
    checks = receipt.get("checks")
    if not isinstance(checks, list):
        return False
    observed: dict[str, str] = {}
    for item in checks:
        if not isinstance(item, Mapping):
            return False
        code = item.get("code")
        status = item.get("status")
        if code not in EX_CODES or code in observed or not isinstance(status, str):
            return False
        observed[str(code)] = status
    audit = receipt.get("audit")
    return (
        observed == {code: "clear" for code in EX_CODES}
        and isinstance(audit, Mapping)
        and audit.get("status") == "ok"
    )


def load_reasoning_outcome(
    reasoning_root: str | Path,
    source_root: str | Path,
    task_id: str,
) -> dict[str, Any]:
    """Load one accepted outcome, preferring the final agentic verdict.

    The returned route is deliberately conservative.  Passed tasks are routed
    through Ex-1/Ex-2/Ex-3 validation.  Failed tasks may enter ablation only if
    the final accepted codes include H1-H5.
    """

    _validate_task_id(task_id)
    reasoning = Path(reasoning_root).expanduser().resolve()
    source = Path(source_root).expanduser().resolve()
    recording_path = reasoning / "recordings" / f"{task_id}.json"
    recording = _read_json(recording_path)
    if recording.get("task_id") != task_id:
        raise ValueError(f"recording task_id mismatch: {recording_path}")

    passed = recording.get("passed") is True
    accepted = recording.get("accepted") is True
    score_value = recording.get("score", 0.0)
    score = float(score_value) if isinstance(score_value, (int, float)) else 0.0
    if not math.isfinite(score):
        raise ValueError(f"recording score must be finite: {recording_path}")
    verdict_path = reasoning / "agentic-audit" / "verdicts" / f"{task_id}.json"
    verdict = _read_json(verdict_path) if verdict_path.is_file() else None

    if verdict is not None:
        if verdict.get("task_id") != task_id:
            raise ValueError(f"agentic verdict task_id mismatch: {verdict_path}")
        code_entries = _normalized_code_entries(verdict.get("codes"))
        reasoning_source = "agentic_final_verdict"
        audit = verdict.get("codex_audit") if isinstance(verdict.get("codex_audit"), Mapping) else {}
        decision = verdict.get("decision")
    else:
        result = recording.get("result") if isinstance(recording.get("result"), Mapping) else {}
        code_entries = _normalized_code_entries(result.get("codes"))
        reasoning_source = "simple_v2_recording"
        audit = {
            "status": recording.get("audit_status"),
            "source": "simple_v2_audit",
        }
        decision = result.get("attribution_status")

    codes = [entry["code"] for entry in code_entries]
    h_codes = [code for code in codes if code in H_CODES]
    ex_codes = [code for code in codes if code in EX_CODES]
    m_codes = [code for code in codes if code in M_CODES]

    pass_validation: dict[str, Any] | None = None
    pass_validation_source: str | None = None
    if not accepted:
        route = "reasoning_rejected"
    elif passed:
        pass_validation, pass_validation_source = _existing_pass_validation(
            reasoning, source, task_id
        )
        route = (
            "pass_validated_no_ablation"
            if _pass_receipt_is_clear(pass_validation, task_id)
            else "pass_requires_review_no_ablation"
        )
    elif h_codes:
        route = "h_feature_ablation"
    else:
        route = "non_h_failure_no_ablation"

    return redact_sensitive_value(
        {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "task_family": recording.get("task_family"),
            "passed": passed,
            "score": score,
            "reasoning_accepted": accepted,
            "decision": decision,
            "codes": codes,
            "code_entries": code_entries,
            "h_codes": h_codes,
            "ex_codes": ex_codes,
            "m_codes": m_codes,
            "route": route,
            "reasoning_source": reasoning_source,
            "reasoning_recording": str(recording_path),
            "agentic_verdict": str(verdict_path) if verdict is not None else None,
            "audit": dict(audit),
            "pass_validation": pass_validation,
            "pass_validation_source": pass_validation_source,
            "automatic_ablation_policy": {
                "H": True,
                "Ex": False,
                "M": False,
            },
        }
    )


def _evidence_payload(outcome: Mapping[str, Any]) -> list[Any]:
    evidence: list[Any] = []
    for entry in outcome.get("code_entries", []):
        if not isinstance(entry, Mapping):
            continue
        for key in ("reason", "rationale"):
            if entry.get(key):
                evidence.append(entry[key])
        if entry.get("evidence"):
            evidence.append(entry["evidence"])
    return evidence


def build_ablation_plan(
    outcome: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map final H evidence to zero-to-two single-Feature-OFF experiments."""

    task_id = str(outcome.get("task_id", ""))
    _validate_task_id(task_id)
    h_codes = [code for code in outcome.get("h_codes", []) if code in H_CODES]
    blocked_codes = [
        code
        for code in outcome.get("codes", [])
        if code in EX_CODES or code in M_CODES
    ]
    selected_manifest = dict(
        manifest or load_manifests("agentscope")["agentscope"]
    )

    if outcome.get("route") != "h_feature_ablation" or not h_codes:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "route": outcome.get("route"),
            "h_codes": h_codes,
            "blocked_non_h_codes": blocked_codes,
            "recommended_feature_ids": [],
            "experiments": [],
            "automatic_ablation_triggered": False,
            "policy": "Only final accepted H codes may trigger Feature ablation.",
        }

    row = {
        "run_group": "closed-loop",
        "harness": "AgentScope-Lab",
        "model": outcome.get("reasoning_source"),
        "task_id": task_id,
        "score": outcome.get("score"),
        "status": outcome.get("decision"),
        "codes": list(outcome.get("codes", [])),
        "evidence": _evidence_payload(outcome),
    }
    bridged = bridge_row(row, selected_manifest)
    feature_ids = [
        feature_id
        for feature_id in bridged.get("recommended_feature_ids", [])
        if feature_id in FEATURE_IDS
    ]
    experiments: list[dict[str, Any]] = []
    for feature_id in feature_ids:
        owners = [h_code for h_code in h_codes if feature_id in H_TO_FEATURES[h_code]]
        mappings = [
            mapping
            for mapping in bridged.get("h_to_features", [])
            if isinstance(mapping, Mapping) and mapping.get("h_code") in owners
        ]
        matches: list[str] = []
        for mapping in mappings:
            for item in mapping.get("switchable_features", []):
                if isinstance(item, Mapping) and item.get("feature_id") == feature_id:
                    matches.extend(str(value) for value in item.get("evidence_matches", []))
        experiments.append(
            {
                "feature_id": feature_id,
                "feature_name": FEATURES[feature_id].name_en,
                "owned_by_h_codes": owners,
                "switch": "OFF",
                "all_other_features": "ON",
                "evidence_matches": list(dict.fromkeys(matches)),
            }
        )

    return redact_sensitive_value(
        {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "route": (
                "single_feature_off"
                if experiments
                else "h_evidence_insufficient_no_ablation"
            ),
            "taxonomy_version": TAXONOMY_VERSION,
            "h_codes": h_codes,
            "blocked_non_h_codes": blocked_codes,
            "recommended_feature_ids": feature_ids,
            "experiments": experiments,
            "automatic_ablation_triggered": bool(experiments),
            "policy": "Each experiment disables exactly one Feature; Ex/M never trigger it.",
            "bridge_receipt": bridged,
        }
    )


def _file_observation(root: Path) -> tuple[dict[str, str], dict[str, int]]:
    snapshot = snapshot_workspace(root)
    return (
        {
            relative: (
                str(receipt["sha256"])
                if isinstance(receipt.get("sha256"), str)
                else f"{receipt.get('hash_status', 'unhashed')}:{int(receipt['size'])}"
            )
            for relative, receipt in snapshot.items()
        },
        {relative: int(receipt["size"]) for relative, receipt in snapshot.items()},
    )


def _trace_event_counts(path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.is_file():
        return {}
    rows, _ = load_native_trace(path)
    for value in rows:
        if isinstance(value, Mapping) and isinstance(value.get("type"), str):
            counts[str(value["type"])] += 1
    return dict(sorted(counts.items()))


def observation_from_harbor(
    result: Mapping[str, Any] | BaseModel,
    *,
    variant: str,
    workspace_root: str | Path,
    disabled_features: Sequence[str] = (),
    score: float | None = None,
) -> RunObservation:
    """Create the comparison surface from one Harbor-bridge result."""

    payload = result.model_dump(mode="json") if isinstance(result, BaseModel) else dict(result)
    verifier = payload.get("verifier") if isinstance(payload.get("verifier"), Mapping) else {}
    accepted = payload.get("accepted") is True
    verifier_ok = verifier.get("ok") is True
    passed = accepted and verifier_ok
    numeric_score = float(score) if score is not None else (1.0 if passed else 0.0)
    if not math.isfinite(numeric_score):
        raise ValueError("observation score must be finite")
    files = payload.get("files") if isinstance(payload.get("files"), Mapping) else {}
    trace_path = Path(str(files.get("trace"))) if files.get("trace") else None
    artifact_hashes, artifact_sizes = _file_observation(
        Path(workspace_root).expanduser().resolve()
    )
    return RunObservation(
        task_id=str(payload.get("task_id")),
        variant=variant,
        disabled_features=sorted(set(disabled_features)),
        passed=passed,
        accepted=accepted,
        verifier_ok=verifier_ok,
        score=numeric_score,
        trajectory_path=str(files.get("trajectory")) if files.get("trajectory") else None,
        trace_path=str(trace_path) if trace_path is not None else None,
        event_counts=_trace_event_counts(trace_path) if trace_path is not None else {},
        artifact_hashes=artifact_hashes,
        artifact_sizes=artifact_sizes,
        final_text=str(payload.get("final_text") or ""),
    )


def compare_feature_off(
    observed: RunObservation,
    baseline: RunObservation,
    feature_off: RunObservation,
    feature_id: str,
    *,
    tolerance: float = 1e-9,
) -> AblationComparison:
    """Decide whether one Feature-OFF run supports the H-to-F hypothesis."""

    if feature_id not in FEATURE_IDS:
        raise ValueError(f"unknown Feature ID: {feature_id}")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("comparison tolerance must be finite and nonnegative")
    if baseline.disabled_features:
        raise ValueError("baseline must keep all Features enabled")
    if feature_off.disabled_features != [feature_id]:
        raise ValueError("Feature-OFF observation must disable exactly the compared Feature")
    if len({observed.task_id, baseline.task_id, feature_off.task_id}) != 1:
        raise ValueError("all observations must belong to the same task")

    score_drop = baseline.score - feature_off.score
    reproduced = (
        not observed.passed
        and (
            not feature_off.passed
            or feature_off.score <= observed.score + tolerance
        )
    )
    observed_false_acceptance = observed.accepted and not observed.verifier_ok
    baseline_false_acceptance = baseline.accepted and not baseline.verifier_ok
    feature_off_false_acceptance = feature_off.accepted and not feature_off.verifier_ok
    introduced_false_acceptance = (
        feature_id == "F4.3"
        and not baseline_false_acceptance
        and feature_off_false_acceptance
    )
    if observed_false_acceptance and feature_off_false_acceptance:
        reproduced = True
    positive_effect = (
        (baseline.passed and not feature_off.passed)
        or score_drop > tolerance
    )
    negative_effect = (
        (feature_off.passed and not baseline.passed)
        or score_drop < -tolerance
    )
    if introduced_false_acceptance and observed_false_acceptance:
        status = "supported"
        rationale = (
            "All-ON safely rejected a verifier failure; F4.3 OFF reproduced "
            "the observed false acceptance."
        )
    elif baseline.passed and positive_effect and reproduced:
        status = "supported"
        rationale = (
            f"All-ON passed; {feature_id} OFF reduced the outcome and reproduced "
            "the observed failure."
        )
    elif negative_effect:
        status = "contradicted"
        rationale = f"{feature_id} OFF improved the outcome relative to all-ON."
    else:
        status = "inconclusive"
        rationale = f"{feature_id} OFF did not produce a decisive outcome change."

    artifact_names = sorted(
        set(baseline.artifact_hashes) | set(feature_off.artifact_hashes)
    )
    changed = [
        name
        for name in artifact_names
        if (
            baseline.artifact_hashes.get(name) != feature_off.artifact_hashes.get(name)
            or baseline.artifact_sizes.get(name) != feature_off.artifact_sizes.get(name)
        )
    ]
    missing = sorted(set(baseline.artifact_hashes) - set(feature_off.artifact_hashes))
    event_names = sorted(set(baseline.event_counts) | set(feature_off.event_counts))
    event_delta = {
        name: feature_off.event_counts.get(name, 0) - baseline.event_counts.get(name, 0)
        for name in event_names
        if feature_off.event_counts.get(name, 0) != baseline.event_counts.get(name, 0)
    }
    return AblationComparison(
        task_id=baseline.task_id,
        feature_id=feature_id,
        status=status,
        baseline_score=baseline.score,
        feature_off_score=feature_off.score,
        score_drop=score_drop,
        baseline_passed=baseline.passed,
        feature_off_passed=feature_off.passed,
        reproduced_observed_failure=reproduced,
        introduced_false_acceptance=introduced_false_acceptance,
        changed_artifacts=changed,
        missing_artifacts_when_off=missing,
        event_count_delta=event_delta,
        rationale=rationale,
    )


def execute_task_plan(
    outcome: Mapping[str, Any],
    plan: Mapping[str, Any],
    observed: RunObservation,
    executor: ExperimentExecutor,
) -> dict[str, Any]:
    """Execute a baseline once, then each planned single-Feature-OFF variant."""

    experiments = plan.get("experiments")
    if not isinstance(experiments, list) or not experiments:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": outcome.get("task_id"),
            "outcome": dict(outcome),
            "ablation_plan": dict(plan),
            "observed": observed.model_dump(mode="json"),
            "execution_status": "not_applicable",
            "baseline": None,
            "comparisons": [],
        }

    feature_ids = _validated_experiment_features(
        outcome,
        plan,
        context="ablation plan",
    )

    baseline = executor("all_features_on", None)
    comparisons: list[dict[str, Any]] = []
    feature_off_runs: list[dict[str, Any]] = []
    for feature_id in feature_ids:
        variant = f"without_{feature_id.replace('.', '_')}"
        off = executor(variant, feature_id)
        comparison = compare_feature_off(observed, baseline, off, feature_id)
        feature_off_runs.append(off.model_dump(mode="json"))
        comparisons.append(comparison.model_dump(mode="json"))

    return redact_sensitive_value(
        {
            "schema_version": SCHEMA_VERSION,
            "task_id": outcome.get("task_id"),
            "outcome": dict(outcome),
            "ablation_plan": dict(plan),
            "observed": observed.model_dump(mode="json"),
            "execution_status": "completed",
            "baseline": baseline.model_dump(mode="json"),
            "feature_off_runs": feature_off_runs,
            "comparisons": comparisons,
        }
    )


def _md(value: Any) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _pass_audit_label(receipt: Mapping[str, Any] | None, *, zh: bool) -> str:
    if not isinstance(receipt, Mapping):
        return "不适用" if zh else "n/a"
    checks = receipt.get("checks") if isinstance(receipt.get("checks"), list) else []
    labels = (
        {"Ex-1": "任务设计", "Ex-2": "Judge", "Ex-3": "外部资源"}
        if zh
        else {"Ex-1": "task design", "Ex-2": "judge", "Ex-3": "external resources"}
    )
    status_labels = {
        "clear": "正常",
        "flagged": "异常",
        "insufficient_evidence": "证据不足",
    } if zh else {}
    values = [
        f"{labels.get(str(item.get('code')), item.get('code'))}="
        f"{status_labels.get(str(item.get('status')), item.get('status'))}"
        for item in checks
        if isinstance(item, Mapping)
    ]
    audit = receipt.get("audit") if isinstance(receipt.get("audit"), Mapping) else {}
    audit_status = str(audit.get("status") or "unknown")
    if zh:
        audit_status = {"ok": "正常", "review": "需复核"}.get(audit_status, audit_status)
    prefix = "通过" if receipt.get("status") == "validated_pass" and zh else (
        "clear" if receipt.get("status") == "validated_pass" else "复核" if zh else "review"
    )
    details = ", ".join(values)
    audit_label = f"审计={audit_status}" if zh else f"audit={audit_status}"
    return f"{prefix}: {details}; {audit_label}" if details else f"{prefix}: {audit_label}"


def _result_label(recording: Mapping[str, Any], *, zh: bool) -> str:
    comparisons = recording.get("comparisons") if isinstance(recording.get("comparisons"), list) else []
    if comparisons:
        return ", ".join(
            (
                f"{item.get('feature_id')}={item.get('status')}"
                + (
                    "（复现误接受）" if zh else " (false acceptance reproduced)"
                )
                if item.get("introduced_false_acceptance") is True
                else f"{item.get('feature_id')}={item.get('status')}"
            )
            for item in comparisons
            if isinstance(item, Mapping)
        )
    plan = recording.get("ablation_plan") if isinstance(recording.get("ablation_plan"), Mapping) else {}
    route = plan.get("route")
    labels_zh = {
        "pass_validated_no_ablation": "通过校验；不消融",
        "pass_requires_review_no_ablation": "通过项需复核；不消融",
        "non_h_failure_no_ablation": "仅 Ex/M；不消融",
        "reasoning_rejected": "归因未接受；不消融",
        "h_evidence_insufficient_no_ablation": "H 证据不足以定位 Feature",
    }
    return labels_zh.get(str(route), str(route)) if zh else str(route)


def _render_report(recordings: Sequence[Mapping[str, Any]], *, zh: bool) -> str:
    title = "Harness-core 测试—归因—Feature 消融闭环" if zh else "Harness-core Test–Attribution–Feature Ablation Loop"
    intro = (
        "仅最终接受的 H 归因可触发单 Feature OFF；Ex/M 永不自动触发消融。"
        if zh
        else "Only final accepted H attributions can trigger single-Feature-OFF runs; Ex/M never trigger ablation automatically."
    )
    headers = (
        ["任务", "测试", "最终归因", "通过项校验", "H→F", "闭环结论"]
        if zh
        else ["Task", "Test", "Final attribution", "Pass validation", "H→F", "Loop result"]
    )
    lines = [f"# {title}", "", intro, "", "| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for recording in recordings:
        outcome = recording.get("outcome") if isinstance(recording.get("outcome"), Mapping) else {}
        plan = recording.get("ablation_plan") if isinstance(recording.get("ablation_plan"), Mapping) else {}
        passed = outcome.get("passed") is True
        test_label = ("通过" if passed else "失败") if zh else ("pass" if passed else "fail")
        codes = ", ".join(outcome.get("codes", [])) or ("无" if zh else "none")
        features = ", ".join(plan.get("recommended_feature_ids", [])) or ("无" if zh else "none")
        row = [
            outcome.get("task_id"),
            f"{test_label} ({float(outcome.get('score', 0.0)):g})",
            codes,
            _pass_audit_label(outcome.get("pass_validation"), zh=zh),
            features,
            _result_label(recording, zh=zh),
        ]
        lines.append("| " + " | ".join(_md(value) for value in row) + " |")

    supported = sum(
        1
        for recording in recordings
        for comparison in recording.get("comparisons", [])
        if isinstance(comparison, Mapping) and comparison.get("status") == "supported"
    )
    experiment_count = sum(len(recording.get("comparisons", [])) for recording in recordings)
    lines += [
        "",
        ("## 汇总" if zh else "## Summary"),
        "",
        (
            f"任务 {len(recordings)} 个；完成单 Feature OFF 对比 {experiment_count} 个；其中 {supported} 个支持归因假设。"
            if zh
            else f"{len(recordings)} tasks; {experiment_count} single-Feature-OFF comparisons; {supported} supported hypotheses."
        ),
        "",
    ]
    return "\n".join(lines)


def write_closed_loop_run(
    output_root: str | Path,
    recordings: Sequence[Mapping[str, Any]],
    *,
    run_name: str | None = None,
) -> dict[str, Any]:
    """Publish one aligned bilingual closed-loop run."""

    requested_output = Path(output_root).expanduser()
    if requested_output.is_symlink():
        raise ValueError(f"closed-loop output must not be a symlink: {requested_output}")
    output = requested_output.resolve()
    if output.exists() and not output.is_dir():
        raise ValueError(f"closed-loop output must be a directory: {output}")
    recordings_root = output / "recordings"
    if recordings_root.is_symlink():
        raise ValueError(f"closed-loop recordings directory must not be a symlink: {recordings_root}")
    ordered = sorted(recordings, key=lambda item: str(item.get("task_id", "")))
    task_ids = [str(recording.get("task_id", "")) for recording in ordered]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("closed-loop recordings must have unique task IDs")
    if recordings_root.exists() and any(recordings_root.iterdir()):
        raise ValueError(f"refusing to mix with existing closed-loop recordings: {recordings_root}")
    for recording in ordered:
        task_id = str(recording.get("task_id", ""))
        _validate_task_id(task_id)
        outcome = recording.get("outcome")
        plan = recording.get("ablation_plan")
        if not isinstance(outcome, Mapping) or not isinstance(plan, Mapping):
            raise ValueError(f"closed-loop recording {task_id} lacks outcome or ablation_plan")
        try:
            _validated_experiment_features(
                outcome,
                plan,
                context=f"closed-loop recording {task_id}",
            )
        except ValueError as exc:
            raise ValueError(
                f"closed-loop recording {task_id} violates H-only single-OFF policy: {exc}"
            ) from exc
        _atomic_json(output / "recordings" / f"{task_id}.json", recording)

    comparisons = [
        comparison
        for recording in ordered
        for comparison in recording.get("comparisons", [])
        if isinstance(comparison, Mapping)
    ]
    route_counts = Counter(
        str(
            (recording.get("ablation_plan") or {}).get("route", "unknown")
            if isinstance(recording.get("ablation_plan"), Mapping)
            else "unknown"
        )
        for recording in ordered
    )
    status_counts = Counter(str(item.get("status", "unknown")) for item in comparisons)
    planned_experiment_count = sum(
        len(recording.get("ablation_plan", {}).get("experiments", []))
        for recording in ordered
        if isinstance(recording.get("ablation_plan"), Mapping)
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_name": run_name or output.name,
        "candidate": "AgentScope-Lab",
        "taxonomy_version": TAXONOMY_VERSION,
        "task_count": len(ordered),
        "route_counts": dict(sorted(route_counts.items())),
        "planned_experiment_count": planned_experiment_count,
        "experiment_count": len(comparisons),
        "comparison_status_counts": dict(sorted(status_counts.items())),
        "policy_checks": {
            "only_h_triggers_ablation": all(
                not recording.get("ablation_plan", {}).get("experiments")
                or bool(recording.get("outcome", {}).get("h_codes"))
                for recording in ordered
            ),
            "ex_m_never_trigger_automatically": all(
                not (
                    recording.get("ablation_plan", {}).get("experiments")
                    and not recording.get("outcome", {}).get("h_codes")
                )
                for recording in ordered
            ),
            "one_feature_off_per_comparison": all(
                len(run.get("disabled_features", [])) == 1
                for recording in ordered
                for run in recording.get("feature_off_runs", [])
                if isinstance(run, Mapping)
            ),
        },
        "tasks": [
            {
                "task_id": recording.get("task_id"),
                "passed": recording.get("outcome", {}).get("passed"),
                "codes": recording.get("outcome", {}).get("codes", []),
                "features": recording.get("ablation_plan", {}).get("recommended_feature_ids", []),
                "comparison_statuses": [
                    item.get("status")
                    for item in recording.get("comparisons", [])
                    if isinstance(item, Mapping)
                ],
            }
            for recording in ordered
        ],
    }
    _atomic_json(output / "summary.json", summary)
    _atomic_text(output / "REPORT_EN.md", _render_report(ordered, zh=False))
    _atomic_text(output / "REPORT_ZH.md", _render_report(ordered, zh=True))
    return summary


__all__ = [
    "AblationComparison",
    "RunObservation",
    "build_ablation_plan",
    "compare_feature_off",
    "execute_task_plan",
    "load_reasoning_outcome",
    "observation_from_harbor",
    "write_closed_loop_run",
]
