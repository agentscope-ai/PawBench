from __future__ import annotations

import json
from pathlib import Path

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse

from pawbench_agentscope.features import FeatureConfig
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import (
    WorkspaceGuardMiddleware,
    build_toolkit,
    enabled_tool_aliases,
    run_task_sync,
    serialize_event,
    validate_action_arguments,
)


EXPECTED_SKILL_ANSWER = "PawBench skill impact ok"
EXPECTED_RECOVERY_ANSWER = "PawBench recovery impact ok"


def full_config() -> FeatureConfig:
    return FeatureConfig.all_enabled().model_copy(update={"runtime_timeout_seconds": 30.0})


class SkillSensitiveModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="skill-sensitive-model",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        prompt_view = "\n".join(str(message) for message in messages)
        has_result = any(any(getattr(block, "type", "") == "tool_result" for block in msg.content) for msg in messages)
        if has_result:
            return ChatResponse(content=[TextBlock(text='{"summary":"done"}')], is_last=True)
        content = EXPECTED_SKILL_ANSWER if "EXPECTED_ANSWER=PawBench skill impact ok" in prompt_view else "wrong answer"
        command = f"printf {json.dumps(content)} > answer.txt"
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id="call-bash",
                    name="Bash",
                    input=json.dumps({"command": command, "description": "write answer"}),
                )
            ],
            is_last=True,
        )


class RecoverySensitiveModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="recovery-sensitive-model",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        prompt_view = "\n".join(str(message) for message in messages)
        has_result = any(any(getattr(block, "type", "") == "tool_result" for block in msg.content) for msg in messages)
        has_recovered = "retry-good-path" in prompt_view
        if not has_result:
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="bad-path",
                        name="Bash",
                        input=json.dumps({"command": f"printf {json.dumps(EXPECTED_RECOVERY_ANSWER)} > ../answer.txt", "description": "bad path"}),
                    )
                ],
                is_last=True,
            )
        if "When a tool call fails" in prompt_view and not has_recovered:
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="retry-good-path",
                        name="Bash",
                        input=json.dumps({"command": f"printf {json.dumps(EXPECTED_RECOVERY_ANSWER)} > answer.txt", "description": "retry-good-path"}),
                    )
                ],
                is_last=True,
            )
        return ChatResponse(content=[TextBlock(text='{"summary":"stopped"}')], is_last=True)


class AbsoluteWorkspacePathModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="absolute-workspace-path-model",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        has_result = any(any(getattr(block, "type", "") == "tool_result" for block in msg.content) for msg in messages)
        if has_result:
            return ChatResponse(content=[TextBlock(text='{"summary":"done"}')], is_last=True)
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id="absolute-workspace",
                    name="Bash",
                    input=json.dumps({"command": "mkdir -p /workspace && printf expected > /workspace/answer.txt", "description": "virtual workspace path"}),
                )
            ],
            is_last=True,
        )


class VerifierRepairModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="verifier-repair-model",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        prompt_view = "\n".join(str(message) for message in messages)
        has_result = any(any(getattr(block, "type", "") == "tool_result" for block in msg.content) for msg in messages)
        if has_result:
            return ChatResponse(content=[TextBlock(text='{"summary":"done"}')], is_last=True)
        command = "printf expected > answer.txt"
        if "Repair the original PawBench task" in prompt_view and "ORIGINAL_REPAIR_MARKER" in prompt_view:
            command = "mkdir -p workspace && printf expected > workspace/answer.txt"
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id="verifier-repair",
                    name="Bash",
                    input=json.dumps({"command": command, "description": "repair verifier output"}),
                )
            ],
            is_last=True,
        )


class PermissionPauseRecoveryModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="permission-pause-recovery-model",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        prompt_view = "\n".join(str(message) for message in messages)
        has_result = any(any(getattr(block, "type", "") == "tool_result" for block in msg.content) for msg in messages)
        if has_result:
            return ChatResponse(content=[TextBlock(text='{"summary":"done"}')], is_last=True)
        if "previous attempt paused because a tool requested interactive permission" in prompt_view:
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="safe-static-recovery",
                        name="Bash",
                        input=json.dumps(
                            {
                                "command": "printf expected > answer.txt",
                                "description": "write with a static workspace path",
                            }
                        ),
                    )
                ],
                is_last=True,
            )
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id="dynamic-shell-command",
                    name="Bash",
                    input=json.dumps(
                        {
                            "command": "printf expected > $(pwd)/answer.txt",
                            "description": "write through a dynamic path",
                        }
                    ),
                )
            ],
            is_last=True,
        )


def make_skill_task(workspace: Path) -> TaskSpec:
    return TaskSpec(
        task_id="skill_impact",
        instruction="Create answer.txt using the discovered skill answer.",
        task_dir=workspace,
        required_artifacts=["answer.txt"],
        required_tools=["run_shell"],
        hidden_contract={"artifact_text": {"answer.txt": EXPECTED_SKILL_ANSWER}},
    )


