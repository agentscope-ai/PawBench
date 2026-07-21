from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pawbench_agentscope.attribution import candidate_harness_codes
from pawbench_agentscope.features import (
    FEATURE_IDS,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
    FeatureConfig,
    PersistentMemoryStore,
    budget_policy,
    compact_context,
    completion_decision,
    discover_context_sources,
    diff_workspace_snapshots,
    normalize_tool_feedback,
    preflight_workspace,
    prepare_prompt,
    safe_join,
    snapshot_workspace,
    trace_runtime_contracts,
)
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import enabled_tool_aliases
from pawbench_agentscope import verifier
from pawbench_agentscope import features as features_module
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


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"runtime_timeout_seconds": float("nan")}, "finite and positive"),
        ({"runtime_timeout_seconds": 0}, "finite and positive"),
        ({"compaction_limit_chars": 999}, "at least 1000"),
        ({"ablation_targets": {"F9.9": "x"}}, "Unknown ablation target"),
        ({"ablation_targets": {"F1.1": ""}}, "non-empty strings"),
        ({"ablation_targets": {"F1.1": "x"}}, "only disabled Features"),
    ],
)
def test_validate_known_rejects_model_copy_bypasses(update: dict[str, object], message: str) -> None:
    config = FeatureConfig.all_enabled().model_copy(update=update)

    with pytest.raises(ValueError, match=message):
        config.validate_known()


