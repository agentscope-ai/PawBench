from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import signal
import shlex
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from agentscope.message import TextBlock
from agentscope.model import ChatModelBase, ChatResponse

from pawbench_agentscope.features import FeatureConfig
from pawbench_agentscope.harbor_bridge import _atomic_json as bridge_atomic_json
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import (
    SanitizedLocalBackend,
    build_toolkit,
    dashscope_model_from_env,
    run_task_sync,
)
from pawbench_agentscope.tracing import TraceWriter
import pawbench_agentscope.verifier as verifier_module
import pawbench_agentscope.tracing as tracing_module
from pawbench_agentscope.verifier import verify_artifacts


SECRET = "opaque-runtime-secret-123456"


def full_config() -> FeatureConfig:
    return FeatureConfig.all_enabled().model_copy(update={"runtime_timeout_seconds": 30.0})


def load_candidate_script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"security_test_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def bash_output(workspace: Path, command: str) -> str:
    toolkit = build_toolkit(workspace, full_config())
    assert toolkit is not None
    tool = await toolkit.get_tool("Bash")
    assert tool is not None
    stream = await tool(command=command)
    chunks = []
    async for chunk in stream:
        chunks.extend(
            block.text
            for block in chunk.content
            if isinstance(block, TextBlock)
        )
    return "".join(chunks)


async def native_tool_output(workspace: Path, tool_name: str, **kwargs: object) -> str:
    toolkit = build_toolkit(workspace, full_config())
    assert toolkit is not None
    tool = await toolkit.get_tool(tool_name)
    assert tool is not None
    stream = await tool(**kwargs)
    chunks = []
    async for chunk in stream:
        chunks.extend(block.text for block in chunk.content if isinstance(block, TextBlock))
    return "".join(chunks)


def test_bash_subprocess_uses_workspace_bound_secret_free_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    probe = (
        "import os; "
        "root=os.environ.get('PAWBENCH_WORKSPACE_ROOT'); "
        "ok=('DASHSCOPE_API_KEY' not in os.environ and root "
        "and os.environ.get('HOME') == root and os.environ.get('TMPDIR') == root); "
        "print('BOUNDARY_OK' if ok else 'BOUNDARY_FAIL')"
    )
    command = f"python3 -c {shlex.quote(probe)}"

    output = asyncio.run(bash_output(tmp_path, command))

    assert "BOUNDARY_OK" in output
    assert "BOUNDARY_FAIL" not in output
    assert SECRET not in output


def test_tool_output_is_redacted_before_agent_consumption(tmp_path: Path) -> None:
    command = f"printf {shlex.quote('DASHSCOPE_API_KEY=' + SECRET)}"

    output = asyncio.run(bash_output(tmp_path, command))

    assert SECRET not in output
    assert "[REDACTED]" in output


