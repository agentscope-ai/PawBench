from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pawbench_agentscope.features import FEATURE_IDS, TAXONOMY_VERSION
from pawbench_agentscope.harbor_bridge import (
    ATIF_SCHEMA_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
)
from pawbench_agentscope.harbor_contract import (
    validate_contract_directory,
    validate_atif_v17,
    validate_provenance_contract,
    validate_result_contract,
)
from pawbench_agentscope import harbor_contract


def _atif() -> dict:
    return {
        "schema_version": ATIF_SCHEMA_VERSION,
        "session_id": "run-1",
        "agent": {
            "name": "agentscope-lab",
            "version": "0.1.0",
            "model_name": "fixture",
        },
        "steps": [
            {
                "step_id": 1,
                "timestamp": "2026-07-16T12:00:00+08:00",
                "source": "user",
                "message": "write answer.txt",
            },
            {
                "step_id": 2,
                "timestamp": "2026-07-16T12:00:01+08:00",
                "source": "agent",
                "model_name": "fixture",
                "message": "done",
                "tool_calls": [
                    {
                        "tool_call_id": "call-1",
                        "function_name": "Bash",
                        "arguments": {"command": "touch answer.txt"},
                    }
                ],
                "observation": {
                    "results": [
                        {"source_call_id": "call-1", "content": "ok"}
                    ]
                },
            },
        ],
        "final_metrics": {"total_steps": 2},
    }


def test_local_atif_validator_accepts_bridge_subset() -> None:
    assert validate_atif_v17(_atif()) == []


def test_local_atif_validator_catches_order_reference_and_agent_field_errors() -> None:
    broken = copy.deepcopy(_atif())
    broken["steps"][0]["step_id"] = 0
    broken["steps"][0]["model_name"] = "not-allowed"
    broken["steps"][1]["observation"]["results"][0]["source_call_id"] = "missing"

    errors = validate_atif_v17(broken)

    assert any("step_id" in error for error in errors)
    assert any("agent-only fields" in error for error in errors)
    assert any("unknown 'missing'" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("reasoning_effort", float("nan"), "must be finite"),
        ("total_cost_usd", float("inf"), "finite nonnegative"),
        ("total_prompt_tokens", 1.5, "nonnegative integer"),
    ],
)
def test_local_atif_validator_rejects_nonfinite_or_invalid_metrics(
    field: str,
    value: object,
    message: str,
) -> None:
    broken = copy.deepcopy(_atif())
    if field == "reasoning_effort":
        broken["steps"][1][field] = value
    else:
        broken["final_metrics"][field] = value

    assert any(message in error for error in validate_atif_v17(broken))


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_root_field",
        "unknown_agent_field",
        "unknown_step_field",
        "user_reasoning_effort",
        "cross_step_tool_ref",
        "arguments_not_object",
        "unknown_observation_field",
        "unknown_result_field",
        "bad_content_part",
        "unknown_final_metrics_field",
        "zero_llm_with_reasoning",
    ],
)
def test_local_atif_validator_rejects_official_harbor_failures(mutation: str) -> None:
    broken = copy.deepcopy(_atif())
    if mutation == "unknown_root_field":
        broken["surprise"] = True
    elif mutation == "unknown_agent_field":
        broken["agent"]["surprise"] = True
    elif mutation == "unknown_step_field":
        broken["steps"][0]["surprise"] = True
    elif mutation == "user_reasoning_effort":
        broken["steps"][0]["reasoning_effort"] = "high"
    elif mutation == "cross_step_tool_ref":
        broken["steps"].append(
            {
                "step_id": 3,
                "timestamp": "2026-07-16T12:00:02+08:00",
                "source": "agent",
                "model_name": "fixture",
                "message": "bad reference",
                "observation": {
                    "results": [{"source_call_id": "call-1", "content": "bad"}]
                },
            }
        )
        broken["final_metrics"]["total_steps"] = 3
    elif mutation == "arguments_not_object":
        broken["steps"][1]["tool_calls"][0]["arguments"] = "bad"
    elif mutation == "unknown_observation_field":
        broken["steps"][1]["observation"]["surprise"] = True
    elif mutation == "unknown_result_field":
        broken["steps"][1]["observation"]["results"][0]["surprise"] = True
    elif mutation == "bad_content_part":
        broken["steps"][0]["message"] = [{"type": "text"}]
    elif mutation == "unknown_final_metrics_field":
        broken["final_metrics"]["surprise"] = True
    elif mutation == "zero_llm_with_reasoning":
        broken["steps"][1]["llm_call_count"] = 0
        broken["steps"][1]["reasoning_content"] = "must be absent"

    assert validate_atif_v17(broken), mutation