def test_feature_config_without_rejects_unknown_feature() -> None:
    with pytest.raises(ValueError, match="Unknown feature ids"):
        FeatureConfig.all_enabled().without("F9.9")


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_budget_policy_rejects_invalid_max_iters(value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        budget_policy(FeatureConfig.all_enabled(), value)  # type: ignore[arg-type]


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


def test_preflight_rejects_external_symlink_without_reading_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("DO_NOT_READ", encoding="utf-8")
    (workspace / "README.md").symlink_to(outside)
    task = TaskSpec(task_id="external-symlink", instruction="x", task_dir=workspace)

    result = preflight_workspace(task, workspace, apply_reset=True)

    assert result["ready"] is False
    assert result["external_symlinks"] == ["README.md"]
    assert "README.md" not in snapshot_workspace(workspace)
    assert discover_context_sources(workspace) == []


def test_preflight_reset_refuses_external_symlink_without_unlinking_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve", encoding="utf-8")
    (workspace / "reset-me").symlink_to(outside)
    task = TaskSpec(
        task_id="external-reset-symlink",
        instruction="x",
        task_dir=workspace,
        reset_paths=["reset-me"],
        isolated_workspace=True,
    )

    result = preflight_workspace(task, workspace, apply_reset=True)

    assert result["ready"] is False
    assert result["reset_events"] == [
        {"path": "reset-me", "status": "refused", "reason": "path_escapes_workspace"}
    ]
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_preflight_reset_refuses_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    victim = workspace / "keep.txt"
    victim.write_text("preserve", encoding="utf-8")
    task = TaskSpec(
        task_id="root-reset",
        instruction="x",
        task_dir=workspace,
        reset_paths=["."],
        isolated_workspace=True,
    )

    result = preflight_workspace(task, workspace, apply_reset=True)

    assert result["ready"] is False
    assert result["reset_events"] == [
        {"path": ".", "status": "refused", "reason": "workspace_root_reset_forbidden"}
    ]
    assert victim.read_text(encoding="utf-8") == "preserve"


def test_preflight_reset_unlinks_internal_symlink_without_deleting_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "target"
    target.mkdir(parents=True)
    victim = target / "keep.txt"
    victim.write_text("preserve", encoding="utf-8")
    link = workspace / "reset-me"
    link.symlink_to(target, target_is_directory=True)
    task = TaskSpec(
        task_id="internal-reset-symlink",
        instruction="x",
        task_dir=workspace,
        reset_paths=["reset-me"],
        isolated_workspace=True,
    )

    result = preflight_workspace(task, workspace, apply_reset=True)

    assert result["reset_events"] == [
        {"path": "reset-me", "status": "symlink_removed"}
    ]
    assert not link.exists()
    assert victim.read_text(encoding="utf-8") == "preserve"


def test_preflight_reset_refuses_symlink_parent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "target"
    target.mkdir(parents=True)
    victim = target / "keep.txt"
    victim.write_text("preserve", encoding="utf-8")
    (workspace / "alias").symlink_to(target, target_is_directory=True)
    task = TaskSpec(
        task_id="parent-reset-symlink",
        instruction="x",
        task_dir=workspace,
        reset_paths=["alias/keep.txt"],
        isolated_workspace=True,
    )

    result = preflight_workspace(task, workspace, apply_reset=True)

    assert result["ready"] is False
    assert result["reset_events"] == [
        {"path": "alias/keep.txt", "status": "refused", "reason": "symlink_parent_refused"}
    ]
    assert victim.read_text(encoding="utf-8") == "preserve"


def test_preflight_rejects_symlink_loop_as_unsafe(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "loop").symlink_to("loop")
    task = TaskSpec(task_id="symlink-loop", instruction="x", task_dir=workspace)

    result = preflight_workspace(task, workspace, apply_reset=True)

    assert result["ready"] is False
    assert result["external_symlinks"] == ["loop"]


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


def test_context_discovery_reads_only_bounded_prefix(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "README.md"
    source.write_text("A" * 20_000, encoding="utf-8")

    def reject_unbounded_read(*args, **kwargs):
        raise AssertionError("discover_context_sources must not call Path.read_text")

    monkeypatch.setattr(Path, "read_text", reject_unbounded_read)
    discovered = discover_context_sources(tmp_path)

    assert len(discovered) == 1
    assert len(discovered[0]["text"]) == 8_000


def test_workspace_prompt_uses_portable_alias_without_host_path(tmp_path: Path) -> None:
    task = TaskSpec(task_id="workspace_prompt", instruction="Create answer.txt.", task_dir=tmp_path)
    trace = TraceStub()

    prompt = prepare_prompt(task, tmp_path, FeatureConfig(enabled={"F1.1"}), trace)

    assert "Workspace root alias: ." in prompt
    assert str(tmp_path) not in prompt


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


def test_persistent_memory_concurrent_upserts_are_lossless(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"

    with ThreadPoolExecutor(max_workers=20) as executor:
        list(
            executor.map(
                lambda index: PersistentMemoryStore(path).upsert(f"task:{index}", f"value:{index}"),
                range(100),
            )
        )

    records = PersistentMemoryStore(path)._load()["records"]
    assert len(records) == 100
    assert {record["key"] for record in records} == {f"task:{index}" for index in range(100)}


def test_persistent_memory_concurrent_versions_are_monotonic(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"

    with ThreadPoolExecutor(max_workers=20) as executor:
        records = list(
            executor.map(
                lambda index: PersistentMemoryStore(path).upsert("task:shared", f"value:{index}"),
                range(100),
            )
        )

    assert {record["version"] for record in records} == set(range(1, 101))
    assert PersistentMemoryStore(path)._load()["records"][0]["version"] == 100


def test_persistent_memory_refuses_preexisting_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"schema_version": 1, "records": []}\n', encoding="utf-8")
    path = tmp_path / "memory.json"
    path.symlink_to(outside)

    try:
        PersistentMemoryStore(path).upsert("task:blocked", "value")
    except ValueError as exc:
        assert "must not be a symlink" in str(exc)
    else:
        raise AssertionError("memory symlink should be rejected")
    assert json.loads(outside.read_text(encoding="utf-8"))["records"] == []


def test_state_artifact_delta_is_hashed(tmp_path: Path) -> None:
    before = snapshot_workspace(tmp_path)
    (tmp_path / "answer.txt").write_text("answer", encoding="utf-8")
    after = snapshot_workspace(tmp_path)
    delta = diff_workspace_snapshots(before, after)
    assert delta["created"] == ["answer.txt"]
    assert len(delta["after"]["answer.txt"]["sha256"]) == 64


def test_state_snapshot_skips_oversized_file_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(features_module, "MAX_SNAPSHOT_FILE_BYTES", 4)
    (tmp_path / "large.bin").write_bytes(b"12345")

    receipt = snapshot_workspace(tmp_path)["large.bin"]

    assert receipt == {
        "size": 5,
        "sha256": None,
        "hash_status": "skipped_too_large",
    }


def test_workspace_scan_and_snapshot_have_resource_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(3):
        (tmp_path / f"file-{index}.txt").write_text("abc", encoding="utf-8")
    monkeypatch.setattr(features_module, "MAX_WORKSPACE_ENTRIES", 2)

    with pytest.raises(ValueError, match="filesystem entries"):
        snapshot_workspace(tmp_path)

    task = TaskSpec(task_id="bounded-workspace", instruction="x", task_dir=tmp_path)
    preflight = preflight_workspace(task, tmp_path, apply_reset=False)
    assert preflight["ready"] is False
    assert "filesystem entries" in preflight["workspace_scan_error"]
    assert "filesystem entries" in preflight["snapshot_error"]


def test_workspace_snapshot_bounds_cumulative_hash_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.txt").write_text("abc", encoding="utf-8")
    (tmp_path / "b.txt").write_text("def", encoding="utf-8")
    monkeypatch.setattr(features_module, "MAX_SNAPSHOT_HASH_BYTES", 4)

    with pytest.raises(ValueError, match="exceeds 4 hashed bytes"):
        snapshot_workspace(tmp_path)


def test_persistent_memory_rejects_oversized_or_malformed_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "memory.json"
    path.write_text('{"schema_version": 1, "records": []}\n', encoding="utf-8")
    monkeypatch.setattr(features_module, "MAX_MEMORY_STORE_BYTES", 8)
    with pytest.raises(ValueError, match="input exceeds 8 bytes"):
        PersistentMemoryStore(path)._load()

    monkeypatch.setattr(features_module, "MAX_MEMORY_STORE_BYTES", 1024)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": [{"key": "x", "value": "y", "version": 0}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Malformed memory store"):
        PersistentMemoryStore(path)._load()


def test_persistent_memory_validates_query_and_upsert_bounds(tmp_path: Path) -> None:
    store = PersistentMemoryStore(tmp_path / "memory.json")
    with pytest.raises(ValueError, match="query limit"):
        store.query("x", limit=0)
    with pytest.raises(ValueError, match="memory key"):
        store.upsert("", "value")
    with pytest.raises(ValueError, match="memory value"):
        store.upsert("key", "x" * (features_module.MAX_MEMORY_VALUE_CHARS + 1))
    with pytest.raises(ValueError, match="finite JSON data"):
        store.upsert("key", "value", metadata={"score": float("nan")})


def test_persistent_memory_redacts_metadata_and_rejects_duplicate_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.json"
    store = PersistentMemoryStore(path)
    stored = store.upsert(
        "task:secret",
        "value",
        metadata={"api_key": "sk-super-secret-12345678"},
    )
    assert stored["metadata"]["api_key"] == "[REDACTED]"
    assert "sk-super-secret" not in path.read_text(encoding="utf-8")

    duplicate = {
        "schema_version": 1,
        "records": [stored, {**stored, "version": 2}],
    }
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed memory store"):
        store._load()


def test_persistent_memory_rejects_nonfinite_json_constant(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(
        '{"schema_version":1,"records":[],"bad":NaN}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Malformed memory store"):
        PersistentMemoryStore(path)._load()


def test_persistent_memory_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(
        '{"schema_version":1,"schema_version":1,"records":[]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Malformed memory store"):
        PersistentMemoryStore(path)._load()


def test_persistent_memory_redacts_key_value_and_preexisting_records(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    store = PersistentMemoryStore(path)
    secret = "sk-super-secret-12345678"
    stored = store.upsert(
        f"OPENAI_API_KEY={secret}",
        f"Authorization: Bearer {secret}",
    )
    assert secret not in stored["key"]
    assert secret not in stored["value"]
    assert secret not in path.read_text(encoding="utf-8")

    preexisting = {
        "schema_version": 1,
        "records": [
            {
                "key": "task:old",
                "value": f"OPENAI_API_KEY={secret}",
                "version": 1,
                "updated_at": "2026-07-16T12:00:00+08:00",
                "metadata": {},
            }
        ],
    }
    path.write_text(json.dumps(preexisting), encoding="utf-8")
    loaded = store._load()
    assert secret not in loaded["records"][0]["value"]


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


def test_completion_decision_never_accepts_interrupted_reply() -> None:
    assert completion_decision(
        {"finished_reason": "interrupted", "timed_out": False},
        enabled=True,
    ) == (False, "interrupted")


def test_isolation_trace_does_not_overclaim_os_sandbox(tmp_path: Path) -> None:
    trace = TraceStub()
    task = TaskSpec(task_id="isolation-trace", instruction="x", task_dir=tmp_path)

    trace_runtime_contracts(
        task,
        ["read_file"],
        FeatureConfig.all_enabled(),
        {"timeout_seconds": 30},
        trace,
    )

    policy = next(payload for event_type, payload in trace.events if event_type == "isolation_policy")
    assert policy["os_level_sandbox_enforced_here"] is False
    assert policy["strong_containment_owner"] == "Harbor container and network policy"


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


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0, -1, 901, True])
def test_semantic_verifier_rejects_invalid_timeout(tmp_path: Path, timeout: object) -> None:
    task = TaskSpec(
        task_id="invalid-validator-timeout",
        instruction="x",
        task_dir=tmp_path,
        test_command=sys.executable,
        hidden_contract={"validator_timeout_sec": timeout},
    )

    result = verify_artifacts(task, tmp_path)

    assert result.ok is False
    assert "validator timeout must be" in result.failed_tests[0]


def test_semantic_verifier_rejects_oversized_or_nul_command(tmp_path: Path) -> None:
    for command in ("x" * (verifier.MAX_VALIDATOR_COMMAND_CHARS + 1), "bad\x00command"):
        task = TaskSpec(
            task_id="invalid-validator-command",
            instruction="x",
            task_dir=tmp_path,
            test_command=command,
        )
        result = verify_artifacts(task, tmp_path)
        assert result.ok is False
        assert "too long or contains NUL" in result.failed_tests[0]


def test_artifact_inspection_refuses_last_moment_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / "answer.txt"
    artifact.write_text("safe", encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("expected", encoding="utf-8")

    def swapped_safe_join(_root: Path, _relative: str) -> Path:
        artifact.unlink()
        artifact.symlink_to(external)
        return artifact

    monkeypatch.setattr(verifier, "safe_join", swapped_safe_join)
    task = TaskSpec(
        task_id="artifact-swap",
        instruction="x",
        task_dir=workspace,
        required_artifacts=["answer.txt"],
        hidden_contract={"artifact_text": {"answer.txt": "expected"}},
    )

    result = verify_artifacts(task, workspace)

    assert result.ok is False
    assert result.failed_tests == ["answer.txt: artifact is not a regular file"]


def test_artifact_inspection_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "answer.txt"
    os.mkfifo(fifo)
    task = TaskSpec(
        task_id="fifo-artifact",
        instruction="x",
        task_dir=tmp_path,
        required_artifacts=["answer.txt"],
    )

    result = verify_artifacts(task, tmp_path)

    assert result.ok is False
    assert result.failed_tests == ["answer.txt: artifact is not a regular file"]


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
