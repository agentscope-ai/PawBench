"""Runtime adapter for Harbor ``user_agent`` tasks with a persona directory.

Some Pawbench tasks ship a rich ``user/`` (persona / dialogue policy / latent
goals / memory / history …) directory plus a multi-turn ``messages.jsonl`` but
**no** user-sim sidecar wiring, so Harbor runs only their first turn. This module
materialises a *runtime-only* task wrapper (the source task is never mutated)
that adds the **generative** user-sim MCP sidecar (``pawbench.user_sim``), so a
persona-driven simulated user drives the agent through a dynamic multi-turn
conversation. The authored ``messages.jsonl`` grounds each turn (see
``UserAgent`` guided-turns) so concrete deliverables are always conveyed.

Packaging mirrors ``data/user-sim-demo/ua-mt-notes-demo`` (the reviewed Strategy
A reference): the sidecar image bakes ``task/.user`` (persona is never visible to
the agent container) and the vendored ``pawbench.user_sim`` + CuES leaf modules,
and exposes the ``user-sim`` streamable-http MCP server on :8000.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pawbench.user_sim.workspace_patch import has_cowork_patches

from .scripted_user import load_authored_messages

# task.toml fragment appended at runtime: mark the task multi-turn, declare the
# user-sim MCP server, and surface the dedicated USER_SIM_* creds (interpolated
# from the docker-compose process env, which HarborV2Backend populates).
_TASK_TOML_FRAGMENT = """

[[environment.mcp_servers]]
name = "user-sim"
transport = "streamable-http"
url = "http://user-sim:8000/mcp"
"""

# The user-sim sidecar image is pre-built (see ``build_generative_user_image``)
# and referenced by tag here; building both ``main`` and ``user-sim`` inside one
# ``docker compose up --build`` deadlocks on Compose v2.18.
_COMPOSE_YAML = """services:
  main:
    depends_on:
      user-sim:
        condition: service_healthy

  user-sim:
    image: __GENERATIVE_USER_IMAGE__
    pull_policy: never
    environment:
      - USER_SIM_API_KEY=${USER_SIM_API_KEY:-}
      - USER_SIM_MODEL=${USER_SIM_MODEL:-}
      - USER_SIM_BASE_URL=${USER_SIM_BASE_URL:-}
      - USER_SIM_MAX_TURNS=${USER_SIM_MAX_TURNS:-16}
      - USER_SIM_TEMPERATURE=${USER_SIM_TEMPERATURE:-0.7}
      - USER_SIM_TASK_DIR=/app/task
      - USER_SIM_STATE_PATH=/logs/agent/user_sim_state.json
    volumes:
      # Long-form syntax is required: HOST_AGENT_LOGS_PATH embeds the agent id
      # (e.g. ".../harbor:openclaw/...") whose colon breaks the short form.
      - type: bind
        source: ${HOST_AGENT_LOGS_PATH}
        target: ${ENV_AGENT_LOGS_PATH}
    expose:
      - "8000"
    healthcheck:
      test: ["CMD", "python3", "-c", "import socket; s=socket.create_connection(('localhost',8000),timeout=2); s.close()"]
      interval: 2s
      timeout: 5s
      retries: 30
      start_period: 5s
"""

_COWORK_COMPOSE_YAML = """services:
  main:
    depends_on:
      user-sim:
        condition: service_healthy
    volumes:
      - type: volume
        source: pawbench-workspace
        target: /home/node/workspace

  user-sim:
    image: __GENERATIVE_USER_IMAGE__
    pull_policy: never
    environment:
      - USER_SIM_API_KEY=${USER_SIM_API_KEY:-}
      - USER_SIM_MODEL=${USER_SIM_MODEL:-}
      - USER_SIM_BASE_URL=${USER_SIM_BASE_URL:-}
      - USER_SIM_MAX_TURNS=${USER_SIM_MAX_TURNS:-16}
      - USER_SIM_TEMPERATURE=${USER_SIM_TEMPERATURE:-0.7}
      - USER_SIM_TASK_DIR=/app/task
      - USER_SIM_STATE_PATH=/logs/agent/user_sim_state.json
      - USER_SIM_WORKSPACE_ROOT=/workspace
    volumes:
      - type: bind
        source: ${HOST_AGENT_LOGS_PATH}
        target: ${ENV_AGENT_LOGS_PATH}
      - type: volume
        source: pawbench-workspace
        target: /workspace
    expose:
      - "8000"
    healthcheck:
      test: ["CMD", "python3", "-c", "import socket; s=socket.create_connection(('localhost',8000),timeout=2); s.close()"]
      interval: 2s
      timeout: 5s
      retries: 30
      start_period: 5s

volumes:
  pawbench-workspace:
"""

_DOCKERFILE = """FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/opt/user-sim
ENV CUES_PLUS_ROOT=/opt/user-sim/cues
ENV USER_SIM_TASK_DIR=/app/task
ENV USER_SIM_STATE_PATH=/logs/agent/user_sim_state.json
ENV USER_SIM_WORKSPACE_ROOT=/workspace

