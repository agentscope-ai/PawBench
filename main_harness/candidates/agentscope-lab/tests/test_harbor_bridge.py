from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse

from pawbench_agentscope import harbor_bridge
from pawbench_agentscope.features import FEATURE_IDS, TAXONOMY_VERSION
from pawbench_agentscope.harbor_bridge import (
    AGENT_NAME,
    AGENT_VERSION,
    ATIF_SCHEMA_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    build_feature_config,
    load_harbor_task_contract,
    main,
    normalize_harbor_artifact,
    run_harbor_task,
    trace_to_atif,
)
from pawbench_agentscope.harbor_contract import validate_contract_directory
from pawbench_agentscope.models import RunResult, TaskSpec, VerifierResult


class HarborDemoModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="harbor-demo-model",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(
        self,
        model: str,
        messages: list,
        tools: list[dict] | None = None,
        tool_choice=None,
        **kwargs,
    ):
        has_result = any(
            any(getattr(block, "type", "") == "tool_result" for block in message.content)
            for message in messages
        )
        if not has_result:
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="write-answer",
                        name="Bash",
                        input=json.dumps(
                            {
                                "command": "printf expected > answer.txt",
                                "description": "write the requested artifact",
                            }
                        ),
                    )
                ],
                is_last=True,
            )
        return ChatResponse(content=[TextBlock(text="Saved answer.txt")], is_last=True)


def test_cli_reports_exact_agent_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.strip() == f"{AGENT_NAME} {AGENT_VERSION}"


def test_normalize_harbor_artifact_supports_all_v2_workspace_roots(tmp_path: Path) -> None:
    for source in (
        "/app/result.json",
        "/home/node/workspace/result.json",
        "/workspace/result.json",
        "result.json",
        {"source": "/app/result.json", "service": "main"},
    ):
        assert normalize_harbor_artifact(source, workspace_root=tmp_path) == "result.json"


def test_normalize_harbor_artifact_routes_logs_outside_workspace_verifier(tmp_path: Path) -> None:
    assert (
        normalize_harbor_artifact(
            "/logs/agent/trajectory.json",
            workspace_root=tmp_path,
        )
        is None
    )
    with pytest.raises(ValueError, match="must be relative"):
        normalize_harbor_artifact("/etc/passwd", workspace_root=tmp_path)
    with pytest.raises(ValueError, match="safe file path"):
        normalize_harbor_artifact("../escape.txt", workspace_root=tmp_path)


def test_load_harbor_task_contract_reads_instruction_artifacts_and_timeout(tmp_path: Path) -> None:
    task_root = tmp_path / "ws-demo-001"
    workspace = tmp_path / "workspace"
    task_root.mkdir()
    workspace.mkdir()
    (task_root / "instruction.md").write_text("Create result.json.", encoding="utf-8")
    (task_root / "task.toml").write_text(
        'version = "1.0"\n'
        'artifacts = ["/app/result.json", "/logs/agent/trajectory.json"]\n'
        "[agent]\n"
        "timeout_sec = 42.0\n",
        encoding="utf-8",
    )

    contract = load_harbor_task_contract(
        task_root=task_root,
        workspace_root=workspace,
    )

    assert contract.task.task_id == "ws-demo-001"
    assert contract.task.instruction == "Create result.json."
    assert contract.task.required_artifacts == ["result.json"]
    assert contract.timeout_seconds == 42.0
    assert contract.skipped_log_artifacts == ("/logs/agent/trajectory.json",)


def test_build_feature_config_defaults_to_all_and_supports_one_off() -> None:
    all_enabled = build_feature_config()
    without_context = build_feature_config(disabled_features=["F5.1"])

    assert all_enabled.enabled == set(FEATURE_IDS)
    assert without_context.enabled == set(FEATURE_IDS) - {"F5.1"}
    with pytest.raises(ValueError, match="unknown Feature IDs"):
        build_feature_config(disabled_features=["F9.9"])
    for invalid_timeout in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite and positive"):
            build_feature_config(runtime_timeout_seconds=invalid_timeout)
    with pytest.raises(ValueError, match="only disabled Features"):
        build_feature_config(ablation_targets={"F1.1": "fault"})
    with pytest.raises(ValueError, match="integer of at least 1000"):
        build_feature_config(compaction_limit_chars=1200.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must not contain duplicates"):
        build_feature_config(disabled_features=["F1.1", "F1.1"])
    with pytest.raises(ValueError, match="must not overlap"):
        build_feature_config(
            enabled_features=["F1.1"],
            disabled_features=["F1.1"],
        )
    with pytest.raises(ValueError, match="sequences, not strings"):
        build_feature_config(disabled_features="F1.1")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="duplicate --ablation-target"):
        harbor_bridge._targets(["F1.1=first", "F1.1=second"])


