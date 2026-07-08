#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-install Harbor agents into the Docker image at build time.

Reuses each Harbor agent's own ``install()`` logic but executes commands
locally (we are already inside the image during ``docker build``) instead of
via ``docker exec``.  This bakes the agent CLIs into the image so that
``HarborBridgeAgent.install()`` detects the marker and skips re-installation.

Usage (inside Dockerfile RUN step)::

    RUN python3 /tmp/preinstall_harbor_agents.py mini-swe aider hermes

On success, writes /installed-agent/<name>.installed for each agent so the
runtime bridge can skip re-installing.  Exits non-zero on any failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("preinstall")

_REGISTRY: dict[str, tuple[str, str]] = {
    "hermes":       ("harbor.agents.installed.hermes",         "Hermes"),
    "openclaw":     ("harbor.agents.installed.openclaw",       "OpenClaw"),
    "aider":        ("harbor.agents.installed.aider",          "Aider"),
    "codex":        ("harbor.agents.installed.codex",          "Codex"),
    "claude-code":  ("harbor.agents.installed.claude_code",    "ClaudeCode"),
    "gemini-cli":   ("harbor.agents.installed.gemini_cli",     "GeminiCli"),
    "goose":        ("harbor.agents.installed.goose",          "Goose"),
    "qwen-code":    ("harbor.agents.installed.qwen_code",      "QwenCode"),
    "qwenpaw":      ("harbor.agents.installed.qwenpaw",        "QwenPaw"),
    "opencode":     ("harbor.agents.installed.opencode",       "OpenCode"),
    "mini-swe":     ("harbor.agents.installed.mini_swe_agent", "MiniSweAgent"),
    "swe-agent":    ("harbor.agents.installed.swe_agent",      "SweAgent"),
    "nemo-agent":   ("harbor.agents.installed.nemo_agent",     "NemoAgent"),
}

_MARKER_DIR = Path("/installed-agent")


class _ExecResult:
    __slots__ = ("return_code", "stdout", "stderr")

    def __init__(self, return_code: int, stdout: str = "", stderr: str = "") -> None:
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr


class LocalBuildEnv:
    """Harbor-compatible environment shim for build-time installs.

    Runs ``bash -c <command>`` directly in the current process (no docker exec).
    Raises ``NonZeroAgentExitCodeError`` on non-zero exit so Harbor's ``_exec``
    wrapper sees the same behaviour it would get from a real BaseEnvironment.
    """

    default_user: str | int | None = None

    def __init__(self) -> None:
        self.logger = _log.getChild("env")

    @property
    def task_os(self) -> str:
        return "linux"

    async def exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,  # ignored – build runs as root
    ) -> _ExecResult:
        # NOTE: Real Harbor ``BaseEnvironment.exec()`` implementations (Docker,
        # Modal, ...) never raise on a non-zero exit code — they just return the
        # ``ExecResult`` and let the caller decide.  Raising on non-zero here
        # would look correct at first glance (it matches the error surfaced by
        # ``BaseInstalledAgent._exec()``, which wraps ``exec_as_root``/
        # ``exec_as_agent``), but some agents' ``install()`` call
        # ``environment.exec()`` *directly* to do a non-fatal existence/version
        # probe (e.g. Codex's ``command -v codex`` check, expected to fail on a
        # fresh image).  Raising unconditionally breaks that pattern.  Preserve
        # the same contract as real environments; ``_exec()`` in
        # ``BaseInstalledAgent`` already raises ``NonZeroAgentExitCodeError``
        # itself when a command run via ``exec_as_root``/``exec_as_agent`` fails.
        run_env = dict(os.environ)
        if env:
            run_env.update(env)

        self.logger.debug("exec: %.140s", command)
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=cwd,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=timeout_sec or 1800,
        )
        return _ExecResult(proc.returncode, proc.stdout, proc.stderr)

    async def upload_file(self, source_path, target_path: str) -> None:
        subprocess.run(["cp", "--", str(source_path), target_path], check=True)

    async def upload_dir(self, source_dir, target_dir: str) -> None:
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["bash", "-c", f'cp -r "{source_dir}/." "{target_dir}/"'],
            check=True,
        )


def _import_agent_cls(name: str) -> type:
    import importlib

    module_path, class_name = _REGISTRY[name]
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


async def _install_one(name: str) -> None:
    _log.info("── pre-installing: %s ──────────────────────", name)
    AgentCls = _import_agent_cls(name)

    logs_dir = Path(tempfile.mkdtemp(prefix=f"preinstall_{name}_"))
    agent = AgentCls(logs_dir=logs_dir, model_name="openai/placeholder")

    env = LocalBuildEnv()
    await agent.setup(env)

    _MARKER_DIR.mkdir(parents=True, exist_ok=True)
    (_MARKER_DIR / f"{name}.installed").write_text("ok\n")
    _log.info("✓  %s  installed", name)


def main(argv: list[str]) -> int:
    agents = argv[1:]
    if not agents:
        _log.info("No agents specified; nothing to pre-install.")
        return 0

    unknown = [a for a in agents if a not in _REGISTRY]
    if unknown:
        _log.error("Unknown agents: %s  Known: %s", unknown, sorted(_REGISTRY))
        return 2

    for name in agents:
        try:
            asyncio.run(_install_one(name))
        except Exception as exc:  # noqa: BLE001
            _log.error("✗  %s  failed: %s", name, exc)
            return 1

    _log.info("Pre-install complete: %s", ", ".join(agents))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
