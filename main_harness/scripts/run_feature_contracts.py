from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.feature_taxonomy import (  # noqa: E402
    CODE_TABLE,
    FEATURE_IDS,
    FEATURE_NAMES,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
)
from scripts.paths import OUTPUT_RESULTS  # noqa: E402


RESULTS = OUTPUT_RESULTS


@dataclass(frozen=True)
class FeatureConfig:
    enabled: frozenset[str]

    @classmethod
    def all_enabled(cls) -> "FeatureConfig":
        return cls(frozenset(FEATURE_IDS))

    def without(self, *feature_ids: str) -> "FeatureConfig":
        return FeatureConfig(frozenset(self.enabled - set(feature_ids)))


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_paths(*, include_legacy: bool = False) -> list[Path]:
    paths = sorted((ROOT / "candidates").glob("*/feature_manifest.json"))
    if include_legacy:
        return paths
    return [path for path in paths if load_manifest(path).get("taxonomy_version") == TAXONOMY_VERSION]


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    candidate = manifest.get("candidate") or "<missing>"
    if manifest.get("taxonomy_version") != TAXONOMY_VERSION:
        return [f"{candidate}: taxonomy_version must be {TAXONOMY_VERSION}"]
    features = manifest.get("features")
    if not isinstance(features, dict):
        return [f"{candidate}: features must be an object"]

    missing = sorted(set(FEATURE_IDS) - set(features))
    extra = sorted(set(features) - set(FEATURE_IDS))
    if missing:
        errors.append(f"{candidate}: missing features {missing}")
    if extra:
        errors.append(f"{candidate}: unknown features {extra}")

    required_fields = {
        "name",
        "h_codes",
        "switch_type",
        "implementation_point",
        "trace_event",
        "evidence_level",
        "expected_effect",
        "known_gap",
        "switch_contract",
        "trace_evidence",
    }
    for feature_id, item in features.items():
        if not isinstance(item, dict):
            errors.append(f"{candidate}:{feature_id}: feature entry must be an object")
            continue
        absent = sorted(required_fields - set(item))
        if absent:
            errors.append(f"{candidate}:{feature_id}: missing fields {absent}")
        if FEATURE_NAMES.get(feature_id) != item.get("name"):
            errors.append(f"{candidate}:{feature_id}: name does not match canonical taxonomy")
        for code in item.get("h_codes") or []:
            if code not in CODE_TABLE:
                errors.append(f"{candidate}:{feature_id}: unknown code {code}")

    declared_h = manifest.get("h_code_mapping")
    if not isinstance(declared_h, dict):
        errors.append(f"{candidate}: h_code_mapping must be an object")
    elif set(declared_h) != set(H_TO_FEATURES):
        errors.append(f"{candidate}: h_code_mapping must contain exactly H1-H5")
    else:
        for h_code, features_for_h in H_TO_FEATURES.items():
            if tuple(declared_h[h_code]) != features_for_h:
                errors.append(f"{candidate}:{h_code}: mapping does not match canonical taxonomy")
    if manifest.get("external_error_policy", {}).get("automatic_feature_mapping") is not False:
        errors.append(f"{candidate}: Ex-3 must not map automatically to an F-code")
    return errors


def feature_events(manifest: dict[str, Any], config: FeatureConfig) -> list[dict[str, Any]]:
    return [
        {
            "type": manifest["features"][feature_id]["trace_event"],
            "feature": feature_id,
            "implementation_point": manifest["features"][feature_id]["implementation_point"],
        }
        for feature_id in sorted(config.enabled)
    ]


def simulate_case(
    manifest: dict[str, Any],
    case: str,
    config: FeatureConfig,
    *,
    semantic_failure: bool = False,
) -> dict[str, Any]:
    selected_tool_visible = "F2.2" in config.enabled
    verifier_ok = not semantic_failure
    completion_ok = True
    accepted = completion_ok and (verifier_ok if "F4.3" in config.enabled else True)
    return {
        "candidate": manifest["candidate"],
        "case": case,
        "features_enabled": sorted(config.enabled),
        "feature_events": feature_events(manifest, config),
        "selected_tool_visible": selected_tool_visible,
        "verifier_reported": True,
        "verifier_ok": verifier_ok,
        "verification_gated": "F4.3" in config.enabled,
        "diagnostic_trace": "F4.1" in config.enabled,
        "outer_audit": True,
        "accepted": accepted,
    }


def run_manifest_contract(manifest: dict[str, Any], disabled: set[str]) -> dict[str, Any]:
    enabled = FeatureConfig.all_enabled().without(*disabled)
    no_availability = enabled.without("F2.2")
    no_verification_gate = enabled.without("F4.3")
    no_diagnostic_trace = enabled.without("F4.1")
    cases = [
        simulate_case(manifest, "all_enabled_minus_cli_disabled", enabled),
        simulate_case(manifest, "without_F2_2_selected_tool_hidden", no_availability),
        simulate_case(manifest, "without_F4_3_verification_not_gating", no_verification_gate, semantic_failure=True),
        simulate_case(manifest, "without_F4_1_outer_audit_retained", no_diagnostic_trace),
    ]
    checks = {
        "selected_tool_can_be_hidden": cases[1]["selected_tool_visible"] is False,
        "verification_still_reports_but_does_not_gate": (
            cases[2]["verifier_reported"]
            and cases[2]["verifier_ok"] is False
            and cases[2]["accepted"] is True
        ),
        "outer_audit_survives_trace_ablation": (
            cases[3]["diagnostic_trace"] is False and cases[3]["outer_audit"] is True
        ),
        "each_h_has_2_to_3_candidates": all(2 <= len(items) <= 3 for items in H_TO_FEATURES.values()),
        "all_features_have_implementation_points": all(
            manifest["features"][feature_id]["implementation_point"] for feature_id in FEATURE_IDS
        ),
    }
    return {
        "candidate": manifest["candidate"],
        "taxonomy_version": manifest["taxonomy_version"],
        "checks": checks,
        "ok": all(checks.values()),
        "h_code_mapping": {key: list(value) for key, value in H_TO_FEATURES.items()},
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate active Harness-core v2 feature contracts.")
    parser.add_argument("--candidate", default="all")
    parser.add_argument("--disable", default="")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    disabled = {item.strip() for item in args.disable.split(",") if item.strip()}
    unknown_disabled = sorted(disabled - set(FEATURE_IDS))
    if unknown_disabled:
        raise SystemExit(f"Unknown disabled feature ids: {unknown_disabled}")

    paths = manifest_paths()
    if args.candidate != "all":
        paths = [path for path in paths if path.parent.name == args.candidate]
    manifests = [load_manifest(path) | {"manifest_path": str(path)} for path in paths]
    validation_errors = [error for manifest in manifests for error in validate_manifest(manifest)]
    contracts = [] if validation_errors else [run_manifest_contract(manifest, disabled) for manifest in manifests]
    summary = {
        "generated_at": now(),
        "taxonomy_version": TAXONOMY_VERSION,
        "candidate_count": len(manifests),
        "disabled_features": sorted(disabled),
        "schema": {
            "features": list(FEATURE_IDS),
            "h_to_features": {key: list(value) for key, value in H_TO_FEATURES.items()},
            "external_error": "Ex-3",
        },
        "validation_errors": validation_errors,
        "contracts": contracts,
        "ok": bool(manifests) and not validation_errors and all(contract["ok"] for contract in contracts),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "feature_contracts__pawbench_v2__local__taxonomy_v2.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
