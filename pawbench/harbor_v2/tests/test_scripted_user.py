from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

from pawbench.harbor_v2.backend import HarborV2Backend
from pawbench.harbor_v2.scripted_user import (
    is_scripted_multi_turn,
    load_authored_messages,
    materialize_scripted_task,
)


def _write_task(root: Path, message_count: int = 3) -> SimpleNamespace:
    (root / "environment").mkdir(parents=True)
    (root / "environment" / "Dockerfile").write_text(
        "FROM python:3.12-slim\n", encoding="utf-8"
    )
    (root / "task.toml").write_text(
        """
schema_version = "1.0"
[metadata]
category = "user_agent"
[agent]
timeout_sec = 60
[environment]
cpus = 1
""".lstrip(),
        encoding="utf-8",
    )
    (root / "instruction.md").write_text("original first turn\n", encoding="utf-8")
    lines = [
        f'{{"turn": {turn}, "role": "user", "content": "message {turn}"}}'
        for turn in range(1, message_count + 1)
    ]
    (root / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return SimpleNamespace(task_id="ua-test", task_dir=root)


def test_detects_authored_multi_turn(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task")
    assert [item["content"] for item in load_authored_messages(task.task_dir)] == [
        "message 1",
        "message 2",
        "message 3",
    ]
    assert is_scripted_multi_turn(task) is True


def test_single_authored_turn_is_not_wrapped(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task", message_count=1)
    assert is_scripted_multi_turn(task) is False


def test_materialization_preserves_source_and_adds_runtime_sidecar(
    tmp_path: Path,
) -> None:
    task = _write_task(tmp_path / "source")
    original_toml = (task.task_dir / "task.toml").read_text(encoding="utf-8")
    original_instruction = (task.task_dir / "instruction.md").read_text(
        encoding="utf-8"
    )

    runtime = materialize_scripted_task(task, tmp_path / "runtime" / "ua-test")

    assert (task.task_dir / "task.toml").read_text(encoding="utf-8") == original_toml
    assert (
        task.task_dir / "instruction.md"
    ).read_text(encoding="utf-8") == original_instruction

    config = tomllib.loads((runtime / "task.toml").read_text(encoding="utf-8"))
    assert config["environment"]["mcp_servers"][0]["name"] == "user-sim"
    wrapped_instruction = (runtime / "instruction.md").read_text(encoding="utf-8")
    assert "start_conversation()" in wrapped_instruction
    assert original_instruction.strip() in wrapped_instruction
    compose = (runtime / "environment" / "docker-compose.yaml").read_text(
        encoding="utf-8"
    )
    assert "type: bind" in compose
    assert "source: ${HOST_AGENT_LOGS_PATH}" in compose
    assert (
        runtime / "environment" / ".pawbench-scripted-user" / "server.py"
    ).is_file()


def test_scripted_tasks_need_no_user_llm_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    task = _write_task(tmp_path / "task")
    task.metadata = {"category": "user_agent"}
    task.raw_config = {"environment": {}}
    monkeypatch.delenv("USER_SIM_MAX_TURNS", raising=False)

    backend = object.__new__(HarborV2Backend)
    assert backend._requires_user_sim(task) is True
    assert backend._build_environment_env(task, {}) == {}