def test_result_validator_accepts_completed_and_runtime_error_envelopes() -> None:
    completed = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "task_id": "ws-contract-001",
        "success": True,
        "accepted": False,
        "completion_ok": True,
        "verification_gated": True,
        "verifier": {"ok": False},
        "agent": {
            "name": "agentscope-lab",
            "version": "0.1.0",
            "runtime": "AgentScope",
            "model_name": "fixture",
        },
        "taxonomy_version": TAXONOMY_VERSION,
        "enabled_features": sorted(FEATURE_IDS),
        "disabled_features": [],
        "files": {
            "result": "/logs/agent/result.json",
            "trace": "/logs/agent/harness-core-trace.jsonl",
            "trajectory": "/logs/agent/trajectory.json",
            "provenance": "/logs/agent/provenance.json",
        },
    }
    runtime_error = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "task_id": "ws-contract-001",
        "success": False,
        "error_type": "RuntimeError",
        "error": "model endpoint unavailable",
    }

    assert validate_result_contract(completed) == []
    assert validate_result_contract(runtime_error) == []


def test_result_validator_rejects_forged_error_retry_semantics() -> None:
    payload = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "task_id": "ws-contract-error",
        "success": False,
        "error_type": "RuntimeError",
        "error": "rate limited",
        "error_schema_version": "harness-core-error-codes/v1",
        "error_code": "HC_PROVIDER_RATE_LIMIT",
        "failure_scope": "configuration",
        "retryable": False,
        "cause_type": "",
    }
    errors = validate_result_contract(payload)
    assert any("failure_scope" in error for error in errors)
    assert any("retryable" in error for error in errors)
    assert any("cause_type" in error for error in errors)


def test_result_and_provenance_reject_parent_traversal_file_references() -> None:
    completed = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "task_id": "ws-contract-001",
        "success": True,
        "accepted": True,
        "completion_ok": True,
        "verification_gated": True,
        "verifier": {"ok": True},
        "agent": {
            "name": "agentscope-lab",
            "version": "0.1.0",
            "runtime": "AgentScope",
            "model_name": "fixture",
        },
        "taxonomy_version": TAXONOMY_VERSION,
        "enabled_features": sorted(FEATURE_IDS),
        "disabled_features": [],
        "files": {
            "result": "/logs/agent/result.json",
            "trace": "/logs/agent/harness-core-trace.jsonl",
            "trajectory": "../../trajectory.json",
            "provenance": "/logs/agent/provenance.json",
        },
    }
    assert any(
        "safe reference" in error for error in validate_result_contract(completed)
    )

    provenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "created_at": "2026-07-16T12:00:00+08:00",
        "task_id": "ws-contract-001",
        "run_id": "run-1",
        "agent": completed["agent"],
        "input_hashes": {
            "instruction_sha256": "a" * 64,
            "task_contract_sha256": "b" * 64,
            "feature_config_sha256": "c" * 64,
        },
        "feature_config": {
            "taxonomy_version": TAXONOMY_VERSION,
            "enabled": sorted(FEATURE_IDS),
            "disabled": [],
            "ablation_targets": {},
            "runtime_timeout_seconds": 300.0,
            "compaction_limit_chars": 12_000,
            "max_iters": 8,
        },
        "required_artifacts": {},
        "outputs": {
            "native_trace": {
                "path": "/logs/agent/harness-core-trace.jsonl",
                "sha256": "d" * 64,
                "size": 1,
            },
            "trajectory": {
                "path": "../../trajectory.json",
                "sha256": "e" * 64,
                "size": 2,
            },
        },
        "result": {
            "accepted": True,
            "completion_ok": True,
            "verifier_ok": True,
            "event_count": 2,
        },
    }
    assert any(
        "safe reference" in error for error in validate_provenance_contract(provenance)
    )


