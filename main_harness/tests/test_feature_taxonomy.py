from __future__ import annotations

from scripts.feature_taxonomy import (
    CODE_TABLE,
    FEATURE_IDS,
    FEATURES,
    H_TO_FEATURES,
    LEGACY_P0_FEATURE_IDS,
    TAXONOMY_VERSION,
    feature_label,
    migrate_legacy_feature_ids,
    select_features_for_evidence,
    validate_taxonomy,
)


def test_taxonomy_v2_is_internally_consistent() -> None:
    assert TAXONOMY_VERSION == "harness_core_v2_20260710"
    assert validate_taxonomy() == []
    assert len(FEATURE_IDS) == 15
    assert set(FEATURE_IDS) == set(FEATURES)
    assert set(H_TO_FEATURES) == {"H1", "H2", "H3", "H4", "H5"}
    assert all(len(feature_ids) == 3 for feature_ids in H_TO_FEATURES.values())


def test_h6_is_removed_and_ex3_has_no_feature_mapping() -> None:
    assert "H6" not in CODE_TABLE
    assert CODE_TABLE["Ex-3"].owner == "External dependency"
    assert "Ex-3" not in H_TO_FEATURES


def test_legacy_profile_is_frozen_and_migration_is_explicit() -> None:
    assert len(LEGACY_P0_FEATURE_IDS) == 10
    migration = migrate_legacy_feature_ids(["F2.3", "F2.4", "F3.1", "F4.1"])
    assert {"F2.2", "F2.3", "F3.1", "F3.2", "F4.3", "F5.3"} <= set(migration["feature_ids"])
    assert migration["lossy_warnings"]
    assert migration["unknown_feature_ids"] == []


def test_evidence_selector_returns_zero_one_or_two_not_whole_family() -> None:
    assert select_features_for_evidence("H2", "score was low") == []
    one = select_features_for_evidence("H2", "the tool schema rejected a valid argument")
    assert [item["feature_id"] for item in one] == ["F2.1"]
    two = select_features_for_evidence(
        "H2",
        "the required tool was unavailable and the returned error feedback was malformed",
    )
    assert [item["feature_id"] for item in two] == ["F2.3", "F2.2"]


def test_feature_labels_use_v2_contract_names() -> None:
    assert feature_label("F2.3") == "F2.3 Result and Error Feedback"
    assert feature_label("F4.3", zh=True) == "F4.3 验证"
    assert all("/" not in entry.name_en for entry in FEATURES.values())