def test_harbor_contract_rejects_bounded_input_abuse(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="required_artifacts must be a sequence"):
        load_harbor_task_contract(
            workspace_root=workspace,
            task_id="bad-artifact-shape",
            instruction="x",
            required_artifacts="answer.txt",
        )
    with pytest.raises(ValueError, match="too long"):
        load_harbor_task_contract(
            workspace_root=workspace,
            task_id="long-artifact",
            instruction="x",
            required_artifacts=["a" * 4097],
        )
    with pytest.raises(ValueError, match="more than 1024 artifacts"):
        load_harbor_task_contract(
            workspace_root=workspace,
            task_id="many-artifacts",
            instruction="x",
            required_artifacts=[f"artifact-{index}.txt" for index in range(1025)],
        )
    with pytest.raises(ValueError, match="control characters"):
        load_harbor_task_contract(
            workspace_root=workspace,
            task_id="control-artifact",
            instruction="x",
            required_artifacts=["answer\n.txt"],
        )


def test_harbor_task_control_files_must_be_bounded_regular_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_root = tmp_path / "task"
    task_root.mkdir()
    outside = tmp_path / "outside-instruction.md"
    outside.write_text("do not follow", encoding="utf-8")
    (task_root / "instruction.md").symlink_to(outside)

    with pytest.raises(ValueError, match="must not be a symlink"):
        load_harbor_task_contract(task_root=task_root, workspace_root=workspace)

    (task_root / "instruction.md").unlink()
    (task_root / "instruction.md").write_text("x" * (512 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="instruction.md exceeds"):
        load_harbor_task_contract(task_root=task_root, workspace_root=workspace)


def test_harbor_task_toml_rejects_nonfinite_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_root = tmp_path / "task"
    task_root.mkdir()
    (task_root / "instruction.md").write_text("x", encoding="utf-8")
    (task_root / "task.toml").write_text("[agent]\ntimeout_sec = inf\n", encoding="utf-8")

    with pytest.raises(ValueError, match="finite and positive"):
        load_harbor_task_contract(task_root=task_root, workspace_root=workspace)

    (task_root / "task.toml").write_text('[agent]\ntimeout_sec = "slow"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a number"):
        load_harbor_task_contract(task_root=task_root, workspace_root=workspace)


def test_cli_invalid_utf8_instruction_emits_input_contract_error(tmp_path: Path) -> None:
    instruction = tmp_path / "instruction.bin"
    instruction.write_bytes(b"\xff\xfe")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logs = tmp_path / "logs"

    exit_code = main(
        [
            "--task-id",
            "invalid-utf8",
            "--workspace-root",
            str(workspace),
            "--logs-dir",
            str(logs),
            "--instruction-file",
            str(instruction),
        ]
    )

    assert exit_code == 1
    result = json.loads((logs / "result.json").read_text(encoding="utf-8"))
    assert result["error_code"] == "HC_INPUT_CONTRACT_INVALID"
    assert result["failure_scope"] == "configuration"


def test_cli_zero_runtime_timeout_is_rejected_not_silently_defaulted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logs = tmp_path / "logs"

    exit_code = main(
        [
            "--task-id",
            "zero-timeout",
            "--instruction",
            "x",
            "--workspace-root",
            str(workspace),
            "--logs-dir",
            str(logs),
            "--runtime-timeout",
            "0",
        ]
    )

    assert exit_code == 1
    result = json.loads((logs / "result.json").read_text(encoding="utf-8"))
    assert result["error_code"] == "HC_INPUT_CONTRACT_INVALID"


def test_cli_refuses_logs_directory_symlink_without_writing_external_target(tmp_path: Path) -> None:
    external = tmp_path / "external-logs"
    external.mkdir()
    victim = external / "keep.txt"
    victim.write_text("preserve", encoding="utf-8")
    logs_link = tmp_path / "logs-link"
    logs_link.symlink_to(external, target_is_directory=True)

    exit_code = main(
        [
            "--task-id",
            "symlink-logs",
            "--instruction",
            "x",
            "--workspace-root",
            str(tmp_path),
            "--logs-dir",
            str(logs_link),
            "--disable-feature",
            "UNKNOWN",
        ]
    )

    assert exit_code == 1
    assert victim.read_text(encoding="utf-8") == "preserve"
    assert not (external / "result.json").exists()


def test_cli_refuses_logs_nested_in_workspace_without_creating_output(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logs = workspace / "logs" / "agent"

    exit_code = main(
        [
            "--task-id",
            "overlapping-logs",
            "--instruction",
            "x",
            "--workspace-root",
            str(workspace),
            "--logs-dir",
            str(logs),
        ]
    )

    assert exit_code == 1
    assert not logs.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-only")
def test_bounded_task_reader_refuses_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "instruction.pipe"
    os.mkfifo(fifo)

    with pytest.raises(ValueError, match="must be a regular file"):
        harbor_bridge._read_bounded_task_file(
            fifo,
            maximum_bytes=1024,
            label="instruction file",
        )


def test_provenance_receipt_does_not_follow_required_artifact_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("secret", encoding="utf-8")
    (workspace / "answer.txt").symlink_to(external)

    receipts = harbor_bridge._required_artifact_receipts(workspace, ["answer.txt"])

    assert receipts == {"answer.txt": {"path": "answer.txt", "exists": False}}


def test_provenance_receipt_bounds_artifact_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.bin").write_bytes(b"12345")
    monkeypatch.setattr(harbor_bridge, "MAX_RECEIPT_HASH_BYTES", 4)

    with pytest.raises(ValueError, match="receipt file exceeds 4 bytes"):
        harbor_bridge._required_artifact_receipts(workspace, ["large.bin"])


def test_atomic_bridge_json_rejects_nonfinite_and_oversized_output(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    with pytest.raises(RuntimeError, match="could not serialize standard JSON"):
        harbor_bridge._atomic_json(path, {"score": float("nan")})
    assert not path.exists()

    with pytest.raises(RuntimeError, match="JSON output exceeds 4 bytes"):
        harbor_bridge._atomic_json(path, {"ok": True}, maximum_bytes=4)
    assert not path.exists()


def test_run_harbor_task_exports_result_native_trace_and_atif(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    logs = tmp_path / "logs" / "agent"
    workspace.mkdir(parents=True)
    contract = load_harbor_task_contract(
        workspace_root=workspace,
        task_id="ua-harbor-demo-001",
        instruction="Create answer.txt containing exactly expected.",
        required_artifacts=["/app/answer.txt", "/logs/agent/trajectory.json"],
    )
    contract.task.hidden_contract["artifact_text"] = {"answer.txt": "expected"}

    result = run_harbor_task(
        contract,
        workspace_root=workspace,
        logs_dir=logs,
        feature_config=build_feature_config(runtime_timeout_seconds=20),
        model=HarborDemoModel(),
        max_iters=4,
    )

    assert result.success is True
    assert result.accepted is True
    assert result.verifier["ok"] is True
    assert (logs / "result.json").is_file()
    assert (logs / "harness-core-trace.jsonl").is_file()
    provenance_path = logs / "provenance.json"
    assert provenance_path.is_file()
    trajectory_path = logs / "trajectory.json"
    assert trajectory_path.is_file()

    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    assert trajectory["schema_version"] == ATIF_SCHEMA_VERSION
    assert trajectory["agent"]["name"] == "agentscope-lab"
    assert trajectory["agent"]["extra"]["enabled_features"] == sorted(FEATURE_IDS)
    assert [step["step_id"] for step in trajectory["steps"]] == list(
        range(1, len(trajectory["steps"]) + 1)
    )
    assert trajectory["steps"][0]["source"] == "user"
    agent_steps = [step for step in trajectory["steps"] if step["source"] == "agent"]
    tool_step = next(step for step in agent_steps if step.get("tool_calls"))
    tool_call = tool_step["tool_calls"][0]
    assert tool_call["function_name"] == "Bash"
    assert tool_call["arguments"]["command"] == "printf expected > answer.txt"
    assert tool_step["observation"]["results"][0]["source_call_id"] == tool_call["tool_call_id"]

    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert provenance["schema_version"] == PROVENANCE_SCHEMA_VERSION
    assert provenance["task_id"] == "ua-harbor-demo-001"
    assert provenance["feature_config"]["disabled"] == []
    assert provenance["required_artifacts"]["answer.txt"]["sha256"]
    assert set(provenance["workspace_state_hashes"]) == {"before", "after"}
    assert validate_contract_directory(logs)["ok"] is True

    provenance["task_id"] = "different-task"
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    tampered = validate_contract_directory(logs)
    assert tampered["ok"] is False
    assert any("cross_file.task_id" in error for error in tampered["errors"])


@pytest.mark.parametrize(
    ("result_events", "expected_status", "expected_content"),
    [
        ([], "missing", "[tool result missing from native trace]"),
        (
            [
                {
                    "type": "agentscope_event",
                    "timestamp": "2026-07-16T00:00:02Z",
                    "payload": {
                        "type": "TOOL_RESULT_TEXT_DELTA",
                        "tool_call_id": "call-1",
                        "delta": "partial output",
                    },
                }
            ],
            "incomplete",
            "partial output",
        ),
        (
            [
                {
                    "type": "agentscope_event",
                    "timestamp": "2026-07-16T00:00:02Z",
                    "payload": {
                        "type": "TOOL_RESULT_START",
                        "tool_call_id": "call-1",
                    },
                }
            ],
            "incomplete",
            "[tool result started but did not finish]",
        ),
    ],
)
def test_atif_projection_never_invents_completed_tool_results(
    result_events: list[dict],
    expected_status: str,
    expected_content: str,
    tmp_path: Path,
) -> None:
    task = TaskSpec(task_id="trace-gap", instruction="inspect", task_dir=tmp_path)
    run_result = RunResult(
        run_id="run-1",
        task_id=task.task_id,
        completion_ok=False,
        verification_gated=True,
        accepted=False,
        verifier=VerifierResult(ok=False),
        final_text="",
        event_count=2 + len(result_events),
        trace_path=tmp_path / "trace.jsonl",
        workspace_root=tmp_path,
        taxonomy_version=TAXONOMY_VERSION,
        enabled_features=list(FEATURE_IDS),
    )
    trace_rows = [
        {
            "type": "agentscope_event",
            "timestamp": "2026-07-16T00:00:00Z",
            "payload": {"type": "MODEL_CALL_START"},
        },
        {
            "type": "agentscope_event",
            "timestamp": "2026-07-16T00:00:01Z",
            "payload": {
                "type": "TOOL_CALL_START",
                "tool_call_id": "call-1",
                "tool_call_name": "Read",
            },
        },
        *result_events,
    ]

    trajectory = trace_to_atif(
        trace_rows,
        task=task,
        run_result=run_result,
        model_name="test-model",
    )
    observation = trajectory["steps"][1]["observation"]["results"][0]

    assert observation["content"] == expected_content
    assert observation["extra"]["trace_projection_status"] == expected_status
    assert observation["content"] != "completed"


def test_run_harbor_task_rejects_contract_workspace_mismatch(tmp_path: Path) -> None:
    contract_workspace = tmp_path / "contract-workspace"
    actual_workspace = tmp_path / "actual-workspace"
    logs = tmp_path / "logs" / "agent"
    contract_workspace.mkdir()
    actual_workspace.mkdir()
    contract = load_harbor_task_contract(
        workspace_root=contract_workspace,
        task_id="workspace-mismatch",
        instruction="x",
    )

    with pytest.raises(ValueError, match="contract workspace does not match"):
        run_harbor_task(
            contract,
            workspace_root=actual_workspace,
            logs_dir=logs,
            feature_config=build_feature_config(),
            model=HarborDemoModel(),
            max_iters=2,
        )

    assert not logs.exists()


def test_run_harbor_task_rejects_invalid_model_name_before_creating_logs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    logs = tmp_path / "logs"
    workspace.mkdir()
    contract = load_harbor_task_contract(
        workspace_root=workspace,
        task_id="bad-model",
        instruction="x",
    )

    with pytest.raises(ValueError, match="model_name must be"):
        run_harbor_task(
            contract,
            workspace_root=workspace,
            logs_dir=logs,
            feature_config=build_feature_config(),
            model=HarborDemoModel(),
            model_name="bad\nmodel",
            max_iters=2,
        )

    assert not logs.exists()
