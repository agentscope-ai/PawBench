from __future__ import annotations

import re

from pawbench_agentscope.features import H_TO_FEATURES


def candidate_harness_codes(record: dict) -> list[str]:
    codes: set[str] = set()
    validation = str(record.get("validation", ""))
    features = set(record.get("features", []))
    if record.get("validator_passed") is False:
        codes.add("H4")
    if "missing workspace/" in validation or "missing_artifacts" in validation:
        codes.add("H2")
    if "semantic_validator" in validation or record.get("verifier_ok") is False:
        codes.add("H4")
    if "without_F1_1" == record.get("config"):
        codes.add("H1")
    if "F2.2" not in features:
        codes.add("H2")
    if re.search(
        r"invalid api key|authentication failed|model not found|quota|rate.?limit|\b429\b|service unavailable|provider outage|dns|tls|connection refused",
        validation,
        flags=re.I,
    ):
        codes.add("Ex-3")
    elif "Exception" in validation or "RuntimeError" in validation:
        codes.add("H3")
    return sorted(codes)


def feature_delta_for_h_code(h_code: str) -> tuple[str, ...]:
    return H_TO_FEATURES[h_code]