RUN pip install --no-cache-dir "fastmcp>=3.0" "openai>=1.40" "pyyaml>=6"

COPY vendor/ /opt/user-sim/
COPY task/ /app/task/
COPY workspace/ /workspace/
RUN rm -rf /workspace/.copaw /workspace/.docker

EXPOSE 8000
CMD ["python3", "-m", "pawbench.user_sim.mcp_server"]
"""

_PROTOCOL = """<pawbench-multi-turn-protocol>
You are collaborating with a **real user** over multiple turns. The user reveals
their needs gradually — you must converse to clarify them, then deliver. Reach
the user through the `user-sim` MCP server:
1. Your FIRST task action MUST be `start_conversation()`. Do not inspect files,
   run commands, edit the workspace, or answer the task before this call.
   It returns JSON; read `user_message` as the authoritative opening request.
2. A normal assistant response is NOT delivered to the user. The only way to
   reply is `send_message_to_user(message)`. After doing the work for each turn,
   call it with your complete response instead of ending your run.
3. Read the returned JSON. If `conversation_over` is false, treat
   `user_message` as the next authoritative request and continue working.
4. Do not finish until a `send_message_to_user` result explicitly reports
   `conversation_over: true`; then ensure every requested deliverable is on disk.
5. Keep using the same workspace throughout the conversation. Requirements are
   intentionally withheld until `start_conversation()` returns them.
</pawbench-multi-turn-protocol>
"""

# CuES-plus leaf modules the sidecar UserAgent needs (mirrors sync_user_sim.sh).
_CUES_RUNTIME_MODULES = (
    "__init__",
    "llm",
    "errors",
    "redaction",
    "shared_utils",
    "jsonl",
    "files",
    "multimodal",
    "prompts",
)


def _persona_dir(task_dir: Path) -> Path | None:
    """Return the task's persona directory (``.user`` preferred, else ``user``)."""
    for name in (".user", "user"):
        candidate = task_dir / name
        if (candidate / "persona.md").is_file():
            return candidate
    return None


def is_generative_user_task(task: Any) -> bool:
    """Whether *task* can run the generative persona user-sim.

    Requires both a persona directory (``persona.md``) — so CuES has a character
    to role-play — and at least two authored user turns, i.e. genuine multi-turn
    intent. Single-turn ``user_agent`` tasks (one authored turn) are untouched.
    """
    task_dir = Path(getattr(task, "task_dir", task))
    if _persona_dir(task_dir) is None:
        return False
    return len(load_authored_messages(task_dir)) > 1


def _repo_root() -> Path:
    # pawbench/harbor_v2/generative_user.py → repo root is three parents up.
    return Path(__file__).resolve().parents[2]


def _vendor_user_sim(server_dir: Path) -> None:
    """Vendor ``pawbench.user_sim`` + CuES leaf modules into the sidecar context.

    Python port of ``sync_user_sim.sh``: only the light leaf modules reachable
    from ``src.client.user_agent`` are copied — never the heavy CuES
    builder/filter/train/sandbox stack.
    """
    repo = _repo_root()
    src_pawbench = repo / "pawbench" / "user_sim"
    src_cues = repo / "examples" / "CuES-plus" / "src"
    for path in (src_pawbench, src_cues):
        if not path.is_dir():
            raise FileNotFoundError(f"user-sim vendor source missing: {path}")

    vendor = server_dir / "vendor"
    if vendor.exists():
        shutil.rmtree(vendor)

    # 1. pawbench.user_sim integration glue.
    dst_pawbench = vendor / "pawbench"
    dst_pawbench.mkdir(parents=True)
    (dst_pawbench / "__init__.py").write_text(
        "# vendored namespace shim for the user-sim sidecar\n", encoding="utf-8"
    )
    shutil.copytree(src_pawbench, dst_pawbench / "user_sim")
    shutil.rmtree(dst_pawbench / "user_sim" / "tests", ignore_errors=True)

    # 2. CuES-plus UserAgent (light leaf modules only).
    dst_cues_src = vendor / "cues" / "src"
    (dst_cues_src / "client").mkdir(parents=True)
    (dst_cues_src / "runtime" / "prompts" / "client").mkdir(parents=True)
    shutil.copy2(src_cues / "__init__.py", dst_cues_src / "__init__.py")
    shutil.copy2(src_cues / "defaults.py", dst_cues_src / "defaults.py")
    # NB: src/client/__init__.py is deliberately NOT vendored (it drags in the
    # OSS/E2B/QwenPaw stack); _cues.py registers a lightweight src.client shim.
    shutil.copy2(
        src_cues / "client" / "user_agent.py",
        dst_cues_src / "client" / "user_agent.py",
    )
    for mod in _CUES_RUNTIME_MODULES:
        shutil.copy2(
            src_cues / "runtime" / f"{mod}.py",
            dst_cues_src / "runtime" / f"{mod}.py",
        )
    for md in (src_cues / "runtime" / "prompts" / "client").glob("*.md"):
        shutil.copy2(md, dst_cues_src / "runtime" / "prompts" / "client" / md.name)

    # Prune caches so image content-addressing is stable.
    for cache in vendor.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def _hash_tree(digest: hashlib._Hash, root: Path) -> None:
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())


