from __future__ import annotations

import json
import sys
from pathlib import Path

from pawbench_agentscope.attribution import candidate_harness_codes
from pawbench_agentscope.features import (
    FEATURE_IDS,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
    FeatureConfig,
    PersistentMemoryStore,
    compact_context,
    diff_workspace_snapshots,
    normalize_tool_feedback,
    preflight_workspace,
    prepare_prompt,
    safe_join,
    snapshot_workspace,
)
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import enabled_tool_aliases
from pawbench_agentscope.verifier import verify_artifacts


class TraceStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type: str, payload: dict, **_: object) -> None:
        self.events.append((event_type, payload))


def test_v2_has_fifteen_features_and_h1_h5_only() -> None:
    assert TAXONOMY_VERSION == "harness_core_v2_20260710"
    assert len(FEATURE_IDS) == 15
    assert set(H_TO_FEATURES) == {"H1", "H2", "H3", "H4", "H5"}
    assert all(len(features) == 3 for features in H_TO_FEATURES.values())
    assert FeatureConfig.all_enabled().enabled == set(FEATURE_IDS)


def test_safe_join_blocks_path_escape(tmp_path: Path) -> None:
    assert safe_join(tmp_path, "a.txt") == (tmp_path / "a.txt").resolve()
    try:
        safe_join(tmp_path, "../outside.txt")
    except ValueError as exc:
        assert "escapes workspace" in str(exc)
    else:
        raise AssertionError("path escape should fail")


def test_readiness_reset_only_mutates_isolated_workspace(tmp_path: Path) -> None:
    stale = tmp_path / "stale.txt"
    stale.write_text("old", encoding="utf-8")
    task = TaskSpec(
        task_id="reset",
        instruction="x",
        task_dir=tmp_path,
        reset_paths=["stale.txt"],
        isolated_workspace=True,
        required_binaries=["sh"],
    )
    result = preflight_workspace(task, tmp_path, apply_reset=True)
    assert result["ready"] is True
    assert not stale.exists()
    assert result["state_hash"]


def test_context_assembly_switch_controls_discovered_sources(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("EXPECTED_ANSWER=from-skill", encoding="utf-8")
    task = TaskSpec(task_id="t", instruction="do it", task_dir=tmp_path)
    off_trace = TraceStub()
    off = prepare_prompt(task, tmp_path, FeatureConfig(enabled=set()), off_trace)
    on_trace = TraceStub()
    on = prepare_prompt(task, tmp_path, FeatureConfig(enabled={"F5.1"}), on_trace)
    assert "from-skill" not in off
    assert "from-skill" in on
    assembly = next(payload for event, payload in on_trace.events if event == "context_assembly")
    assert any(source.get("path") == "SKILL.md" for source in assembly["sources"])


def test_compaction_reconstructs_while_off_uses_truncation() -> None:
    text = "MUST preserve artifact path.\n" + ("context line\n" * 400) + "RECENT CONTEXT"
    on = compact_context(text, limit=1_000, enabled=True)
    off = compact_context(text, limit=1_000, enabled=False)
    assert on.mode == "compacted"
    assert "MUST preserve artifact path" in on.text
    assert on.preserved_fact_hashes
    assert off.mode == "truncated"
    assert off.text == text[:1_000]


def test_persistent_memory_versions_and_retrieves(tmp_path: Path) -> None:
    store = PersistentMemoryStore(tmp_path / "memory.json")
    first = store.upsert("task:alpha", "remember red artifact", metadata={"ok": True})
    second = store.upsert("task:alpha", "remember blue artifact", metadata={"ok": True})
    assert first["version"] == 1
    assert second["version"] == 2
    assert store.query("blue artifact")[0]["value"] == "remember blue artifact"


def test_state_artifact_delta_is_hashed(tmp_path: Path) -> None:
    before = snapshot_workspace(tmp_path)
    (tmp_path / "answer.txt").write_text("answer", encoding="utf-8")
    after = snapshot_workspace(tmp_path)
    delta = diff_workspace_snapshots(before, after)
    assert delta["created"] == ["answer.txt"]
    assert len(delta["after"]["answer.txt"]["sha256"]) == 64


def test_availability_hides_only_selected_tool() -> None:
    all_aliases = enabled_tool_aliases(FeatureConfig.all_enabled())
    without_write = enabled_tool_aliases(FeatureConfig.controlled_off("F2.2", target="write_file"))
    assert "write_file" in all_aliases
    assert "write_file" not in without_write
    assert set(all_aliases) - set(without_write) == {"write_file"}


def test_feedback_off_preserves_raw_error() -> None:
    error = {"tool": "Bash", "state": "error", "metadata": {"exit_code": 2}}
    assert normalize_tool_feedback(error, enabled=False) == {"raw_error": error}
    structured = normalize_tool_feedback(error, enabled=True)
    assert structured["ok"] is False
    assert structured["suggested_next_action"]


def test_verifier_report_is_independent_from_acceptance_gate(tmp_path: Path) -> None:
    (tmp_path / "answer.txt").write_text("wrong", encoding="utf-8")
    validator = tmp_path / "validate.py"
    validator.write_text("raise SystemExit(7)\n", encoding="utf-8")
    task = TaskSpec(
        task_id="semantic",
        instruction="x",
        task_dir=tmp_path,
        required_artifacts=["answer.txt"],
        test_command=f"{sys.executable} {validator}",
    )
    result = verify_artifacts(task, tmp_path, run_semantic=True)
    assert result.ok is False
    assert "semantic_validator exit=7" in result.failed_tests[0]


def test_external_provider_error_is_ex3_not_h6() -> None:
    record = {
        "features": list(FEATURE_IDS),
        "validation": "provider returned 429 rate limit",
        "verifier_ok": True,
        "validator_passed": True,
    }
    codes = candidate_harness_codes(record)
    assert "Ex-3" in codes
    assert "H6" not in codes
