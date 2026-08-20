from __future__ import annotations

import asyncio
import importlib.util
import json
import shlex
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from agentscope.message import TextBlock
from agentscope.model import ChatModelBase, ChatResponse

from pawbench_agentscope.features import FeatureConfig
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import (
    SanitizedLocalBackend,
    build_toolkit,
    dashscope_model_from_env,
    run_task_sync,
)
from pawbench_agentscope.tracing import TraceWriter
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