def test_native_trace_cross_file_binding_rejects_tampering() -> None:
    rows = [
        {
            "run_id": "run-1",
            "task_id": "task-1",
            "event_index": 1,
            "event_id": "run-1:1",
            "parent_event_id": None,
            "timestamp": "2026-07-16T12:00:00+08:00",
            "type": "agentscope_event",
            "payload": {"type": "MODEL_CALL_START"},
        }
    ]
    result = {"task_id": "task-1", "event_count": 1}
    provenance = {"run_id": "run-1"}
    assert harbor_contract._validate_native_trace_bindings(rows, result, provenance) == []

    rows[0]["task_id"] = "other-task"
    rows[0]["event_index"] = 2
    rows[0]["event_id"] = "forged"
    rows[0]["timestamp"] = "not-a-timestamp"
    errors = harbor_contract._validate_native_trace_bindings(rows, result, provenance)
    assert any("task_id" in error for error in errors)
    assert any("event_index" in error for error in errors)
    assert any("event_id" in error for error in errors)
    assert any("timestamp" in error for error in errors)


def test_native_trace_event_count_respects_f41_diagnostic_switch() -> None:
    result = {"task_id": "task-1", "event_count": 7}
    provenance = {
        "run_id": "run-1",
        "feature_config": {
            "enabled": [feature_id for feature_id in FEATURE_IDS if feature_id != "F4.1"]
        },
    }
    outer_row = {
        "run_id": "run-1",
        "task_id": "task-1",
        "event_index": 1,
        "event_id": "run-1:1",
        "parent_event_id": None,
        "timestamp": "2026-07-16T12:00:00+08:00",
        "type": "run_start",
        "payload": {},
    }
    assert harbor_contract._validate_native_trace_bindings(
        [outer_row], result, provenance
    ) == []

    diagnostic_row = {**outer_row, "type": "agentscope_event"}
    errors = harbor_contract._validate_native_trace_bindings(
        [diagnostic_row], result, provenance
    )
    assert any("F4.1 OFF" in error for error in errors)


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("missing_root_extra", "trajectory.extra"),
        ("wrong_bridge_schema", "bridge_schema_version"),
        ("missing_agent_extra", "agent.extra"),
        ("wrong_agent_runtime", "agent.extra.runtime"),
        ("wrong_agent_taxonomy", "agent.extra.taxonomy_version"),
        ("wrong_agent_features", "agent.extra.enabled_features"),
        ("missing_final_metrics", "trajectory.final_metrics"),
        ("missing_final_extra", "final_metrics.extra"),
        ("wrong_final_accepted", "trajectory.accepted"),
    ],
)
def test_cross_file_binding_rejects_removed_or_forged_atif_bridge_metadata(
    mutation: str,
    expected_fragment: str,
) -> None:
    agent = {
        "name": "agentscope-lab",
        "version": "0.2.0",
        "runtime": "AgentScope",
        "model_name": "fixture",
    }
    enabled = sorted(FEATURE_IDS)
    result = {
        "task_id": "task-1",
        "agent": agent,
        "taxonomy_version": TAXONOMY_VERSION,
        "enabled_features": enabled,
        "disabled_features": [],
        "accepted": True,
        "completion_ok": True,
        "verifier": {"ok": True},
        "event_count": 2,
        "files": {
            "trace": "/logs/agent/harness-core-trace.jsonl",
            "trajectory": "/logs/agent/trajectory.json",
        },
    }
    provenance = {
        "task_id": "task-1",
        "run_id": "run-1",
        "agent": agent,
        "feature_config": {
            "taxonomy_version": TAXONOMY_VERSION,
            "enabled": enabled,
            "disabled": [],
        },
        "outputs": {
            "native_trace": {"path": "/logs/agent/harness-core-trace.jsonl"},
            "trajectory": {"path": "/logs/agent/trajectory.json"},
        },
        "result": {
            "accepted": True,
            "completion_ok": True,
            "verifier_ok": True,
            "event_count": 2,
        },
    }
    trajectory = {
        "session_id": "run-1",
        "trajectory_id": "agentscope-lab:run-1",
        "agent": {
            "name": agent["name"],
            "version": agent["version"],
            "model_name": agent["model_name"],
            "extra": {
                "runtime": "AgentScope",
                "taxonomy_version": TAXONOMY_VERSION,
                "enabled_features": enabled,
            },
        },
        "extra": {
            "bridge_schema_version": harbor_contract.BRIDGE_SCHEMA_VERSION,
            "task_id": "task-1",
        },
        "final_metrics": {
            "extra": {
                "accepted": True,
                "verifier_ok": True,
                "agentscope_event_count": 2,
            }
        },
    }
    assert harbor_contract._validate_cross_file_bindings(
        result, trajectory, provenance
    ) == []

    broken = copy.deepcopy(trajectory)
    if mutation == "missing_root_extra":
        broken.pop("extra")
    elif mutation == "wrong_bridge_schema":
        broken["extra"]["bridge_schema_version"] = "forged/v9"
    elif mutation == "missing_agent_extra":
        broken["agent"].pop("extra")
    elif mutation == "wrong_agent_runtime":
        broken["agent"]["extra"]["runtime"] = "OtherRuntime"
    elif mutation == "wrong_agent_taxonomy":
        broken["agent"]["extra"]["taxonomy_version"] = "other-taxonomy"
    elif mutation == "wrong_agent_features":
        broken["agent"]["extra"]["enabled_features"] = enabled[:-1]
    elif mutation == "missing_final_metrics":
        broken.pop("final_metrics")
    elif mutation == "missing_final_extra":
        broken["final_metrics"].pop("extra")
    elif mutation == "wrong_final_accepted":
        broken["final_metrics"]["extra"]["accepted"] = False

    errors = harbor_contract._validate_cross_file_bindings(
        result, broken, provenance
    )
    assert any(expected_fragment in error for error in errors), errors


