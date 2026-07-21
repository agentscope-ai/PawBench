from __future__ import annotations

import json
from pathlib import Path

import pytest

from pawbench_agentscope.domain_profiles import (
    DOMAIN_CODES,
    load_domain_profiles,
    profile_for_task,
)
from pawbench_agentscope import domain_profiles
from pawbench_agentscope.features import FEATURE_IDS


REPO_ROOT = Path(__file__).resolve().parents[4]
V2_DATA_ROOT = REPO_ROOT / "main_harness" / "Data" / "data_v2"
PUBLIC_PROFILE_PATH = Path(__file__).resolve().parents[1] / "domain_profiles.json"


def test_catalog_is_priority_only_and_uses_existing_features() -> None:
    catalog = load_domain_profiles()
    assert catalog.prioritization_only is True
    assert tuple(catalog.by_code()) == DOMAIN_CODES
    assert sum(len(profile.known_v2_prefixes) for profile in catalog.profiles) == 8
    for profile in catalog.profiles:
        assert set(profile.priority_feature_ids) <= set(FEATURE_IDS)
        assert len(profile.priority_feature_ids) == len(set(profile.priority_feature_ids))


def test_packaged_catalog_matches_public_contract() -> None:
    public = json.loads(PUBLIC_PROFILE_PATH.read_text(encoding="utf-8"))
    assert load_domain_profiles().model_dump(mode="json") == public


def test_all_local_v2_tasks_resolve_to_the_expected_profile() -> None:
    if not V2_DATA_ROOT.is_dir():
        pytest.skip("PawBench V2 task data is intentionally excluded from the agent PR")
    task_ids = sorted(path.name for path in V2_DATA_ROOT.iterdir() if path.is_dir())
    assert len(task_ids) == 8
    assert {profile_for_task(task_id).code for task_id in task_ids} == {"UA", "WS", "MA"}
    for task_id in task_ids:
        profile = profile_for_task(task_id)
        assert any(task_id.startswith(f"{prefix}-") for prefix in profile.known_v2_prefixes)


def test_catalog_rejects_new_or_unknown_feature_ids(tmp_path: Path) -> None:
    source = load_domain_profiles().model_dump()
    source["profiles"][0]["priority_feature_groups"][0]["feature_ids"].append("F6.1")
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown Features"):
        load_domain_profiles(path)


def test_catalog_rejects_behavior_switch_semantics(tmp_path: Path) -> None:
    source = load_domain_profiles().model_dump()
    source["prioritization_only"] = False
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="prioritization-only"):
        load_domain_profiles(path)


def test_unknown_family_is_not_silently_guessed() -> None:
    with pytest.raises(ValueError, match="no UA/WS/MA family prefix"):
        profile_for_task("other-task-001")


def test_catalog_loader_rejects_symlink_duplicate_keys_and_oversize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = tmp_path / "external.json"
    external.write_text(PUBLIC_PROFILE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    link = tmp_path / "profiles.json"
    link.symlink_to(external)
    with pytest.raises(ValueError, match="must not be a symlink"):
        load_domain_profiles(link)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"one","schema_version":"two"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_domain_profiles(duplicate)

    monkeypatch.setattr(domain_profiles, "MAX_DOMAIN_PROFILE_BYTES", 4)
    with pytest.raises(ValueError, match="input exceeds 4 bytes"):
        load_domain_profiles(external)