def event_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_build_toolkit_availability_hides_one_selected_tool(tmp_path: Path) -> None:
    full = full_config()
    without_write = FeatureConfig.controlled_off("F2.2", target="write_file")
    assert build_toolkit(tmp_path, full) is not None
    assert build_toolkit(tmp_path, without_write) is not None
    assert set(enabled_tool_aliases(full)) - set(enabled_tool_aliases(without_write)) == {"write_file"}


def test_serialize_event_handles_pydantic_like_object() -> None:
    class E:
        def model_dump(self, mode="json"):
            return {"type": "X", "value": 1, "mode": mode}

    data = serialize_event(E())
    assert data["type"] == "X"
    assert data["event_class"] == "E"


def test_action_contract_validates_without_changing_tool_availability() -> None:
    assert validate_action_arguments("Bash", {"command": "pwd"}) == []
    assert validate_action_arguments("Bash", {}) == ["missing non-empty command"]
    assert validate_action_arguments("Write", {"file_path": "a.txt", "content": "x"}) == []
    assert set(enabled_tool_aliases(full_config())) == set(
        enabled_tool_aliases(full_config().without("F2.1"))
    )


def test_context_assembly_feature_changes_answer_outcome(tmp_path: Path) -> None:
    off_workspace = tmp_path / "off"
    on_workspace = tmp_path / "on"
    off_workspace.mkdir()
    on_workspace.mkdir()
    (off_workspace / "SKILL.md").write_text("EXPECTED_ANSWER=PawBench skill impact ok", encoding="utf-8")
    (on_workspace / "SKILL.md").write_text("EXPECTED_ANSWER=PawBench skill impact ok", encoding="utf-8")

    off = run_task_sync(
        make_skill_task(off_workspace),
        workspace_root=off_workspace,
        trace_path=tmp_path / "skill_off.jsonl",
        feature_config=full_config().without("F5.1"),
        model=SkillSensitiveModel(),
        max_iters=4,
    )
    on = run_task_sync(
        make_skill_task(on_workspace),
        workspace_root=on_workspace,
        trace_path=tmp_path / "skill_on.jsonl",
        feature_config=full_config(),
        model=SkillSensitiveModel(),
        max_iters=4,
    )
    assert off.accepted is False
    assert on.accepted is True
    assert (on_workspace / "answer.txt").read_text(encoding="utf-8") == EXPECTED_SKILL_ANSWER


def test_structured_feedback_feature_enables_recovery(tmp_path: Path) -> None:
    task = TaskSpec(
        task_id="recovery_impact",
        instruction="Create answer.txt with the recovery answer.",
        task_dir=tmp_path,
        required_artifacts=["answer.txt"],
        required_tools=["run_shell"],
        hidden_contract={"artifact_text": {"answer.txt": EXPECTED_RECOVERY_ANSWER}},
    )
    off_workspace = tmp_path / "off"
    on_workspace = tmp_path / "on"
    off_workspace.mkdir()
    on_workspace.mkdir()
    off = run_task_sync(
        task,
        workspace_root=off_workspace,
        trace_path=tmp_path / "recovery_off.jsonl",
        feature_config=full_config().without("F2.3"),
        model=RecoverySensitiveModel(),
        max_iters=4,
    )
    on = run_task_sync(
        task,
        workspace_root=on_workspace,
        trace_path=tmp_path / "recovery_on.jsonl",
        feature_config=full_config(),
        model=RecoverySensitiveModel(),
        max_iters=4,
    )
    assert off.accepted is False
    assert on.accepted is True
    assert (on_workspace / "answer.txt").read_text(encoding="utf-8") == EXPECTED_RECOVERY_ANSWER


def test_workspace_alias_is_owned_by_isolation_feature(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace_root"
    workspace.mkdir()
    task = TaskSpec(
        task_id="workspace_alias",
        instruction="Create workspace/answer.txt.",
        task_dir=workspace,
        required_artifacts=["workspace/answer.txt"],
        hidden_contract={"artifact_text": {"workspace/answer.txt": "expected"}},
    )
    trace_path = tmp_path / "workspace_alias.trace.jsonl"
    result = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=trace_path,
        feature_config=full_config(),
        model=AbsoluteWorkspacePathModel(),
        max_iters=4,
    )
    assert result.accepted is True
    assert (workspace / "workspace" / "answer.txt").read_text(encoding="utf-8") == "expected"
    assert "workspace_alias_rewrite" in [row["type"] for row in event_rows(trace_path)]


def test_workspace_alias_does_not_corrupt_host_path_ending_in_workspace(tmp_path: Path) -> None:
    guard = WorkspaceGuardMiddleware(tmp_path)
    host_workspace = tmp_path / "workspace"
    host_command = f"mkdir -p {host_workspace}"
    assert guard._normalize_workspace_aliases("Bash", {"command": host_command})["command"] == host_command
    assert guard._normalize_workspace_aliases("Bash", {"command": "mkdir -p /workspace/output"})["command"] == (
        "mkdir -p workspace/output"
    )