def test_provenance_validator_enforces_hashes_and_feature_partition() -> None:
    payload = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "created_at": "2026-07-16T12:00:00+08:00",
        "task_id": "ws-contract-001",
        "run_id": "run-1",
        "agent": {
            "name": "agentscope-lab",
            "version": "0.1.0",
            "runtime": "AgentScope",
            "model_name": "fixture",
        },
        "input_hashes": {
            "instruction_sha256": "a" * 64,
            "task_contract_sha256": "b" * 64,
            "feature_config_sha256": "c" * 64,
        },
        "feature_config": {
            "taxonomy_version": TAXONOMY_VERSION,
            "enabled": sorted(FEATURE_IDS),
            "disabled": [],
            "ablation_targets": {},
            "runtime_timeout_seconds": 300.0,
            "compaction_limit_chars": 12_000,
            "max_iters": 8,
        },
        "workspace_state_hashes": {"before": "f" * 64, "after": "0" * 64},
        "required_artifacts": {},
        "outputs": {
            "native_trace": {
                "path": "/logs/agent/harness-core-trace.jsonl",
                "sha256": "d" * 64,
                "size": 1,
            },
            "trajectory": {
                "path": "/logs/agent/trajectory.json",
                "sha256": "e" * 64,
                "size": 2,
            },
        },
        "result": {
            "accepted": True,
            "completion_ok": True,
            "verifier_ok": True,
            "event_count": 2,
        },
    }
    assert validate_provenance_contract(payload) == []

    payload["feature_config"]["disabled"] = ["F1.1"]
    assert any(
        "invalid Feature partition" in error
        for error in validate_provenance_contract(payload)
    )

    payload["feature_config"]["disabled"] = []
    payload["feature_config"]["runtime_timeout_seconds"] = float("nan")
    assert any(
        "runtime_timeout_seconds: invalid" in error
        for error in validate_provenance_contract(payload)
    )


def test_contract_directory_refuses_root_and_result_symlinks(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "result.json").write_text(
        json.dumps(
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "task_id": "error-task",
                "success": False,
                "error_type": "RuntimeError",
                "error": "x",
            }
        ),
        encoding="utf-8",
    )
    root_link = tmp_path / "root-link"
    root_link.symlink_to(external, target_is_directory=True)
    assert validate_contract_directory(root_link)["errors"] == [
        "logs_dir: must not be a symlink"
    ]

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "result.json").symlink_to(external / "result.json")
    receipt = validate_contract_directory(logs)
    assert receipt["ok"] is False
    assert any("must not be a symlink" in error for error in receipt["errors"])


def test_contract_directory_bounds_result_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "result.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(harbor_contract, "MAX_RESULT_BYTES", 1)

    receipt = validate_contract_directory(logs)

    assert receipt["ok"] is False
    assert any("result.json exceeds" in error for error in receipt["errors"])


def test_contract_directory_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "result.json").write_text(
        '{"schema_version":"harness-core-harbor-result/v1",'
        '"task_id":"error-task","success":false,"success":true,'
        '"error_type":"RuntimeError","error":"x"}',
        encoding="utf-8",
    )

    receipt = validate_contract_directory(logs)

    assert receipt["ok"] is False
    assert any("duplicate JSON key" in error for error in receipt["errors"])
