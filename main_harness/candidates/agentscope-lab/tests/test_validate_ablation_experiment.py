from __future__ import annotations

import importlib.util
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = CANDIDATE_ROOT / "scripts" / "validate_ablation_experiment.py"
EXAMPLE = CANDIDATE_ROOT / "community_demo" / "ablation_experiment.example.json"
SCHEMA = CANDIDATE_ROOT / "community_demo" / "ablation_experiment.schema.json"
SPEC = importlib.util.spec_from_file_location("pawbench_validate_ablation_experiment", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_example_is_a_valid_non_authoritative_generalization_plan() -> None:
    receipt = MODULE.load_and_validate(EXAMPLE)

    assert receipt["ok"] is True
    assert receipt["claim_level"] == "generalization"
    assert receipt["feature_id"] == "F1.1"
    assert receipt["task_counts"] == {
        "calibration": 1,
        "validation": 1,
        "held_out": 1,
    }
    assert receipt["domain_counts"] == {"UA": 0, "WS": 3, "MA": 0}
    assert receipt["authority"] == "validation_only"
    assert all(receipt["checks"].values())
    assert receipt["errors"] == []


def test_schema_and_semantic_validator_publish_the_same_feature_catalog() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    schema_features = schema["$defs"]["featureIds"]["items"]["enum"]

    assert tuple(schema_features) == MODULE.FEATURE_IDS
    assert schema["properties"]["schema_version"]["const"] == MODULE.SPEC_SCHEMA_VERSION


def test_validator_rejects_more_than_one_controlled_off_feature(tmp_path: Path) -> None:
    payload = _example()
    intervention = payload["variants"][1]
    intervention["enabled_feature_ids"].remove("F1.2")
    intervention["disabled_feature_ids"].append("F1.2")

    receipt = MODULE.load_and_validate(_write(tmp_path, payload))

    assert receipt["ok"] is False
    assert receipt["checks"]["single_feature_intervention"] is False
    assert any("canonical one-Feature intervention" in error for error in receipt["errors"])


def test_validator_rejects_cross_family_attribution_and_feature(tmp_path: Path) -> None:
    payload = _example()
    payload["hypothesis"]["attribution_code"] = "H2"

    receipt = MODULE.load_and_validate(_write(tmp_path, payload))

    assert receipt["ok"] is False
    assert any("F1.1 is not owned by H2" in error for error in receipt["errors"])


def test_generalization_requires_disjoint_tasks_and_matched_baseline(tmp_path: Path) -> None:
    payload = _example()
    payload["task_set"]["held_out_task_ids"] = ["ws-h1-workspace-binding-002"]
    payload["hypothesis"]["regression_risk_task_ids"] = ["ws-h1-workspace-binding-002"]
    payload["evaluation"]["matched_task_level_baseline"] = "none"

    receipt = MODULE.load_and_validate(_write(tmp_path, payload))

    assert receipt["ok"] is False
    assert receipt["checks"]["disjoint_task_partitions"] is False
    assert any("matched task-level baseline" in error for error in receipt["errors"])


def test_task_domain_profiles_cover_each_task_and_follow_v2_prefixes(tmp_path: Path) -> None:
    payload = _example()
    payload["task_set"]["domain_by_task_id"].pop("ws-h1-workspace-binding-002")
    payload["task_set"]["domain_by_task_id"]["ws-h1-workspace-binding-001"] = "UA"

    receipt = MODULE.load_and_validate(_write(tmp_path, payload))

    assert receipt["ok"] is False
    assert any("conflicts with its V2 prefix" in error for error in receipt["errors"])
    assert any("is missing tasks" in error for error in receipt["errors"])


@pytest.mark.parametrize(
    ("field", "unsafe"),
    [
        ("human_approval_required", False),
        ("core_mutation_allowed", True),
        ("reasoning_read_only", False),
        ("analyzers_authoritative", True),
    ],
)
def test_fixed_core_governance_is_not_configurable(
    tmp_path: Path, field: str, unsafe: bool
) -> None:
    payload = _example()
    payload["governance"][field] = unsafe

    receipt = MODULE.load_and_validate(_write(tmp_path, payload))

    assert receipt["ok"] is False
    assert receipt["checks"]["fixed_core_governance"] is False
    assert any(error.startswith(f"governance.{field}") for error in receipt["errors"])


def test_reliability_claim_requires_five_declared_trials(tmp_path: Path) -> None:
    payload = _example()
    payload["claim_level"] = "reliability"
    payload["budget"]["trials_per_task_variant"] = 4
    payload["evaluation"]["minimum_trials"] = 4

    receipt = MODULE.load_and_validate(_write(tmp_path, payload))

    assert receipt["ok"] is False
    assert any("requires at least five trials" in error for error in receipt["errors"])


def test_approved_plan_requires_external_approval_receipt(tmp_path: Path) -> None:
    payload = _example()
    payload["status"] = "approved"

    receipt = MODULE.load_and_validate(_write(tmp_path, payload))

    assert receipt["ok"] is False
    assert receipt["checks"]["fixed_core_governance"] is False
    assert any("external approval receipt" in error for error in receipt["errors"])


def test_validator_rejects_duplicate_keys_and_symlinked_specs(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        MODULE.load_and_validate(duplicate)

    link = tmp_path / "link.json"
    link.symlink_to(EXAMPLE)
    with pytest.raises(ValueError, match="non-symlink"):
        MODULE.load_and_validate(link)


def test_validator_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "experiment.fifo"
    os.mkfifo(fifo)

    with pytest.raises(ValueError, match="regular, non-symlink"):
        MODULE.load_and_validate(fifo)


def test_help_is_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = False

    def fail_if_called(_path: Path) -> dict:
        nonlocal called
        called = True
        raise AssertionError("validation must not run during --help")

    monkeypatch.setattr(MODULE, "load_and_validate", fail_if_called)
    with pytest.raises(SystemExit) as exc_info:
        MODULE.main(["--help"])

    assert exc_info.value.code == 0
    assert called is False
    assert list(tmp_path.iterdir()) == []


def test_unknown_fields_fail_closed_without_changing_input(tmp_path: Path) -> None:
    payload = _example()
    original = deepcopy(payload)
    payload["automatic_core_patch"] = True
    path = _write(tmp_path, payload)

    receipt = MODULE.load_and_validate(path)

    assert receipt["ok"] is False
    assert any("unknown fields" in error for error in receipt["errors"])
    assert original == _example()