@pytest.mark.parametrize(
    "command",
    (
        "printf escaped > sub/../../outside.txt",
        "printf escaped >sub/../../outside.txt",
        "printf escaped > $HOME/../outside.txt",
    ),
)
def test_bash_workspace_guard_blocks_nested_relative_escape(tmp_path: Path, command: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sub").mkdir()

    output = asyncio.run(bash_output(workspace, command))

    assert not (tmp_path / "outside.txt").exists()
    assert "Workspace guard denied" in output


def test_bash_workspace_guard_blocks_existing_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    (workspace / "link").symlink_to(outside)

    output = asyncio.run(bash_output(workspace, "printf escaped > link"))

    assert not outside.exists()
    assert "Workspace guard denied" in output


@pytest.mark.parametrize("path_style", ("relative_literal", "absolute_literal"))
def test_bash_workspace_guard_detects_paths_embedded_in_python_code(tmp_path: Path, path_style: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    target = "../outside.txt" if path_style == "relative_literal" else str(outside)
    probe = f"from pathlib import Path; Path({target!r}).write_text('escaped')"

    output = asyncio.run(bash_output(workspace, f"python3 -c {shlex.quote(probe)}"))

    assert not outside.exists()
    assert "Workspace guard denied" in output


def test_bash_workspace_guard_allows_normalized_path_that_stays_inside(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sub").mkdir()

    output = asyncio.run(bash_output(workspace, "printf safe > sub/../answer.txt"))

    assert output == ""
    assert (workspace / "answer.txt").read_text(encoding="utf-8") == "safe"


@pytest.mark.parametrize(
    ("tool_name", "kwargs"),
    (
        ("Glob", {"pattern": "**/*.txt"}),
        ("Grep", {"pattern": "WORKSPACE_ONLY", "output_mode": "content"}),
    ),
)
def test_search_tools_default_to_workspace_not_process_cwd(
    tmp_path: Path,
    tool_name: str,
    kwargs: dict[str, object],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("WORKSPACE_ONLY", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("WORKSPACE_ONLY", encoding="utf-8")

    output = asyncio.run(native_tool_output(workspace, tool_name, **kwargs))

    assert "inside.txt" in output
    assert "outside.txt" not in output


@pytest.mark.parametrize("pattern", ("../*.txt", "/tmp/*.txt", "~/secrets/*"))
def test_glob_pattern_cannot_escape_workspace(tmp_path: Path, pattern: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    output = asyncio.run(native_tool_output(workspace, "Glob", pattern=pattern))

    assert "Workspace guard denied" in output


def test_cancelled_shell_process_is_terminated(tmp_path: Path) -> None:
    async def run_and_cancel() -> None:
        backend = SanitizedLocalBackend(tmp_path)
        task = asyncio.create_task(
            backend.exec_shell(
                [sys.executable, "-c", "import time; time.sleep(30)"],
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())


def test_shell_timeout_kills_background_child_after_parent_exits(
    tmp_path: Path,
) -> None:
    child_pid_path = tmp_path / "background-child.pid"
    child_code = (
        "import os,time; from pathlib import Path; "
        f"Path({str(child_pid_path)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])"
    )

    async def run_probe():
        backend = SanitizedLocalBackend(tmp_path)
        return await backend.exec_shell(
            [sys.executable, "-c", parent_code],
            timeout=0.5,
        )

    started = time.monotonic()
    result = asyncio.run(run_probe())
    elapsed = time.monotonic() - started

    assert result.exit_code == -1
    assert result.stderr == b"timed out"
    assert elapsed < 5.0
    # Under load the group kill can win the race before the child writes its
    # pid file; that still proves no background survivor.
    if not child_pid_path.is_file():
        return
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    for _ in range(30):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        raise AssertionError(f"background child process {child_pid} is still alive")


def test_shell_output_flood_is_bounded_and_process_is_terminated(tmp_path: Path) -> None:
    async def run_probe():
        backend = SanitizedLocalBackend(tmp_path)
        backend.MAX_CAPTURE_BYTES = 4_096
        return await backend.exec_shell(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
            timeout=5,
        )

    result = asyncio.run(run_probe())

    assert result.exit_code == -1
    assert len(result.stdout) <= 4_096
    assert b"output limit exceeded (4096 bytes)" in result.stderr


def test_shell_output_limit_does_not_wait_for_detached_pipe_holder(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "detached-output-child.pid"
    child_code = (
        "import os,time; from pathlib import Path; "
        f"Path({str(child_pid_path)!r}).write_text(str(os.getpid())); "
        "os.write(1, b'x' * 1000000); time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}], start_new_session=True)"
    )

    async def run_probe():
        backend = SanitizedLocalBackend(tmp_path)
        backend.MAX_CAPTURE_BYTES = 4_096
        return await backend.exec_shell(
            [sys.executable, "-c", parent_code],
            timeout=None,
        )

    started = time.monotonic()
    result = asyncio.run(run_probe())
    elapsed = time.monotonic() - started
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    try:
        assert result.exit_code == -1
        assert b"output limit exceeded (4096 bytes)" in result.stderr
        assert elapsed < 3.0
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_verifier_uses_sanitized_env_and_redacts_failure_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    capture = tmp_path / "validator_env.json"
    probe = tmp_path / "validator.py"
    probe.write_text(
        "import json, os, pathlib\n"
        "root = os.environ['PAWBENCH_WORKSPACE_ROOT']\n"
        f"pathlib.Path({str(capture)!r}).write_text(json.dumps({{\n"
        "    'has_key': 'DASHSCOPE_API_KEY' in os.environ,\n"
        "    'home': os.environ.get('HOME'),\n"
        "    'tmp': os.environ.get('TMPDIR'),\n"
        "    'root': root,\n"
        "}))\n",
        encoding="utf-8",
    )
    task = TaskSpec(
        task_id="safe-validator",
        instruction="validate",
        task_dir=tmp_path,
        test_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(probe))}",
    )

    result = verify_artifacts(task, tmp_path)
    captured = json.loads(capture.read_text(encoding="utf-8"))

    assert result.ok is True
    assert captured == {
        "has_key": False,
        "home": str(tmp_path),
        "tmp": str(tmp_path),
        "root": str(tmp_path),
    }

    failing = tmp_path / "failing_validator.py"
    failing.write_text(
        f"print('OPENAI_API_KEY={SECRET}')\nraise SystemExit(7)\n",
        encoding="utf-8",
    )
    failed_task = task.model_copy(
        update={"test_command": f"{shlex.quote(sys.executable)} {shlex.quote(str(failing))}"}
    )
    failed = verify_artifacts(failed_task, tmp_path)
    rendered = "\n".join(failed.failed_tests)
    assert failed.ok is False
    assert SECRET not in rendered
    assert "[REDACTED]" in rendered


def test_verifier_output_flood_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier_module, "MAX_VALIDATOR_CAPTURE_BYTES", 4_096)
    probe = tmp_path / "output_flood.py"
    probe.write_text("import sys\nsys.stdout.write('x' * 1000000)\n", encoding="utf-8")
    task = TaskSpec(
        task_id="validator-output-flood",
        instruction="validate",
        task_dir=tmp_path,
        test_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(probe))}",
    )

    result = verify_artifacts(task, tmp_path)

    assert result.ok is False
    assert "output limit exceeded (4096 bytes)" in result.failed_tests[0]
    assert len(result.failed_tests[0]) < 4_200


def test_verifier_timeout_kills_descendant_process_group(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "validator-child.pid"
    probe = tmp_path / "timeout_tree.py"
    child_code = f"echo $$ > {shlex.quote(str(child_pid_path))}; exec sleep 30"
    probe.write_text(
        "#!/bin/sh\n"
        f"sh -c {shlex.quote(child_code)} &\n"
        "exec sleep 30\n",
        encoding="utf-8",
    )
    task = TaskSpec(
        task_id="validator-timeout-tree",
        instruction="validate",
        task_dir=tmp_path,
        test_command=f"/bin/sh {shlex.quote(str(probe))}",
        hidden_contract={"validator_timeout_sec": 1.0},
    )

    result = verify_artifacts(task, tmp_path)

    assert result.ok is False
    assert "TimeoutExpired" in result.failed_tests[0]
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        raise AssertionError(f"validator child process {child_pid} is still alive")


def test_verifier_output_limit_does_not_wait_for_detached_pipe_holder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier_module, "MAX_VALIDATOR_CAPTURE_BYTES", 4_096)
    child_pid_path = tmp_path / "detached-validator-child.pid"
    child_code = (
        "import os,time; from pathlib import Path; "
        f"Path({str(child_pid_path)!r}).write_text(str(os.getpid())); "
        "os.write(1, b'x' * 1000000); time.sleep(30)"
    )
    probe = tmp_path / "detached_validator.py"
    probe.write_text(
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}], start_new_session=True)\n",
        encoding="utf-8",
    )
    task = TaskSpec(
        task_id="validator-detached-output",
        instruction="validate",
        task_dir=tmp_path,
        test_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(probe))}",
        hidden_contract={"validator_timeout_sec": 10.0},
    )

    started = time.monotonic()
    result = verify_artifacts(task, tmp_path)
    elapsed = time.monotonic() - started
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    try:
        assert result.ok is False
        assert "output limit exceeded (4096 bytes)" in result.failed_tests[0]
        assert elapsed < 3.0
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_verifier_rejects_required_artifact_symlink_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("looks valid", encoding="utf-8")
    (workspace / "answer.txt").symlink_to(outside)
    task = TaskSpec(
        task_id="artifact-symlink",
        instruction="Create answer.txt.",
        task_dir=workspace,
        required_artifacts=["answer.txt"],
    )

    result = verify_artifacts(task, workspace)

    assert result.ok is False
    assert result.failed_tests == ["answer.txt: artifact path escapes workspace"]


