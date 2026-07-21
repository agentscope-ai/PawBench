"""Runtime adapter for Harbor tasks with authored ``messages.jsonl`` turns."""

from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any


_MCP_SERVER_TOML = """

[[environment.mcp_servers]]
name = "user-sim"
transport = "streamable-http"
url = "http://user-sim:8000/mcp"
"""

_COMPOSE_YAML = """services:
  main:
    depends_on:
      user-sim:
        condition: service_healthy

  user-sim:
    image: __SCRIPTED_USER_IMAGE__
    pull_policy: never
    volumes:
      - type: bind
        source: ${HOST_AGENT_LOGS_PATH}
        target: ${ENV_AGENT_LOGS_PATH}
    expose:
      - "8000"
    healthcheck:
      test: ["CMD", "python3", "-c", "import socket; s=socket.create_connection(('localhost',8000),timeout=2); s.close()"]
      interval: 2s
      timeout: 5s
      retries: 15
      start_period: 5s
"""

_DOCKERFILE = """FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV SCRIPTED_MESSAGES_PATH=/app/task/messages.jsonl
ENV USER_SIM_STATE_PATH=/logs/agent/user_sim_state.json

RUN pip install --no-cache-dir "fastmcp>=3.0"

COPY messages.jsonl /app/task/messages.jsonl
COPY environment/.pawbench-scripted-user/server.py /app/server.py

EXPOSE 8000
CMD ["python3", "/app/server.py"]
"""

_PROTOCOL = """<pawbench-multi-turn-protocol>
This benchmark contains an authored multi-turn user conversation.
Use the `user-sim` MCP tools to conduct it:
1. Your FIRST task action MUST be `start_conversation()`. Do not inspect files,
   run commands, edit the workspace, or answer the task before this call.
2. A normal assistant response is NOT delivered to the user. After completing
   each turn, call `send_message_to_user(message)` with your complete response
   instead of ending the run.
3. Read the returned JSON. If `conversation_over` is false, process its
   `user_message` and continue working.
4. Stop only after `send_message_to_user` explicitly returns
   `conversation_over: true` and all requested deliverables have been written.
5. Requirements are intentionally withheld until the conversation starts.
</pawbench-multi-turn-protocol>
"""


def load_authored_messages(task_dir: Path) -> list[dict[str, Any]]:
    """Load valid user turns from a task's ``messages.jsonl``."""
    path = task_dir / "messages.jsonl"
    if not path.is_file():
        return []

    messages: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError:
            return []
        if (
            isinstance(item, dict)
            and item.get("role") == "user"
            and isinstance(item.get("content"), str)
            and item["content"].strip()
        ):
            messages.append(item)
    return messages


def is_scripted_multi_turn(task: Any) -> bool:
    """Return whether *task* declares at least two authored user turns."""
    task_dir = Path(getattr(task, "task_dir", task))
    return len(load_authored_messages(task_dir)) > 1


def scripted_user_image_name(task_dir: Path) -> str:
    """Return a content-addressed local image name for a runtime task."""
    digest = hashlib.sha256()
    digest.update((task_dir / "messages.jsonl").read_bytes())
    digest.update(Path(__file__).with_name("scripted_user_server.py").read_bytes())
    return f"pawbench-scripted-user:{digest.hexdigest()[:16]}"


def build_scripted_user_image(task_dir: Path, image_name: str) -> None:
    """Build the sidecar before Compose to avoid multi-service build deadlocks."""
    subprocess.run(
        [
            "docker",
            "build",
            "--progress=plain",
            "-f",
            "environment/.pawbench-scripted-user/Dockerfile",
            "-t",
            image_name,
            ".",
        ],
        cwd=task_dir,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )


def materialize_scripted_task(task: Any, destination: Path) -> Path:
    """Create a runtime-only Harbor task wrapper without mutating source data."""
    source = Path(task.task_dir)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)

    compose_path = destination / "environment" / "docker-compose.yaml"
    if compose_path.exists():
        raise ValueError(
            f"Scripted multi-turn task {task.task_id!r} already has a task-authored "
            "docker-compose.yaml; automatic sidecar composition is ambiguous."
        )

    task_toml = destination / "task.toml"
    task_toml.write_text(
        task_toml.read_text(encoding="utf-8").rstrip() + _MCP_SERVER_TOML,
        encoding="utf-8",
    )

    instruction = destination / "instruction.md"
    instruction.write_text(
        _PROTOCOL.rstrip() + "\n",
        encoding="utf-8",
    )

    server_dir = destination / "environment" / ".pawbench-scripted-user"
    server_dir.mkdir(parents=True, exist_ok=True)
    server_source = Path(__file__).with_name("scripted_user_server.py")
    shutil.copy2(server_source, server_dir / "server.py")
    (server_dir / "Dockerfile").write_text(_DOCKERFILE, encoding="utf-8")
    image_name = scripted_user_image_name(destination)
    compose_path.write_text(
        _COMPOSE_YAML.replace("__SCRIPTED_USER_IMAGE__", image_name),
        encoding="utf-8",
    )
    return destination
