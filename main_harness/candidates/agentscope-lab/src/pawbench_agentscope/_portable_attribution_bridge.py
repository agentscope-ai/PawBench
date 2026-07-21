"""Portable, pure subset of the Harness-core attribution-to-Feature bridge.

The repository bridge also owns CLI, sampling, API audit, and output paths.  A
Harbor-installed wheel only needs deterministic manifest loading and row
mapping, so those functions live here without repository-path dependencies.
Parity tests compare this subset with the canonical repository implementation.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

from pawbench_agentscope._portable_security import (
    redact_sensitive_text,
    redact_sensitive_value,
)
from pawbench_agentscope._portable_taxonomy import (
    FEATURE_NAMES,
    H_CODE_MEANING,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
    select_features_for_evidence,
)


H_CODES = tuple(H_TO_FEATURES)
ATTRIBUTION_H_TO_FEATURES = H_TO_FEATURES


def feature_switch_key(feature_id: str) -> str:
    return feature_id.replace(".", "_")


def compact_text(value: Any, *, limit: int = 260) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = redact_sensitive_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def row_key(row: dict[str, Any]) -> str:
    fields = [
        row.get("run_group"),
        row.get("harness"),
        row.get("model"),
        row.get("task_id"),
        row.get("path"),
    ]
    return redact_sensitive_text("::".join(str(item or "") for item in fields))


def extract_h_codes(row: dict[str, Any]) -> list[str]:
    codes = row.get("codes") or []
    if not isinstance(codes, list):
        return []
    return [code for code in H_CODES if code in codes]


def load_manifests(candidate: str, *, include_legacy: bool = False) -> dict[str, dict[str, Any]]:
    if candidate not in {"all", "agentscope", "feature_manifest"}:
        raise ValueError(f"No feature manifests found for candidate={candidate!r}")
    resource = resources.files("pawbench_agentscope").joinpath("feature_manifest.json")
    manifest = json.loads(resource.read_text(encoding="utf-8"))
    if not include_legacy and manifest.get("taxonomy_version") != TAXONOMY_VERSION:
        raise ValueError(f"No feature manifests found for candidate={candidate!r}")
    manifest["_manifest_path"] = "package:pawbench_agentscope/feature_manifest.json"
    manifest["_candidate_dir"] = "agentscope"
    return {"agentscope": manifest}


def switchable_features(manifest: dict[str, Any]) -> set[str]:
    features = manifest.get("features")
    if not isinstance(features, dict):
        return set()
    return set(features)


def manifest_h_features(manifest: dict[str, Any], h_code: str) -> set[str]:
    declared = manifest.get("h_code_mapping", {}).get(h_code, [])
    return {item for item in declared if isinstance(item, str)}


def feature_name(manifest: dict[str, Any], feature_id: str) -> str:
    item = manifest.get("features", {}).get(feature_id, {})
    return item.get("name") or FEATURE_NAMES.get(feature_id) or feature_id


def map_h_code_to_switches(
    h_code: str,
    manifest: dict[str, Any],
    *,
    evidence: Any = None,
) -> dict[str, Any]:
    evidence = redact_sensitive_value(evidence)
    switchable = switchable_features(manifest)
    attribution_features = list(ATTRIBUTION_H_TO_FEATURES.get(h_code, ()))
    manifest_features = manifest_h_features(manifest, h_code)
    recommendation_basis = (manifest_features or switchable) & switchable
    selections = select_features_for_evidence(h_code, evidence, max_features=2)
    selected = [item for item in selections if item["feature_id"] in recommendation_basis]
    return {
        "h_code": h_code,
        "h_meaning": H_CODE_MEANING.get(h_code, h_code),
        "candidate_features": [
            feature_id for feature_id in attribution_features if feature_id in recommendation_basis
        ],
        "switchable_features": [
            {
                "feature_id": item["feature_id"],
                "switch_key": feature_switch_key(item["feature_id"]),
                "name": feature_name(manifest, item["feature_id"]),
                "source": "evidence+taxonomy+manifest",
                "evidence_matches": item["evidence_matches"],
                "evidence_score": item["score"],
            }
            for item in selected
        ],
        "unsupported_attribution_features": [
            feature_id for feature_id in attribution_features if feature_id not in switchable
        ],
        "switchable_but_not_candidate_mapped": [
            feature_id
            for feature_id in attribution_features
            if feature_id in switchable and feature_id not in recommendation_basis
        ],
        "manifest_only_features": sorted(
            (manifest_features & switchable) - set(attribution_features)
        ),
        "selection_rule": "zero_or_one_normally; at_most_two_with_distinct_evidence",
    }


def bridge_row(row: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    manifest_version = manifest.get("taxonomy_version")
    if manifest_version not in (None, TAXONOMY_VERSION):
        raise ValueError(
            f"Manifest {manifest.get('candidate')} uses {manifest_version}; expected {TAXONOMY_VERSION}"
        )
    h_codes = extract_h_codes(row)
    mappings = [
        map_h_code_to_switches(h_code, manifest, evidence=row.get("evidence"))
        for h_code in h_codes
    ]
    feature_ids: list[str] = []
    for mapping in mappings:
        for feature in mapping["switchable_features"]:
            feature_ids.append(feature["feature_id"])
    feature_ids = list(dict.fromkeys(feature_ids))
    features_payload = row.get("features") if isinstance(row.get("features"), dict) else {}
    payload = {
        "row_key": row_key(row),
        "candidate": manifest.get("candidate") or manifest.get("_candidate_dir"),
        "candidate_dir": manifest.get("_candidate_dir"),
        "run_group": row.get("run_group"),
        "harness": row.get("harness"),
        "model": row.get("model"),
        "task_id": row.get("task_id"),
        "score": row.get("score"),
        "status": row.get("status"),
        "all_codes": row.get("codes", []),
        "external_codes": [code for code in row.get("codes", []) if code == "Ex-3"],
        "h_codes": h_codes,
        "recommended_feature_ids": feature_ids,
        "recommended_switch_keys": [feature_switch_key(feature_id) for feature_id in feature_ids],
        "h_to_features": mappings,
        "evidence": compact_text(row.get("evidence"), limit=360),
        "feature_hits": features_payload.get("hits", {}),
        "anomaly": features_payload.get("anomaly"),
        "judge": row.get("judge"),
        "path": row.get("path"),
    }
    return redact_sensitive_value(payload)