def test_verifier_rejects_directory_as_required_file_artifact(tmp_path: Path) -> None:
    (tmp_path / "answer.txt").mkdir()
    task = TaskSpec(
        task_id="artifact-directory",
        instruction="Create answer.txt.",
        task_dir=tmp_path,
        required_artifacts=["answer.txt"],
    )

    result = verify_artifacts(task, tmp_path)

    assert result.ok is False
    assert result.failed_tests == ["answer.txt: artifact is not a regular file"]


def test_verifier_bounds_exact_text_artifact_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier_module, "MAX_EXACT_TEXT_ARTIFACT_BYTES", 4_096)
    (tmp_path / "answer.txt").write_bytes(b"x" * 4_097)
    task = TaskSpec(
        task_id="artifact-too-large",
        instruction="Create answer.txt.",
        task_dir=tmp_path,
        required_artifacts=["answer.txt"],
        hidden_contract={"artifact_text": {"answer.txt": "x"}},
    )

    result = verify_artifacts(task, tmp_path)

    assert result.ok is False
    assert result.failed_tests == ["answer.txt: exact-text artifact exceeds 4096 bytes"]


@pytest.mark.parametrize("artifact_text", [["answer.txt"], "answer.txt", {1: "expected"}])
def test_verifier_reports_malformed_exact_text_contract(
    tmp_path: Path,
    artifact_text: object,
) -> None:
    (tmp_path / "answer.txt").write_text("expected", encoding="utf-8")
    task = TaskSpec(
        task_id="malformed-artifact-text",
        instruction="Create answer.txt.",
        task_dir=tmp_path,
        required_artifacts=["answer.txt"],
        hidden_contract={"artifact_text": artifact_text},
    )

    result = verify_artifacts(task, tmp_path)

    assert result.ok is False
    assert result.failed_tests == [
        "hidden_contract.artifact_text must be an object with string keys"
    ]


