# -*- coding: utf-8 -*-
"""QwenPaw agent — installs and drives the qwenpaw HTTP server inside a Docker container."""

import json
import os
import time
from typing import Any, Dict

from pawbench.agents.base import ContainerAgent
from pawbench.agents.constants import AGENT_WORKSPACE
from pawbench.envs.base import BaseEnvironment
from pawbench.llm.model_config import get_model_config, ProviderType


_WORKING_DIR = "/app/working"
_SECRET_DIR = "/app/working.secret"
_SERVER_URL = "http://127.0.0.1:8088"

# Default qwenpaw package version to install when the binary is absent from
# the image.  Override per-task via agent config key "qwenpaw_version".
_DEFAULT_QWENPAW_VERSION = "1.1.3"

# Map our ProviderType to qwenpaw's builtin provider IDs and chat_model values.
# Builtin providers are pre-registered in qwenpaw; only api_key / model need to
# be configured via HTTP.  CUSTOM goes through the full custom-provider flow.
_BUILTIN_PROVIDER_MAP: Dict[ProviderType, Dict[str, str]] = {
    ProviderType.DASHSCOPE: {"id": "dashscope",    "chat_model": "OpenAIChatModel"},
    ProviderType.OPENAI:    {"id": "openai",        "chat_model": "OpenAIChatModel"},
    ProviderType.ANTHROPIC: {"id": "anthropic",     "chat_model": "AnthropicChatModel"},
    ProviderType.GOOGLE:    {"id": "gemini",         "chat_model": "GeminiChatModel"},
    ProviderType.AZURE:     {"id": "azure-openai",  "chat_model": "OpenAIChatModel"},
}