def test_verifier_retry_keeps_original_task_context_and_completes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace_root"
    workspace.mkdir()
    trace_path = tmp_path / "verifier_retry.jsonl"
    task = TaskSpec(
        task_id="verifier_retry",
        instruction="ORIGINAL_REPAIR_MARKER: create workspace/answer.txt containing expected.",
        task_dir=workspace,
        required_artifacts=["workspace/answer.txt"],
        hidden_contract={"artifact_text": {"workspace/answer.txt": "expected"}},
    )
    result = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=trace_path,
        feature_config=full_config(),
        model=VerifierRepairModel(),
        max_iters=4,
    )
    rows = event_rows(trace_path)
    assert result.verifier.ok is True
    assert result.completion_ok is True
    assert result.accepted is True
    assert (workspace / "workspace" / "answer.txt").read_text(encoding="utf-8") == "expected"
    assert any(row["type"] == "retry_start" for row in rows)
    assert any(row["type"] == "retry_verifier_result" and row["payload"]["ok"] is True for row in rows)


def test_permission_pause_retry_recovers_without_bypassing_safety(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trace_path = tmp_path / "permission_recovery.jsonl"
    task = TaskSpec(
        task_id="permission_recovery",
        instruction="Create answer.txt containing expected.",
        task_dir=workspace,
        required_artifacts=["answer.txt"],
        hidden_contract={"artifact_text": {"answer.txt": "expected"}},
    )
    result = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=trace_path,
        feature_config=full_config(),
        model=PermissionPauseRecoveryModel(),
        max_iters=4,
    )
    rows = event_rows(trace_path)
    assert result.accepted is True
    assert (workspace / "answer.txt").read_text(encoding="utf-8") == "expected"
    assert any(row["type"] == "permission_pause" for row in rows)
    assert any(row["type"] == "retry_verifier_result" and row["payload"]["ok"] is True for row in rows)


def test_verification_off_reports_failure_without_gating(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trace_path = tmp_path / "verification_off.jsonl"
    result = run_task_sync(
        make_skill_task(workspace),
        workspace_root=workspace,
        trace_path=trace_path,
        feature_config=full_config().without("F5.1", "F4.3", "F3.3"),
        model=SkillSensitiveModel(),
        max_iters=4,
    )
    assert result.verifier.ok is False
    assert result.verification_gated is False
    assert result.completion_ok is True
    assert result.accepted is True


def test_diagnostic_trace_off_retains_outer_audit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SKILL.md").write_text("EXPECTED_ANSWER=PawBench skill impact ok", encoding="utf-8")
    trace_path = tmp_path / "trace_off.jsonl"
    result = run_task_sync(
        make_skill_task(workspace),
        workspace_root=workspace,
        trace_path=trace_path,
        feature_config=full_config().without("F4.1"),
        model=SkillSensitiveModel(),
        max_iters=4,
    )
    rows = event_rows(trace_path)
    assert result.accepted is True
    assert not any(row["type"] == "agentscope_event" for row in rows)
    assert any(row["type"] == "run_start" and row["audit_class"] == "outer" for row in rows)
    assert all(row["event_id"] for row in rows)


def test_state_delta_records_created_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SKILL.md").write_text("EXPECTED_ANSWER=PawBench skill impact ok", encoding="utf-8")
    trace_path = tmp_path / "delta.jsonl"
    result = run_task_sync(
        make_skill_task(workspace),
        workspace_root=workspace,
        trace_path=trace_path,
        feature_config=full_config(),
        model=SkillSensitiveModel(),
        max_iters=4,
    )
    delta = next(row["payload"] for row in event_rows(trace_path) if row["type"] == "state_artifact_delta")
    assert result.accepted is True
    assert "answer.txt" in delta["created"]


def test_runtime_retrieves_persistent_memory_across_runs(tmp_path: Path) -> None:
    memory_path = tmp_path / "shared.memory.json"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    for workspace in (first_workspace, second_workspace):
        (workspace / "SKILL.md").write_text("EXPECTED_ANSWER=PawBench skill impact ok", encoding="utf-8")

    run_task_sync(
        make_skill_task(first_workspace),
        workspace_root=first_workspace,
        trace_path=tmp_path / "first.jsonl",
        memory_path=memory_path,
        feature_config=full_config(),
        model=SkillSensitiveModel(),
        max_iters=4,
    )
    second_trace = tmp_path / "second.jsonl"
    run_task_sync(
        make_skill_task(second_workspace),
        workspace_root=second_workspace,
        trace_path=second_trace,
        memory_path=memory_path,
        feature_config=full_config(),
        model=SkillSensitiveModel(),
        max_iters=4,
    )
    memory_query = next(row["payload"] for row in event_rows(second_trace) if row["type"] == "memory_query")
    assert memory_query["returned_records"]
    assert memory_query["returned_records"][0]["key"] == "task:skill_impact"