@pytest.mark.parametrize(
    "script_name",
    ("real_audit_trail_loop.py", "real_feature_ablation.py"),
)
def test_api_wrapper_validator_uses_sanitized_env_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script_name: str,
) -> None:
    module = load_candidate_script(script_name)
    captured: dict[str, object] = {}
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=7,
            stdout=f"OPENAI_API_KEY={SECRET}",
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    code, output = module.validate(tmp_path)
    env = captured["env"]

    assert code == 7
    assert isinstance(env, dict)
    assert "DASHSCOPE_API_KEY" not in env
    assert env["HOME"] == str(tmp_path)
    assert SECRET not in output
    assert "[REDACTED]" in output


def test_trace_writer_recursively_redacts_payload(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace = TraceWriter(trace_path, task_id="secret-trace")

    class LeakyObject:
        def __str__(self) -> str:
            return f"OPENAI_API_KEY={SECRET}"

    row = trace.append(
        "provider_event",
        {
            "OPENAI_API_KEY": SECRET,
            "nested": {"message": f"Authorization: Bearer {SECRET}"},
            "object": LeakyObject(),
        },
    )
    raw = trace_path.read_text(encoding="utf-8")

    assert SECRET not in raw
    assert row["payload"]["OPENAI_API_KEY"] == "[REDACTED]"
    assert "[REDACTED]" in row["payload"]["nested"]["message"]
    assert "[REDACTED]" in row["payload"]["object"]


def test_trace_writer_refuses_preexisting_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text("outside-stays-intact\n", encoding="utf-8")
    trace_path = tmp_path / "trace.jsonl"
    trace_path.symlink_to(outside)

    with pytest.raises(ValueError, match="must not be a symlink"):
        TraceWriter(trace_path, task_id="symlink-trace")

    assert outside.read_text(encoding="utf-8") == "outside-stays-intact\n"


def test_trace_writer_serializes_threaded_appends(tmp_path: Path) -> None:
    trace = TraceWriter(tmp_path / "trace.jsonl", task_id="threaded-trace")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda value: trace.append("thread_event", {"value": value}), range(100)))

    rows = trace.read_events()
    assert len(rows) == 100
    assert sorted(row["event_index"] for row in rows) == list(range(1, 101))
    assert len({row["event_id"] for row in rows}) == 100