class QwenPawAgent(ContainerAgent):
    """QwenPaw agent implementation.

    Installs qwenpaw inside the execution environment and drives it via HTTP API.

    Lifecycle (called by backend per task, one fresh container each time):

    1. ``install()``         — fast-path if binary already present in image.
    2. ``setup()``           — wipe leftover sessions, start Xvfb + qwenpaw server.
    3. ``run()``             — write call_agent.py (provider setup + agent call),
                               execute it, collect output.
    4. ``post_run_collect()``— sync ~/.qwenpaw/~/.copaw paths into standard workspace.
    5. ``teardown()``        — remove temp files.

    Provider configuration is done entirely via qwenpaw's HTTP API inside
    call_agent.py, mirroring the approach in copaw_rl/utils/setup_provider.py.
    No provider JSON files are pre-written to disk.
    """

    def __init__(self, name: str = "qwenpaw", **kwargs: Any):
        super().__init__(name, **kwargs)
        self._model_config = None
        self._api_key: str = ""
        self._base_url: str = ""
        self._generate_kwargs: Dict[str, Any] = {}
        self._qwenpaw_version: str = _DEFAULT_QWENPAW_VERSION

    # ── config ────────────────────────────────────────────────────────────────

    def _compute_config(self) -> None:
        """Resolve model / api_key / base_url / generate_kwargs and store as instance vars."""
        model_identifier = self.config.get("model", "dashscope/qwen3.6-plus")
        self._model_config = get_model_config(model_identifier)
        self._api_key = (
            self.config.get("api_key")
            or self._model_config.api_key
            or os.environ.get("OPENAI_API_KEY", "")
        )
        raw_url = self.config.get("base_url") or self._model_config.base_url or ""
        # Normalize: OpenAI-compatible custom endpoints need /v1 suffix so that
        # qwenpaw's image probe hits /v1/chat/completions (correct path).
        # Without /v1, probe goes to /chat/completions → unexpected response →
        # AttributeError → model marked UNAVAILABLE → global-fallback.
        # Skip normalization for dashscope and anthropic (they manage their own paths).
        if raw_url and self._model_config.provider not in (
            ProviderType.DASHSCOPE, ProviderType.ANTHROPIC, ProviderType.GOOGLE
        ):
            stripped = raw_url.rstrip("/")
            if not stripped.endswith("/v1"):
                raw_url = stripped + "/v1"
        self._base_url = raw_url
        # generate_kwargs can be set in agent config (e.g. {"temperature": 0})
        # and is forwarded to qwenpaw's provider via PUT /models/{id}/config.
        self._generate_kwargs = self.config.get("generate_kwargs") or {}
        # Package version to install when the binary is absent from the image.
        self._qwenpaw_version: str = (
            self.config.get("qwenpaw_version") or _DEFAULT_QWENPAW_VERSION
        )

    # ── installation ──────────────────────────────────────────────────────────

    async def install(self, environment: BaseEnvironment) -> None:
        want_ver = self._qwenpaw_version
        pkg_spec = f"qwenpaw=={want_ver}"

        check = await environment.execute_command(
            "command -v qwenpaw || command -v copaw",
            timeout=30,
        )
        if check.get("returncode", 1) == 0 and check.get("stdout", "").strip():
            # Binary present — verify the installed version matches what we want.
            ver_check = await environment.execute_command(
                f"python3 -c \"import qwenpaw; print(qwenpaw.__version__)\" 2>/dev/null || "
                f"pip show qwenpaw 2>/dev/null | grep '^Version:' | awk '{{print $2}}'",
                timeout=15,
            )
            installed_ver = (ver_check.get("stdout") or "").strip()
            if installed_ver == want_ver:
                # Correct version already present — just ensure 'copaw' alias.
                await self._ensure_copaw_alias(environment)
                return
            # Wrong version — reinstall below (fall through).

        await environment.execute_command(
            "apt-get update && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip git curl",
            timeout=120,
        )
        await environment.execute_command(
            f"pip install '{pkg_spec}' --quiet 2>&1 | tail -5",
            timeout=600,
        )
        await self._ensure_copaw_alias(environment)

    async def _ensure_copaw_alias(self, environment: BaseEnvironment) -> None:
        """Create /usr/local/bin/copaw → qwenpaw shim if not already present."""
        await environment.execute_command(
            "if ! command -v copaw >/dev/null 2>&1 && command -v qwenpaw >/dev/null 2>&1; then "
            "  printf '%s\n' '#!/usr/bin/env bash' 'exec qwenpaw \"$@\"' > /usr/local/bin/copaw && "
            "  chmod +x /usr/local/bin/copaw; "
            "fi",
            timeout=15,
        )

    # ── setup ─────────────────────────────────────────────────────────────────

    async def setup(self, environment: BaseEnvironment) -> None:
        """Install binary, wipe leftover sessions, start server.

        Provider configuration is deferred to run() / call_agent.py so that
        it is done via the HTTP API after the server is up, identical to the
        copaw_rl reference implementation.
        """
        self._compute_config()
        await self.install(environment)
        await self._patch_agentscope(environment)
        await self._wipe_sessions(environment)

        await self._start_xvfb(environment)
        await self._start_server(environment)

    # ── setup sub-steps ───────────────────────────────────────────────────────

    async def _patch_agentscope(self, environment: BaseEnvironment) -> None:
        """Patch agentscope to persist _model_trajectory in the session JSON.

        Mirrors CoPaw-Pro's model_trajectory.patch.  Two changes are made to
        agentscope's _react_agent.py:

        1. __init__: initialise ``self._model_trajectory = []`` and then call
           ``register_state("_model_trajectory")`` so the list is included in
           the session JSON written to disk at the end of each run.

        2. _reasoning: after ``formatter.format()`` builds the prompt list,
           append ``{"messages": prompt}`` to ``_model_trajectory`` so every
           raw API request is recorded.

        This is a serialisation-only change: it never modifies the messages
        sent to the model or the tool calls executed.  ``register_state``
        requires the attribute to exist and be an empty (JSON-serialisable)
        list at registration time – hence the initialisation in step 1 comes
        *before* the register call.
        """
        _PATCH_CMD = r"""python3 - <<'__PYEOF__'
import pathlib, re

p = pathlib.Path(
    '/usr/local/lib/python3.11/site-packages/agentscope/agent/_react_agent.py'
)
if not p.exists():
    print('[patch] _react_agent.py not found, skipping')
    raise SystemExit(0)

t = p.read_text(encoding='utf-8')

if '_model_trajectory' in t:
    print('[patch] already patched')
    raise SystemExit(0)

# ── Patch 1: __init__ ────────────────────────────────────────────────────────
# Insert initialisation + register_state right after the existing
#   self.register_state("_sys_prompt")
OLD_INIT = '        self.register_state("_sys_prompt")'
NEW_INIT = (
    '        self._model_trajectory = []\n'
    '        self.register_state("_sys_prompt")\n'
    '        self.register_state("_model_trajectory")'
)
if OLD_INIT not in t:
    print('[patch] ERROR: could not locate register_state("_sys_prompt")')
    raise SystemExit(1)
t = t.replace(OLD_INIT, NEW_INIT, 1)

# ── Patch 2: _reasoning ──────────────────────────────────────────────────────
# Capture the formatted prompt just before the model is called.
# The block to find (unique in _reasoning):
#   await self.memory.delete_by_mark(mark=_MemoryMark.HINT)
#
#   res = await self.model(
OLD_REASONING = (
    '        await self.memory.delete_by_mark(mark=_MemoryMark.HINT)\n'
    '\n'
    '        res = await self.model('
)
NEW_REASONING = (
    '        await self.memory.delete_by_mark(mark=_MemoryMark.HINT)\n'
    '\n'
    '        try:\n'
    '            self._model_trajectory.append({"messages": list(prompt)})\n'
    '        except Exception:\n'
    '            pass\n'
    '\n'
    '        res = await self.model('
)
if OLD_REASONING not in t:
    print('[patch] ERROR: could not locate _reasoning model call anchor')
    raise SystemExit(1)
t = t.replace(OLD_REASONING, NEW_REASONING, 1)

p.write_text(t, encoding='utf-8')
print('[patch] _model_trajectory patched successfully')

# Quick syntax check
import ast
ast.parse(p.read_text(encoding='utf-8'))
print('[patch] syntax OK')
__PYEOF__
"""
        await environment.execute_command(_PATCH_CMD, timeout=15)

    async def _wipe_sessions(self, environment: BaseEnvironment) -> None:
        """Remove leftover session files so each task starts with a clean transcript."""
        await environment.execute_command(
            f"rm -f {AGENT_WORKSPACE}/sessions/default_*.json "
            f"{AGENT_WORKSPACE}/sessions/*.jsonl "
            f"{AGENT_WORKSPACE}/sessions/*.jsonl.lock 2>/dev/null || true",
            timeout=15,
        )

    async def _start_xvfb(self, environment: BaseEnvironment) -> None:
        """Start Xvfb + dbus + xfsettingsd for GUI-capable tasks (no-op if absent)."""
        xvfb_cmd = (
            "if command -v Xvfb >/dev/null 2>&1; then "
            "  rm -f /tmp/.X1-lock /tmp/.X11-unix/X1; "
            "  mkdir -p /tmp/.X11-unix; "
            "  nohup Xvfb :1 -screen 0 1280x800x24 >/tmp/xvfb.log 2>&1 & "
            "  for i in $(seq 1 30); do "
            "    [ -S /tmp/.X11-unix/X1 ] && echo '[xvfb] socket ready' && break; "
            "    sleep 0.2; "
            "  done; "
            "  export DISPLAY=:1; "
            "  if command -v dbus-daemon >/dev/null 2>&1; then "
            "    export DBUS_SESSION_BUS_ADDRESS=$(dbus-daemon --session --fork --print-address 2>/dev/null); "
            "    echo \"[dbus] session bus: $DBUS_SESSION_BUS_ADDRESS\"; "
            "    sleep 0.5; "
            "  fi; "
            "  if command -v xfsettingsd >/dev/null 2>&1; then "
            "    nohup xfsettingsd --no-daemon >/tmp/xfsettingsd.log 2>&1 & "
            "    sleep 1; "
            "    echo '[xfsettingsd] started'; "
            "  fi; "
            "  echo '[xvfb] DISPLAY=:1 ready'; "
            "else "
            "  echo '[xvfb] not installed, skipping'; "
            "fi"
        )
        await environment.execute_command(xvfb_cmd, timeout=60)

    # Startup script written to /tmp/qwenpaw_start.py inside the container.
    # Monkey-patches _resolve_content_url so that local image file paths are
    # converted to base64 data URLs before being forwarded to the LLM API.
    # DashScope (and most OpenAI-compatible endpoints) reject bare filesystem
    # paths like "/app/working/…/image.jpg", but accept "data:image/jpeg;base64,…".
    _QWENPAW_START_SCRIPT = """\
import base64
import mimetypes
import os
import sys

# ── monkey-patch: local image path → base64 data URL ──────────────────────
try:
    import qwenpaw.app.runner.utils as _qw_utils

    _orig_resolve = _qw_utils._resolve_content_url

    def _patched_resolve_content_url(url: str) -> str:
        resolved = _orig_resolve(url)
        if (
            resolved
            and isinstance(resolved, str)
            and not resolved.startswith(("http://", "https://", "data:"))
            and os.path.isfile(resolved)
        ):
            mime, _ = mimetypes.guess_type(resolved)
            if mime and mime.startswith("image/"):
                if mime == "image/jpg":
                    mime = "image/jpeg"
                with open(resolved, "rb") as _f:
                    data = base64.b64encode(_f.read()).decode("utf-8")
                return f"data:{mime};base64,{data}"
        return resolved

    _qw_utils._resolve_content_url = _patched_resolve_content_url
except Exception:
    pass  # monkey-patch is optional; skip if qwenpaw internals changed

# ── monkey-patch: allow custom base_url for builtin OpenAI provider ─────────
try:
    import qwenpaw.providers.provider_manager as _qw_provider_manager

    _qw_provider_manager.PROVIDER_OPENAI.freeze_url = False
except Exception:
    pass  # monkey-patch is optional; skip if qwenpaw internals changed

# ── start qwenpaw app ──────────────────────────────────────────────────────
sys.argv = ["qwenpaw", "app", "--host", "127.0.0.1", "--port", "8088"]
from qwenpaw.__main__ import cli  # noqa: E402

cli()
"""

    async def _start_server(self, environment: BaseEnvironment) -> None:
        """Start the qwenpaw HTTP server (127.0.0.1:8088) and wait until ready."""
        api_key = self._api_key
        base_url = self._base_url
        # Write the startup script (with image base64 monkey-patch) to the container.
        await environment.write_file("/tmp/qwenpaw_start.py", self._QWENPAW_START_SCRIPT)
        server_cmd = (
            f"export OPENAI_API_KEY='{api_key}' && "
            f"export OPENAI_BASE_URL='{base_url}' && "
            f"export DASHSCOPE_API_KEY='{api_key}' && "
            f"export QWENPAW_WORKING_DIR='{_WORKING_DIR}' && "
            f"export COPAW_WORKING_DIR='{_WORKING_DIR}' && "
            f"export QWENPAW_SECRET_DIR='{_SECRET_DIR}' && "
            f"export COPAW_SECRET_DIR='{_SECRET_DIR}' && "
            "export QWENPAW_TOOL_GUARD_ENABLED=false && "
            "export DISPLAY=${DISPLAY:-:1} && "
            "export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-} && "
            "nohup python3 /tmp/qwenpaw_start.py "
            ">/tmp/qwenpaw_server.log 2>&1 & "
            "echo $! > /tmp/qwenpaw_server.pid && "
            # Use /api/version (same as setup_provider._detect_api_base) for readiness.
            "for i in $(seq 1 60); do "
            f"  curl -sf {_SERVER_URL}/api/version >/dev/null 2>&1 && "
            "    echo '[qwenpaw-server] ready' && break; "
            "  sleep 1; "
            "done; "
            f"curl -sf {_SERVER_URL}/api/version >/dev/null 2>&1 || "
            "  echo '[qwenpaw-server] WARNING: server may not be ready'"
        )
        await environment.execute_command(server_cmd, timeout=90)

    # ── run ───────────────────────────────────────────────────────────────────

    async def run(self, instruction: str, environment: BaseEnvironment) -> Dict[str, Any]:
        """Configure provider via HTTP API and call the qwenpaw agent."""
        if self._model_config is None:
            self._compute_config()

        await environment.write_file("/tmp/task_instruction.txt", instruction)

        # Remove BOOTSTRAP.md / SOUL.md from the task workspace so they don't
        # distract the agent with qwenpaw-specific onboarding instructions.
        # Enabled by default (skip_bootstrap defaults to True); pass
        # skip_bootstrap=False in agent_config to keep the files.
        if self.config.get("skip_bootstrap", True):
            await environment.execute_command(
                f"rm -f {AGENT_WORKSPACE}/BOOTSTRAP.md 2>/dev/null || true",
                timeout=10,
            )

        session_id = f"openjudge_{time.strftime('%Y%m%d_%H%M%S')}"
        call_agent_script = self._build_call_agent_script(session_id)
        await environment.write_file("/tmp/call_agent.py", call_agent_script)

        inner_timeout = int(self.config.get("task_timeout_s") or 1800)
        run_cmd = (
            f"export OPENAI_API_KEY='{self._api_key}' && "
            f"export DASHSCOPE_API_KEY='{self._api_key}' && "
            f"export QWENPAW_WORKING_DIR='{_WORKING_DIR}' && "
            f"export COPAW_WORKING_DIR='{_WORKING_DIR}' && "
            f"export QWENPAW_SECRET_DIR='{_SECRET_DIR}' && "
            f"export COPAW_SECRET_DIR='{_SECRET_DIR}' && "
            "pip install requests -q 2>/dev/null || true && "
            f"timeout {inner_timeout}s python3 /tmp/call_agent.py "
            "2>&1 | tee /tmp/qwenpaw_output.txt || true"
        )
        result = await environment.execute_command(run_cmd, timeout=inner_timeout + 60)

        # Kill qwenpaw server (and its children) so it cannot hold session locks
        # or LLM connections beyond the current task, regardless of whether the
        # task completed normally or was cut short by the shell timeout.
        await environment.execute_command(
            "server_pid=$(cat /tmp/qwenpaw_server.pid 2>/dev/null) && "
            "[ -n \"$server_pid\" ] && "
            "  pkill -KILL -P \"$server_pid\" 2>/dev/null; "
            "  kill -9 \"$server_pid\" 2>/dev/null || true",
            timeout=10,
        )

        output_content = await environment.read_file("/tmp/qwenpaw_output.txt") or result["stdout"]

        await self._sync_workspace_to_output(environment, AGENT_WORKSPACE)

        session_data = await self._read_session_file(environment, session_id)

        return {
            "success": result["success"],
            "output": output_content,
            "error": result["stderr"],
            "returncode": result["returncode"],
            "session_data": session_data,
            "metrics": {
                "execution_time": 0,
                "command": run_cmd,
                "model_used": self._model_config.get_full_model_identifier(),
                "provider": self._model_config.provider.value,
                "model_name": self._model_config.model_name,
            },
        }

    def _build_call_agent_script(self, session_id: str) -> str:
        """Return the Python source for call_agent.py run inside the container.

        Mirrors the flow in copaw_rl/utils/run.py::call_agent() +
        setup_provider.py::config_provider() / config_builtin_provider():

          1. Disable tool guard
          2. Read AUTO_EVAL_GENERATE_KWARGS from env (merged with agent config)
          3. Configure provider via HTTP API (builtin or custom)
          4. Add model with multimodal metadata
          5. Activate model globally
          6. Stream POST /api/agent/process (parse SSE data: / [DONE])
          7. Wait for session file
        """
        model_config = self._model_config
        model_name = model_config.model_name
        api_key = self._api_key
        base_url = self._base_url
        generate_kwargs_from_config = self._generate_kwargs

        builtin_info = _BUILTIN_PROVIDER_MAP.get(model_config.provider)
        is_builtin = builtin_info is not None
        if is_builtin:
            provider_id = builtin_info["id"]
            chat_model = builtin_info["chat_model"]
        else:
            # CUSTOM: use an OpenAI-compatible custom provider (same as rl-server)
            provider_id = "rl-server"
            chat_model = "OpenAIChatModel"

        sessions_dir = f"{_WORKING_DIR}/workspaces/default/sessions"
        inner_timeout = int(self.config.get("task_timeout_s") or 1800)

        return (
            "import json, os, sys, time, requests\n"
            "\n"
            "instruction = open('/tmp/task_instruction.txt', encoding='utf-8').read().strip()\n"
            f"SESSION_ID   = {repr(session_id)}\n"
            "USER_ID      = 'default'\n"
            f"URL          = {repr(_SERVER_URL)}\n"
            f"API_BASE     = URL + '/api'\n"
            f"SESSIONS_DIR = {repr(sessions_dir)}\n"
            f"PROVIDER_ID  = {repr(provider_id)}\n"
            f"MODEL_ID     = {repr(model_name)}\n"
            f"BASE_URL     = {repr(base_url)}\n"
            f"API_KEY      = {repr(api_key)}\n"
            f"CHAT_MODEL   = {repr(chat_model)}\n"
            f"IS_BUILTIN   = {repr(is_builtin)}\n"
            f"TIMEOUT      = {inner_timeout}\n"
            f"GEN_KWARGS_CONFIG = {json.dumps(generate_kwargs_from_config)}\n"
            "\n"
            "headers = {'Referer': URL + '/chat', 'content-type': 'application/json'}\n"
            "\n"
            "# ── 1. Disable tool guard ──────────────────────────────────────\n"
            "try:\n"
            "    r = requests.put(\n"
            "        f'{API_BASE}/config/security/tool-guard',\n"
            "        json={'enabled': False, 'guarded_tools': None,\n"
            "              'denied_tools': [], 'custom_rules': [], 'disabled_rules': []},\n"
            "        headers=headers, timeout=10,\n"
            "    )\n"
            "    print(f'[tool-guard] {r.status_code}', flush=True)\n"
            "except Exception as e:\n"
            "    print(f'[tool-guard] FAILED: {e}', flush=True)\n"
            "\n"
            "# ── 2. Resolve generate_kwargs (config + env override) ─────────\n"
            "generate_kwargs = dict(GEN_KWARGS_CONFIG)\n"
            "gk_env = os.environ.get('AUTO_EVAL_GENERATE_KWARGS')\n"
            "if gk_env:\n"
            "    try:\n"
            "        env_gk = json.loads(gk_env)\n"
            "        if isinstance(env_gk, dict):\n"
            "            generate_kwargs.update(env_gk)\n"
            "    except Exception:\n"
            "        print('[generate_kwargs] invalid AUTO_EVAL_GENERATE_KWARGS, ignored', flush=True)\n"
            "\n"
            "# ── 3. Multimodal metadata ─────────────────────────────────────\n"
            "# Declare vision up front so qwenpaw skips the flaky red-PNG color\n"
            "# probe (RL/custom endpoints often fail it despite supporting images).\n"
            "# Builtin DashScope models use qwenpaw's own registry; this applies to\n"
            "# custom providers (rl-server, etc.) registered below.\n"
            "mm = {'supports_multimodal': True, 'supports_image': True, 'supports_video': True}\n"
            "model_entry = {'id': MODEL_ID, 'name': MODEL_ID, **mm}\n"
            "\n"
            "# ── 4. Configure provider via HTTP API ─────────────────────────\n"
            "if IS_BUILTIN:\n"
            "    # Builtin provider: PUT /config (api_key + generate_kwargs)\n"
            "    cfg = {'chat_model': CHAT_MODEL, 'generate_kwargs': generate_kwargs}\n"
            "    if API_KEY:\n"
            "        cfg['api_key'] = API_KEY\n"
            "    if BASE_URL:\n"
            "        cfg['base_url'] = BASE_URL\n"
            "    try:\n"
            "        r = requests.put(f'{API_BASE}/models/{PROVIDER_ID}/config',\n"
            "                         json=cfg, headers=headers, timeout=10)\n"
            "        print(f'[provider] config builtin {PROVIDER_ID}: {r.status_code}', flush=True)\n"
            "    except Exception as e:\n"
            "        print(f'[provider] config builtin FAILED: {e}', flush=True)\n"
            "else:\n"
            "    # Custom provider: POST /custom-providers, then PUT /config\n"
            "    try:\n"
            "        r = requests.post(f'{API_BASE}/models/custom-providers',\n"
            "            json={'id': PROVIDER_ID, 'name': PROVIDER_ID,\n"
            "                  'default_base_url': BASE_URL, 'api_key_prefix': '',\n"
            "                  'chat_model': CHAT_MODEL, 'models': [model_entry]},\n"
            "            headers=headers, timeout=10)\n"
            "        print(f'[provider] create custom {PROVIDER_ID}: {r.status_code}', flush=True)\n"
            "    except Exception as e:\n"
            "        print(f'[provider] create custom FAILED: {e}', flush=True)\n"
            "    extra = {}\n"
            "    if API_KEY:\n"
            "        extra['api_key'] = API_KEY\n"
            "    if generate_kwargs:\n"
            "        extra['generate_kwargs'] = generate_kwargs\n"
            "    if extra:\n"
            "        try:\n"
            "            r = requests.put(f'{API_BASE}/models/{PROVIDER_ID}/config',\n"
            "                             json=extra, headers=headers, timeout=10)\n"
            "            print(f'[provider] config custom {PROVIDER_ID}: {r.status_code}', flush=True)\n"
            "        except Exception as e:\n"
            "            print(f'[provider] config custom FAILED: {e}', flush=True)\n"
            "\n"
            "# ── 5. Register model (ensures activation validation passes) ───\n"
            "try:\n"
            "    r = requests.post(f'{API_BASE}/models/{PROVIDER_ID}/models',\n"
            "                      json=model_entry, headers=headers, timeout=10)\n"
            "    print(f'[model] add {MODEL_ID}: {r.status_code}', flush=True)\n"
            "except Exception as e:\n"
            "    print(f'[model] add FAILED: {e}', flush=True)\n"
            "\n"
            "# ── 6. Activate model ──────────────────────────────────────────\n"
            "try:\n"
            "    r = requests.put(f'{API_BASE}/models/active',\n"
            "        json={'provider_id': PROVIDER_ID, 'model': MODEL_ID, 'scope': 'global'},\n"
            "        headers=headers, timeout=10)\n"
            "    print(f'[model] activate {PROVIDER_ID}/{MODEL_ID}: {r.status_code} {r.text[:200]}', flush=True)\n"
            "except Exception as e:\n"
            "    print(f'[model] activate FAILED: {e}', flush=True)\n"
            "\n"
            "# ── 7. Call agent ──────────────────────────────────────────────\n"
            "os.makedirs(SESSIONS_DIR, exist_ok=True)\n"
            "payload = {\n"
            "    'input': [{'role': 'user', 'type': 'message',\n"
            "               'content': [{'type': 'text', 'text': instruction, 'status': 'created'}]}],\n"
            "    'session_id': SESSION_ID,\n"
            "    'user_id': USER_ID,\n"
            "    'channel': 'console',\n"
            "    'stream': True,\n"
            "}\n"
            "print(f'[call_agent] session_id={SESSION_ID}', flush=True)\n"
            "try:\n"
            "    resp = requests.post(f'{URL}/api/agent/process', json=payload,\n"
            "                         headers=headers, stream=True, timeout=TIMEOUT)\n"
            "    resp.raise_for_status()\n"
            "    for chunk in resp.iter_content(chunk_size=None):\n"
            "        if not chunk:\n"
            "            continue\n"
            "        text = chunk.decode('utf-8', errors='ignore').strip()\n"
            "        if not text:\n"
            "            continue\n"
            "        if text.startswith('data:'):\n"
            "            text = text[len('data:'):].strip()\n"
            "        if text == '[DONE]':\n"
            "            break\n"
            "        print(text, flush=True)\n"
            "except Exception as e:\n"
            "    print(f'[call_agent] ERROR: {e}', flush=True)\n"
            "    sys.exit(1)\n"
            "\n"
            "# ── 8. Wait for session file (with assistant response) ────────────\n"
            "session_file = f'{SESSIONS_DIR}/{USER_ID}_{SESSION_ID}.json'\n"
            "for _ in range(60):\n"
            "    try:\n"
            "        if os.path.isfile(session_file) and os.path.getsize(session_file) > 10:\n"
            "            with open(session_file, encoding='utf-8') as _sf:\n"
            "                _d = json.load(_sf)\n"
            "            _mem = _d.get('agent', {}).get('memory', {}).get('content', [])\n"
            "            _traj = _d.get('agent', {}).get('_model_trajectory', [])\n"
            "            _has_resp = any((t.get('response') or []) for t in _traj)\n"
            "            if len(_mem) > 1 or _has_resp:\n"
            "                break\n"
            "    except Exception:\n"
            "        pass\n"
            "    time.sleep(1)\n"
            "if not os.path.isfile(session_file):\n"
            "    print(f'[call_agent] WARNING: session file not found: {session_file}', flush=True)\n"
            "    sys.exit(0)\n"
            "print(f'[call_agent] session file ready: {session_file}', flush=True)\n"
            "try:\n"
            "    with open(session_file, encoding='utf-8') as f:\n"
            "        data = json.load(f)\n"
            "    traj = data.get('agent', {}).get('_model_trajectory', [])\n"
            "    if traj:\n"
            "        last = traj[-1]\n"
            "        resp_items = last.get('response', [])\n"
            "        final_text = ''\n"
            "        if isinstance(resp_items, str):\n"
            "            final_text = resp_items\n"
            "        elif isinstance(resp_items, list):\n"
            "            for item in resp_items:\n"
            "                if isinstance(item, dict) and item.get('type') == 'text':\n"
            "                    final_text = item.get('text', '')\n"
            "                    break\n"
            "        if final_text:\n"
            "            print(json.dumps({'response': final_text, 'status': 'success'}), flush=True)\n"
            "except Exception as e:\n"
            "    print(f'[call_agent] WARNING: could not extract final text: {e}', flush=True)\n"
        )

    async def _read_session_file(self, environment: BaseEnvironment, session_id: str) -> str:
        """Return the newest qwenpaw session JSON, retrying until assistant response is present."""
        import asyncio as _asyncio
        import json as _json
        for _attempt in range(20):
            try:
                ls_result = await environment.execute_command(
                    f"ls -t {_WORKING_DIR}/workspaces/default/sessions/default_*.json"
                    " 2>/dev/null | head -1",
                    timeout=10,
                )
                session_path = (ls_result.get("stdout") or "").strip()
                if session_path and ls_result.get("returncode") == 0:
                    raw = await environment.read_file(session_path) or ""
                    if raw:
                        try:
                            _d = _json.loads(raw)
                            _mem = _d.get("agent", {}).get("memory", {}).get("content", [])
                            _traj = _d.get("agent", {}).get("_model_trajectory", [])
                            _has_resp = any((t.get("response") or []) for t in _traj)
                            if len(_mem) > 1 or _has_resp:
                                return raw
                        except Exception:
                            return raw
            except Exception:
                pass
            if _attempt < 19:
                await _asyncio.sleep(1)
        return ""

    # ── post-run hook ─────────────────────────────────────────────────────────

    async def post_run_collect(self, environment: BaseEnvironment) -> None:
        """Sync ~/.qwenpaw and ~/.copaw workspace paths into the standard location."""
        staging = AGENT_WORKSPACE
        collect_cmd = rf"""
STAGING={staging}
mkdir -p "$STAGING"
for src_dir in ~/.qwenpaw/workspaces/default ~/.copaw/workspaces/default; do
  [ -d "$src_dir" ] || continue
  find "$src_dir" -maxdepth 5 -type f \
      ! -path '*/site-packages/*' ! -name '*.py' ! -name '*.pyc' \
      2>/dev/null | while read -r f; do
    rel="${{f#${{src_dir}}/}}"
    dest="$STAGING/$rel"
    mkdir -p "$(dirname "$dest")"
    [ ! -s "$dest" ] && [ -s "$f" ] && cp "$f" "$dest" 2>/dev/null || true
  done
done
DEST="$STAGING/output"
mkdir -p "$DEST"
for src_dir in ~/.qwenpaw/workspaces/default/output ~/.copaw/workspaces/default/output; do
  [ -d "$src_dir" ] || continue
  find "$src_dir" -maxdepth 2 -type f \
      ! -path '*/site-packages/*' ! -name '*.py' ! -name '*.pyc' \
      2>/dev/null | while read -r f; do
    bn=$(basename "$f")
    dest_file="$DEST/$bn"
    [ ! -s "$dest_file" ] && [ -s "$f" ] && cp "$f" "$dest_file" 2>/dev/null || true
  done
done
"""
        await environment.execute_command(collect_cmd, timeout=20)

        # Extract the real system prompt sent to the model from the session JSON.
        #
        # agentscope's ReactAgent stores only the base prompt in agent._sys_prompt.
        # The skills prompt (SKILL.md descriptions, ~8 KB) is appended at request
        # time via the sys_prompt property and appears in _model_trajectory[0].
        # Prefer _model_trajectory for accuracy; fall back to _sys_prompt.
        _CAPTURE_PROMPT = r"""python3 - <<'__PYEOF__'
import json, pathlib

sessions_dir = pathlib.Path('/app/working/workspaces/default/sessions')
sys_prompt = ''
if sessions_dir.is_dir():
    candidates = sorted(
        (p for p in sessions_dir.glob('*.json') if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
            agent = data.get('agent') or {}
            # Prefer the actual system message from the first model request
            trajectory = agent.get('_model_trajectory') or []
            if trajectory:
                first_msgs = trajectory[0].get('messages') or []
                for m in first_msgs:
                    if m.get('role') == 'system':
                        content = m.get('content', '')
                        if isinstance(content, list):
                            content = '\n'.join(
                                b.get('text', '') for b in content
                                if isinstance(b, dict) and b.get('type') == 'text'
                            )
                        sys_prompt = content
                        break
            # Fall back to stored base prompt if trajectory is absent
            if not sys_prompt:
                sys_prompt = agent.get('_sys_prompt', '')
            if sys_prompt:
                break
        except Exception:
            continue

print(sys_prompt)
__PYEOF__
"""
        sp_result = await environment.execute_command(_CAPTURE_PROMPT, timeout=15)
        sp_text = (sp_result.get("stdout") or "").strip()
        self._last_system_prompt: "str | None" = sp_text if sp_text else None

    # ── teardown ──────────────────────────────────────────────────────────────

    async def teardown(self, environment: BaseEnvironment) -> None:
        await environment.execute_command(
            "rm -f /tmp/qwenpaw_output.txt /tmp/call_agent.py "
            "/tmp/task_instruction.txt",
            timeout=10,
        )

    @property
    def version(self) -> str:
        return getattr(self, "_qwenpaw_version", _DEFAULT_QWENPAW_VERSION)
