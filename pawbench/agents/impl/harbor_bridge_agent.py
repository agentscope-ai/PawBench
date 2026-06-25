# -*- coding: utf-8 -*-
"""HarborBridgeAgent — run any Harbor agent inside a PawBench Docker environment.

Usage
-----
Specify an agent with the ``harbor:`` prefix in ``--agents``::

    python run_bench.py --agents harbor:hermes  --model anthropic/claude-sonnet-4-5
    python run_bench.py --agents harbor:codex   --model openai/o3
    python run_bench.py --agents harbor:aider   --model anthropic/claude-opus-4-5

The part after ``harbor:`` must match a key in :data:`HARBOR_AGENT_REGISTRY`.

Architecture
------------
``HarborBridgeAgent`` is a PawBench ``ContainerAgent``.  It wraps any Harbor
``BaseInstalledAgent`` subclass via a :class:`~pawbench.envs.harbor_shim.PawBenchEnvShim`
that translates Harbor's ``exec()``/``upload_file()`` interface into
``docker exec`` / ``docker cp`` calls against the already-running PawBench
container.

Lifecycle::

    AgentFactory.create()
        → HarborBridgeAgent.__init__()
            # imports & instantiates the Harbor agent class
    backend.setup()
        → HarborBridgeAgent.install()
            # creates a temp logs_dir on the host
            # wraps env in PawBenchEnvShim
            # calls harbor_agent.setup(shim)  ← install() + /installed-agent
    backend.run_task()
        → HarborBridgeAgent.run()
            # calls harbor_agent.run(instruction, shim, AgentContext())
    backend.post_run_collect()
        → HarborBridgeAgent.post_run_collect()
            # syncs workspace → output/ (same as other agents)

Requirements
------------
* Python ≥ 3.12 (harbor-framework requires it).
* ``harbor-framework`` installed (``pip install harbor-framework``).
  ``Dockerfile.pawbench-base`` handles this for Docker builds.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from pawbench.agents.base import ContainerAgent
from pawbench.agents.constants import AGENT_WORKSPACE, PAWBENCH_BASE_IMAGE
from pawbench.envs.base import BaseEnvironment

_logger = logging.getLogger(__name__)

# ── Harbor agent registry ─────────────────────────────────────────────────────
# Maps the short name used in ``--agents harbor:<name>`` to a lazy (module, class)
# pair.  All entries correspond to agents in harbor.agents.installed.*.
# Entries are loaded lazily at instantiation time to avoid import overhead.

_REGISTRY: dict[str, tuple[str, str]] = {
    # name                  module path                                  class name
    "hermes":       ("harbor.agents.installed.hermes",        "Hermes"),
    "aider":        ("harbor.agents.installed.aider",         "Aider"),
    "codex":        ("harbor.agents.installed.codex",         "Codex"),
    "claude-code":  ("harbor.agents.installed.claude_code",   "ClaudeCode"),
    "gemini-cli":   ("harbor.agents.installed.gemini_cli",    "GeminiCli"),
    "goose":        ("harbor.agents.installed.goose",         "Goose"),
    "qwen-code":    ("harbor.agents.installed.qwen_code",     "QwenCode"),
    "opencode":     ("harbor.agents.installed.opencode",      "OpenCode"),
    "openhands":    ("harbor.agents.installed.openhands",     "OpenHands"),
    "openhands-sdk":("harbor.agents.installed.openhands_sdk", "OpenHandsSDK"),
    "swe-agent":    ("harbor.agents.installed.swe_agent",     "SweAgent"),
    "mini-swe":     ("harbor.agents.installed.mini_swe_agent","MiniSweAgent"),
    "nemo-agent":   ("harbor.agents.installed.nemo_agent",    "NemoAgent"),
    "copilot-cli":  ("harbor.agents.installed.copilot_cli",   "CopilotCli"),
    "cursor-cli":   ("harbor.agents.installed.cursor_cli",    "CursorCli"),
    "kimi-cli":     ("harbor.agents.installed.kimi_cli",      "KimiCli"),
    "trae-agent":   ("harbor.agents.installed.trae_agent",    "TraeAgent"),
    "rovodev-cli":  ("harbor.agents.installed.rovodev_cli",   "RovodevCli"),
    "devin":        ("harbor.agents.installed.devin",         "Devin"),
    "pi":           ("harbor.agents.installed.pi",            "Pi"),
}

# Public alias for introspection (e.g. CLI help).
HARBOR_AGENT_REGISTRY: dict[str, tuple[str, str]] = _REGISTRY


def _import_harbor_class(module_path: str, class_name: str) -> type:
    """Lazily import a Harbor agent class.

    Raises a clear ``ImportError`` if harbor-framework is not installed.
    """
    import importlib
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(
            f"Cannot import harbor agent '{class_name}' from '{module_path}'.\n"
            "Ensure harbor-framework is installed:\n"
            "  pip install harbor-framework\n"
            "or build the pawbench-base Docker image which includes it."
        ) from exc
    return getattr(mod, class_name)


# ── HarborBridgeAgent ─────────────────────────────────────────────────────────

class HarborBridgeAgent(ContainerAgent):
    """PawBench agent wrapper that delegates to any Harbor ``BaseInstalledAgent``.

    Parameters
    ----------
    harbor_agent_name:
        Short name from :data:`HARBOR_AGENT_REGISTRY`, e.g. ``"hermes"``.
    model:
        Model identifier in ``provider/model`` format, forwarded to the Harbor
        agent's ``model_name`` argument.
    api_key:
        API key for the model provider.  Forwarded via ``extra_env``.
    base_url:
        Optional custom base URL for the model provider.
    version:
        Optional agent version pin (e.g. a git tag).  Not all Harbor agents
        honour this; it is forwarded as-is.
    **kwargs:
        Remaining kwargs are forwarded to the Harbor agent constructor.
    """

    def __init__(
        self,
        harbor_agent_name: str,
        model: str,
        api_key: str = "",
        base_url: str = "",
        version: str | None = None,
        **kwargs: Any,
    ) -> None:
        if harbor_agent_name not in _REGISTRY:
            known = ", ".join(sorted(_REGISTRY))
            raise ValueError(
                f"Unknown harbor agent '{harbor_agent_name}'. "
                f"Known: {known}"
            )

        super().__init__(name=f"harbor:{harbor_agent_name}", **kwargs)

        self._harbor_agent_name = harbor_agent_name
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._version = version

        # Instantiated lazily in install() so Docker image resolves first.
        self._harbor_agent: Any = None
        self._logs_dir: Path | None = None
        self._tmpdir: Any = None   # tempfile.TemporaryDirectory context manager

    # ── version ───────────────────────────────────────────────────────────────

    @property
    def version(self) -> Optional[str]:
        if self._harbor_agent is None:
            return self._version
        v = self._harbor_agent.version()
        return v if v else self._version

    # ── install ───────────────────────────────────────────────────────────────

    async def install(self, environment: BaseEnvironment) -> None:
        """Install the Harbor agent inside the PawBench container.

        Steps
        -----
        1. Create a temporary host directory for Harbor agent logs (``logs_dir``).
        2. Build ``extra_env`` from API keys so the Harbor agent can reach its LLM.
        3. Instantiate the Harbor agent class with ``logs_dir`` and ``model_name``.
        4. Wrap *environment* in :class:`~pawbench.envs.harbor_shim.PawBenchEnvShim`.
        5. Call ``harbor_agent.setup(shim)`` which calls ``harbor_agent.install(shim)``.
        """
        from pawbench.envs.harbor_shim import PawBenchEnvShim

        # Create a persistent temp dir for Harbor's local logs (setup/ trajectory).
        self._tmpdir = tempfile.TemporaryDirectory(prefix="harborbench_")
        self._logs_dir = Path(self._tmpdir.name)

        # Build extra_env from API credentials so Harbor agents can read them.
        extra_env = self._build_extra_env()

        # Instantiate the Harbor agent class.
        # Strip the "provider/" prefix from the model name before passing to the
        # harbor agent, because agents like qwen-code set OPENAI_MODEL directly
        # from model_name and the DashScope / OpenAI API rejects "openai/model".
        # The original self._model (with prefix) is still used by _build_extra_env
        # above for provider detection.
        bare_model_name = (
            self._model.split("/", 1)[1] if "/" in self._model else self._model
        )
        module_path, class_name = _REGISTRY[self._harbor_agent_name]
        HarborAgentCls = _import_harbor_class(module_path, class_name)
        self._harbor_agent = HarborAgentCls(
            logs_dir=self._logs_dir,
            model_name=bare_model_name,
            version=self._version,
            extra_env=extra_env,
            logger=_logger.getChild(self._harbor_agent_name),
        )

        shim = PawBenchEnvShim(environment, logger=_logger)

        # Skip (re-)installation if the agent was pre-installed into the image at
        # build time (see docker/preinstall_harbor_agents.py, which drops a
        # marker file at /installed-agent/<name>.installed).
        if await self._is_preinstalled(environment):
            _logger.info(
                "Harbor agent '%s' already pre-installed in image; skipping setup.",
                self._harbor_agent_name,
            )
            return

        _logger.info(
            "Installing Harbor agent '%s' (model=%s) …",
            self._harbor_agent_name, self._model,
        )

        # claude-code's install() uses `if command -v apk` to pick between
        # npm (Alpine) and `curl https://claude.ai/install.sh` (Debian/Ubuntu).
        # In regions where claude.ai is blocked the curl path returns HTML and
        # fails.  Create a stub `/usr/local/bin/apk` so the npm branch is taken
        # instead (npm/node are present in pawbench-base:latest).
        # Additionally, api.anthropic.com is blocked in some regions; we set up
        # a socat SSL bridge so Claude Code's auth check goes through the proxy.
        if self._harbor_agent_name == "claude-code":
            try:
                await environment.execute_command(
                    "command -v apk > /dev/null 2>&1 || "
                    "(echo '#!/bin/sh' > /usr/local/bin/apk && chmod +x /usr/local/bin/apk)",
                    timeout=10,
                )
                _logger.debug("Ensured apk stub exists for claude-code npm install path.")
            except Exception as _apk_err:  # noqa: BLE001
                _logger.warning("Could not create apk stub: %s", _apk_err)

            # If api.anthropic.com is geo-blocked, Claude Code's auth pre-check
            # fails with 403 before it ever reaches ANTHROPIC_BASE_URL.
            # Fix: install socat + openssl, generate a self-signed cert, then
            # start a local TLS listener on port 443 that forwards to the proxy,
            # and override api.anthropic.com in /etc/hosts.
            anthropic_proxy = (
                self._base_url
                or os.environ.get("ANTHROPIC_BASE_URL", "")
            ).rstrip("/")
            if anthropic_proxy:
                # Extract host:port for socat forward target
                import urllib.parse as _up
                _parsed = _up.urlparse(anthropic_proxy)
                _proxy_host = _parsed.hostname or "127.0.0.1"
                _proxy_port = _parsed.port or (443 if _parsed.scheme == "https" else 80)
                try:
                    setup_bridge = (
                        # Install socat silently
                        "DEBIAN_FRONTEND=noninteractive apt-get install -y -q socat > /dev/null 2>&1; "
                        # Generate self-signed cert for api.anthropic.com
                        "openssl req -x509 -newkey rsa:2048 -keyout /tmp/proxy.key "
                        "-out /tmp/proxy.crt -days 365 -nodes "
                        "-subj '/CN=api.anthropic.com' > /dev/null 2>&1; "
                        # Kill any existing listener on 443
                        "pkill -f 'socat.*443' 2>/dev/null || true; "
                        # Start TLS→TCP bridge in background
                        f"socat openssl-listen:443,cert=/tmp/proxy.crt,key=/tmp/proxy.key,"
                        f"verify=0,reuseaddr,fork "
                        f"TCP:{_proxy_host}:{_proxy_port} "
                        "> /tmp/socat.log 2>&1 & "
                        # Override api.anthropic.com in hosts
                        "grep -q 'api.anthropic.com' /etc/hosts || "
                        "echo '127.0.0.1 api.anthropic.com' >> /etc/hosts; "
                        "echo BRIDGE_OK"
                    )
                    bridge_result = await environment.execute_command(setup_bridge, timeout=60)
                    out = (bridge_result or {}).get("stdout", "") or ""
                    if "BRIDGE_OK" in out:
                        _logger.info(
                            "claude-code: HTTPS bridge api.anthropic.com:443 → %s:%d",
                            _proxy_host, _proxy_port,
                        )
                    else:
                        _logger.warning("claude-code: bridge setup may have failed: %s", out[:200])
                except Exception as _br_err:  # noqa: BLE001
                    _logger.warning("claude-code: could not set up HTTPS bridge: %s", _br_err)

        await self._harbor_agent.setup(shim)
        _logger.info("Harbor agent '%s' installed.", self._harbor_agent_name)

    async def _is_preinstalled(self, environment: BaseEnvironment) -> bool:
        """Return True if a build-time install marker exists in the container."""
        marker = f"/installed-agent/{self._harbor_agent_name}.installed"
        try:
            result = await environment.execute_command(
                f"test -f {marker} && echo PRESENT || echo ABSENT", timeout=15
            )
            return "PRESENT" in (result.get("stdout") or "")
        except Exception:  # noqa: BLE001
            return False

    # ── run ───────────────────────────────────────────────────────────────────

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
    ) -> Dict[str, Any]:
        """Run the Harbor agent on *instruction*.

        Returns a result dict compatible with PawBench's backend expectations.
        """
        if self._harbor_agent is None:
            raise RuntimeError("install() must be called before run()")

        from harbor.models.agent.context import AgentContext
        from pawbench.envs.harbor_shim import PawBenchEnvShim

        context = AgentContext()
        shim = PawBenchEnvShim(environment, logger=_logger)

        # Ensure Harbor's log directories exist inside the container before running.
        # Harbor agents pipe output via `tee /logs/agent/agent.txt`; a missing
        # directory causes the shell pipeline to exit non-zero even on success.
        try:
            mk_result = await environment.execute_command(
                "mkdir -p /logs/agent /logs/verifier /logs/artifacts && echo OK",
                timeout=15,
            )
            _logger.debug(
                "Created harbor log dirs in container: %s",
                (mk_result or {}).get("stdout", ""),
            )
        except Exception as _mk_err:  # noqa: BLE001
            _logger.warning("Failed to create harbor log dirs: %s", _mk_err)

        _logger.info(
            "Running Harbor agent '%s' …", self._harbor_agent_name
        )
        agent_exit_ok = True
        try:
            await self._harbor_agent.run(instruction, shim, context)
        except Exception as exc:  # noqa: BLE001
            # Harbor raises NonZeroAgentExitCodeError when the agent process exits
            # with a non-zero code (max-turns exhausted, partial completion, etc.).
            # The agent may still have produced partial output worth grading, so we
            # log the error and fall through to post_run_collect instead of raising.
            # Genuine infrastructure errors (e.g. container not found) will surface
            # later when post_run_collect tries to sync the workspace.
            exc_type = type(exc).__name__
            _logger.warning(
                "Harbor agent '%s' exited with error (%s); will still collect output for grading. %s",
                self._harbor_agent_name, exc_type, exc,
            )
            agent_exit_ok = False

        # populate_context_post_run is mandatory on BaseInstalledAgent.
        try:
            self._harbor_agent.populate_context_post_run(context)
        except Exception:  # noqa: BLE001
            pass

        return {
            "success": agent_exit_ok,
            "agent": f"harbor:{self._harbor_agent_name}",
            "model": self._model,
            "n_input_tokens":  context.n_input_tokens,
            "n_output_tokens": context.n_output_tokens,
            "cost_usd":        context.cost_usd,
        }

    # ── post_run_collect ──────────────────────────────────────────────────────

    async def post_run_collect(self, environment: BaseEnvironment) -> None:
        """Sync workspace → output/, copy agent logs to sessions/, then clean up."""
        # Copy known Harbor agent session/log files into the PawBench workspace
        # sessions/ dir so that build_transcript_from_session() can read them.
        _LOG_SOURCES = [
            # hermes exports its session here
            "/logs/agent/hermes-session.jsonl",
            # aider session transcript (if any)
            "/logs/agent/aider.txt",
            # mini-swe-agent log (captured via tee)
            "/logs/agent/mini-swe-agent.txt",
            # mini-swe-agent trajectory JSON
            "/logs/agent/mini-swe-agent.trajectory.json",
            # claude-code stream-json log (captured via tee)
            "/logs/agent/claude-code.txt",
            # codex stream-json log (captured via tee)
            "/logs/agent/codex.txt",
            # fallback: the raw agent log
            "/logs/agent/agent.txt",
        ]
        import subprocess as _sp
        from pawbench.envs.docker import DockerEnvironment
        if isinstance(environment, DockerEnvironment):
            container = environment.name
            # Ensure sessions/ exists before copying into it
            _sp.run(
                ["docker", "exec", container, "bash", "-c",
                 f"mkdir -p {AGENT_WORKSPACE}/sessions"],
                capture_output=True,
            )
            for log_src in _LOG_SOURCES:
                fname = Path(log_src).name
                dst = f"{AGENT_WORKSPACE}/sessions/{fname}"
                cp_result = _sp.run(
                    ["docker", "exec", container,
                     "bash", "-c", f"test -f {log_src} && cp {log_src} {dst} && echo COPIED || echo SKIPPED"],
                    capture_output=True, text=True,
                )
                outcome = (cp_result.stdout or "").strip()
                _logger.debug(
                    "post_run_collect copy %s → sessions/: %s (rc=%d)",
                    log_src, outcome, cp_result.returncode,
                )
                if outcome == "COPIED":
                    print(
                        f"  [harbor] sessions/{fname} ← {log_src}",
                        flush=True,
                    )

        await self._sync_workspace_to_output(environment, AGENT_WORKSPACE)
        if self._tmpdir is not None:
            try:
                self._tmpdir.cleanup()
            except Exception:  # noqa: BLE001
                pass
            self._tmpdir = None

    # ── teardown ──────────────────────────────────────────────────────────────

    async def teardown(self, environment: BaseEnvironment) -> None:
        if self._tmpdir is not None:
            try:
                self._tmpdir.cleanup()
            except Exception:  # noqa: BLE001
                pass
            self._tmpdir = None

    # ── helpers ───────────────────────────────────────────────────────────────

    def _build_extra_env(self) -> dict[str, str]:
        """Build extra_env dict from API credentials.

        Harbor agents read standard env vars (``ANTHROPIC_API_KEY``,
        ``OPENAI_API_KEY``, ``GOOGLE_API_KEY``, etc.).  We populate the most
        common ones from the ``api_key`` / ``base_url`` we were constructed with
        so that agents can reach their LLMs without additional configuration.
        """
        env: dict[str, str] = {}

        if self._api_key:
            # Determine provider from model string (e.g. "anthropic/claude-opus-4-5").
            provider = self._model.split("/")[0].lower() if "/" in self._model else ""

            if provider == "anthropic":
                env["ANTHROPIC_API_KEY"] = self._api_key
            elif provider == "google":
                env["GOOGLE_API_KEY"] = self._api_key
            elif provider in {"openai", "dashscope", "custom", ""}:
                env["OPENAI_API_KEY"] = self._api_key
            else:
                # Forward as both so agents that look for either can find it.
                env["OPENAI_API_KEY"] = self._api_key
                env["ANTHROPIC_API_KEY"] = self._api_key
        else:
            provider = self._model.split("/")[0].lower() if "/" in self._model else ""

        if self._base_url:
            base_url = self._base_url
            # Codex constructs its WebSocket/HTTP endpoints by appending
            # "/responses" to the base URL (stripping any path), so it tries
            # "ws://host/responses" and then "http://host/responses".  The
            # standard API path is /v1/responses, so we must ensure the base
            # URL ends with "/v1" so codex resolves "http://host/v1/responses".
            if self._harbor_agent_name == "codex":
                if not base_url.rstrip("/").endswith("/v1"):
                    base_url = base_url.rstrip("/") + "/v1"
            env["OPENAI_BASE_URL"] = base_url
            if provider == "anthropic":
                # claude-code reads ANTHROPIC_BASE_URL from os.environ; set it in
                # extra_env so the shell command receives it directly.
                # Prefer the env-var proxy URL over the model-config default
                # (https://api.anthropic.com) so relay/proxy setups work without
                # needing --base-url on every invocation.
                _DEFAULT_ANTHROPIC = "https://api.anthropic.com"
                anthropic_url = (
                    os.environ.get("ANTHROPIC_BASE_URL")
                    or (self._base_url if self._base_url.rstrip("/") != _DEFAULT_ANTHROPIC else None)
                )
                if anthropic_url:
                    env["ANTHROPIC_BASE_URL"] = anthropic_url

        # When using a proxy (ANTHROPIC_BASE_URL set), claude-code would normally
        # pass the full "provider/model" string as ANTHROPIC_MODEL.  Most proxies
        # only recognise the bare model name, so strip the prefix here.
        # extra_env is merged after claude_code.py builds its own env dict, so
        # this value wins (see harbor BaseInstalledAgent._exec merge order).
        if provider == "anthropic":
            bare_model = self._model.split("/", 1)[-1] if "/" in self._model else self._model
            anthropic_base = (
                self._base_url
                or os.environ.get("ANTHROPIC_BASE_URL", "")
            )
            if anthropic_base and bare_model:
                env["ANTHROPIC_MODEL"] = bare_model
                # Ensure all claude-code model aliases also use the bare name.
                for alias in (
                    "ANTHROPIC_DEFAULT_SONNET_MODEL",
                    "ANTHROPIC_DEFAULT_OPUS_MODEL",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                    "CLAUDE_CODE_SUBAGENT_MODEL",
                ):
                    env[alias] = bare_model
                # Allow Node.js (claude-code) to accept the self-signed cert
                # used by the local socat TLS bridge for api.anthropic.com.
                env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"

        # Also pass through any already-set env vars Harbor agents commonly read.
        for key in (
            "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY",
            "DASHSCOPE_API_KEY",
            "KIMI_API_KEY", "GLM_API_KEY",
        ):
            if key not in env and os.environ.get(key):
                env[key] = os.environ[key]

        return env