def test_trace_writer_retries_partial_os_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_write = tracing_module.os.write

    def partial_write(fd: int, value) -> int:
        return real_write(fd, value[:7])

    monkeypatch.setattr(tracing_module.os, "write", partial_write)
    trace = TraceWriter(tmp_path / "trace.jsonl", task_id="partial-write")
    trace.append("large-event", {"text": "x" * 1000})

    rows = trace.read_events()
    assert len(rows) == 1
    assert rows[0]["payload"]["text"] == "x" * 1000


def test_atomic_json_does_not_follow_predictable_temp_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"untouched": true}\n', encoding="utf-8")
    destination = tmp_path / "result.json"
    legacy_temp = tmp_path / "result.json.tmp"
    legacy_temp.symlink_to(outside)

    bridge_atomic_json(destination, {"accepted": True})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"accepted": True}
    assert json.loads(outside.read_text(encoding="utf-8")) == {"untouched": True}
    assert legacy_temp.is_symlink()


def test_dashscope_rejects_non_https_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DASHSCOPE_API_KEY",
        "BAILIAN_API_KEY",
        "ALIYUN_API_KEY",
        "ALIBABA_CLOUD_API_KEY",
        "DASHSCOPE_BASE_URL",
        "BAILIAN_BASE_URL",
        "ALIYUN_BASE_URL",
        "ALIBABA_CLOUD_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "http://provider.example/v1")

    with pytest.raises(RuntimeError, match="HTTPS"):
        dashscope_model_from_env()


@pytest.mark.parametrize("value", ["nan", "inf", "-0.1", "not-a-number"])
def test_dashscope_rejects_invalid_temperature(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("AGENTSCOPE_TEMPERATURE", value)

    with pytest.raises(RuntimeError, match="finite non-negative"):
        dashscope_model_from_env(model_name="test-model")


def test_dashscope_rejects_blank_explicit_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://provider.example/v1")

    with pytest.raises(RuntimeError, match="model name"):
        dashscope_model_from_env(model_name="   ")


@pytest.mark.parametrize("model_name", ["bad\nmodel", "bad\x00model", "模" * 100])
def test_dashscope_rejects_control_or_oversized_model_name(
    model_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://provider.example/v1")

    with pytest.raises(RuntimeError, match="model name"):
        dashscope_model_from_env(model_name=model_name)


def test_dashscope_rejects_ambiguous_thinking_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("DASHSCOPE_THINKING_ENABLE", "true")

    with pytest.raises(RuntimeError, match="must be 0 or 1"):
        dashscope_model_from_env(model_name="test-model")


class SecretFinalModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="secret-final-model",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )
        self.seen_messages: list = []

    async def _call_api(self, model: str, messages: list, tools=None, tool_choice=None, **kwargs):
        self.seen_messages = messages
        return ChatResponse(
            content=[TextBlock(text=f"DASHSCOPE_API_KEY={SECRET}")],
            is_last=True,
        )


def test_prompt_response_memory_and_run_result_are_redacted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trace_path = tmp_path / "run.jsonl"
    memory_path = tmp_path / "memory.json"
    model = SecretFinalModel()
    task = TaskSpec(
        task_id="secret-boundaries",
        instruction=f"Inspect OPENAI_API_KEY={SECRET}",
        task_dir=workspace,
    )

    result = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=trace_path,
        memory_path=memory_path,
        feature_config=full_config(),
        model=model,
        max_iters=2,
    )

    persisted = trace_path.read_text(encoding="utf-8") + memory_path.read_text(encoding="utf-8")
    outbound = "\n".join(str(message) for message in model.seen_messages)
    assert SECRET not in outbound
    assert SECRET not in persisted
    assert SECRET not in result.final_text
    assert "[REDACTED]" in result.final_text