def generative_user_image_name(server_dir: Path) -> str:
    """Content-addressed local image name for a materialised sidecar context."""
    digest = hashlib.sha256()
    _hash_tree(digest, server_dir / "task")
    _hash_tree(digest, server_dir / "vendor")
    _hash_tree(digest, server_dir / "workspace")
    digest.update((server_dir / "Dockerfile").read_bytes())
    return f"pawbench-generative-user:{digest.hexdigest()[:16]}"


def build_generative_user_image(server_dir: Path, image_name: str) -> None:
    """Build the sidecar image before Compose to avoid multi-service deadlocks."""
    subprocess.run(
        ["docker", "build", "--progress=plain", "-t", image_name, "."],
        cwd=server_dir,
        check=True,
        capture_output=True,
        text=True,
        timeout=1200,
    )


def materialize_generative_task(task: Any, destination: Path) -> tuple[Path, Path]:
    """Create a runtime-only Harbor task wrapper with a generative user-sim.

    Returns ``(runtime_task_dir, sidecar_build_context)``. The source task is
    copied, never mutated.
    """
    source = Path(task.task_dir)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)

    compose_path = destination / "environment" / "docker-compose.yaml"
    if compose_path.exists():
        raise ValueError(
            f"Task {getattr(task, 'task_id', source.name)!r} already ships a "
            "docker-compose.yaml; automatic sidecar composition is ambiguous."
        )

    # task.toml: declare the user-sim MCP server (multi-turn marker + creds are
    # injected by the backend via TrialConfig.environment.env).
    task_toml = destination / "task.toml"
    task_toml.write_text(
        task_toml.read_text(encoding="utf-8").rstrip() + _TASK_TOML_FRAGMENT,
        encoding="utf-8",
    )

    # instruction.md: expose only the protocol.  The first authored request is
    # intentionally withheld behind start_conversation(); repeating it here lets
    # an agent bypass the dialogue and guess later-turn requirements.
    instruction = destination / "instruction.md"
    instruction.write_text(
        _PROTOCOL.rstrip() + "\n",
        encoding="utf-8",
    )

    # Sidecar build context: Dockerfile + vendored code + task/.user + messages.
    server_dir = destination / "environment" / "user-sim-server"
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "Dockerfile").write_text(_DOCKERFILE, encoding="utf-8")
    _vendor_user_sim(server_dir)

    task_ctx = server_dir / "task"
    task_ctx.mkdir(parents=True, exist_ok=True)
    persona = _persona_dir(source)
    if persona is None:  # pragma: no cover - guarded by is_generative_user_task
        raise ValueError(f"Task {source.name!r} has no persona directory")
    # Persona is baked as ``.user`` (CuES reads ``<task_dir>/.user``) and stays
    # inside the sidecar image — never mounted into the agent container.
    shutil.copytree(persona, task_ctx / ".user")
    authored_patches = persona / "patches"
    if authored_patches.is_dir():
        # CuES upstream discovers cowork assets at task-root/.patch.  Keep the
        # original .user/patches copy as provenance and add the canonical view.
        shutil.copytree(authored_patches, task_ctx / ".patch")
    messages = source / "messages.jsonl"
    if messages.is_file():
        shutil.copy2(messages, task_ctx / "messages.jsonl")

    # Cowork patches run in the user-sim sidecar but must affect the exact
    # filesystem the agent sees.  Seed a named Docker volume from the sidecar
    # image's /workspace (the main service waits for user-sim, so copy-up occurs
    # here first), then mount that volume at the task's canonical workdir in
    # main.  For non-cowork tasks the directory is empty and no volume is used.
    workspace_seed = server_dir / "workspace"
    workspace_seed.mkdir(parents=True, exist_ok=True)
    cowork = has_cowork_patches(source)
    if cowork:
        assets = source / "environment" / "assets"
        if not assets.is_dir():
            raise FileNotFoundError(
                f"Cowork task {source.name!r} has no environment/assets workspace"
            )
        shutil.copytree(assets, workspace_seed, dirs_exist_ok=True, symlinks=True)

    image_name = generative_user_image_name(server_dir)
    compose_template = _COWORK_COMPOSE_YAML if cowork else _COMPOSE_YAML
    compose_path.write_text(
        compose_template.replace("__GENERATIVE_USER_IMAGE__", image_name),
        encoding="utf-8",
    )
    return destination, server_dir
