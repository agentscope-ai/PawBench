from __future__ import annotations

from scripts import bridge_attribution_to_harness_core as bridge
from scripts.feature_taxonomy import TAXONOMY_VERSION


def test_feature_switch_key_normalizes_dot_ids() -> None:
    assert bridge.feature_switch_key("F2.3") == "F2_3"


def test_extract_h_codes_keeps_only_harness_codes_in_order() -> None:
    row = {"codes": ["M3", "H4", "H2", "Ex-3"]}
    assert bridge.extract_h_codes(row) == ["H2", "H4"]


def h2_manifest() -> dict:
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "candidate": "TestHarness",
        "features": {
            "F2.1": {"name": "Action Contract"},
            "F2.2": {"name": "Tool Availability"},
            "F2.3": {"name": "Result / Error Feedback"},
        },
        "h_code_mapping": {"H2": ["F2.1", "F2.2", "F2.3"]},
    }


def test_mapping_requires_feature_specific_evidence() -> None:
    mapped = bridge.map_h_code_to_switches(
        "H2",
        h2_manifest(),
        evidence="the required tool was unavailable and error feedback was malformed",
    )
    feature_ids = [item["feature_id"] for item in mapped["switchable_features"]]
    assert feature_ids == ["F2.3", "F2.2"]
    assert mapped["candidate_features"] == ["F2.1", "F2.2", "F2.3"]
    assert mapped["selection_rule"].startswith("zero_or_one")


def test_mapping_returns_no_feature_for_score_only_evidence() -> None:
    mapped = bridge.map_h_code_to_switches("H2", h2_manifest(), evidence="score was zero")
    assert mapped["switchable_features"] == []


def test_bridge_row_recommends_only_budget_for_timeout_evidence() -> None:
    manifest = {
        "taxonomy_version": TAXONOMY_VERSION,
        "candidate": "TestHarness",
        "_candidate_dir": "test",
        "features": {
            "F3.1": {"name": "Completion / Termination"},
            "F3.2": {"name": "Budget / Guards"},
            "F3.3": {"name": "Recovery / Resume"},
        },
        "h_code_mapping": {"H3": ["F3.1", "F3.2", "F3.3"]},
    }
    row = {
        "run_group": "pawbench-v1",
        "harness": "x",
        "model": "y",
        "task_id": "t1",
        "codes": ["H3"],
        "evidence": "timeout after max iterations",
    }
    bridged = bridge.bridge_row(row, manifest)
    assert bridged["recommended_feature_ids"] == ["F3.2"]
    assert bridged["recommended_switch_keys"] == ["F3_2"]


def test_ex3_never_creates_an_automatic_feature() -> None:
    row = {
        "run_group": "pawbench-v1",
        "harness": "x",
        "model": "y",
        "task_id": "t1",
        "codes": ["Ex-3"],
        "evidence": "provider returned persistent 503",
    }
    bridged = bridge.bridge_row(row, h2_manifest())
    assert bridged["external_codes"] == ["Ex-3"]
    assert bridged["recommended_feature_ids"] == []


def test_sample_rounds_uses_unique_rows_when_pool_is_large_enough() -> None:
    rows = [
        {
            "run_group": "v1",
            "harness": "h",
            "model": "m",
            "task_id": f"task-{idx}",
            "path": f"/tmp/{idx}",
            "codes": ["H2"],
        }
        for idx in range(12)
    ]
    samples = bridge.sample_rounds(rows, rounds=3, sample_size=4, seed=7)
    keys = [bridge.row_key(row) for sample in samples for row in sample]
    assert len(keys) == 12
    assert len(set(keys)) == 12
