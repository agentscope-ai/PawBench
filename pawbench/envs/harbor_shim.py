# -*- coding: utf-8 -*-
"""Harbor environment shim — bridges PawBench's DockerEnvironment to Harbor's exec() interface.

Harbor's ``BaseInstalledAgent`` expects an environment with the signature::

    async def exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult

    async def upload_file(self, source_path: str | Path, target_path: str) -> None
    async def upload_dir(self, source_dir: str | Path, target_dir: str) -> None

    default_user: str | int | None
    logger: logging.Logger

This module provides ``PawBenchEnvShim`` — a duck-typed adapter that satisfies
that interface by delegating to a running Docker container managed by PawBench.
No Harbor classes are imported in this module's top-level scope so it loads
without harbor-framework installed.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pawbench.envs.docker import DockerEnvironment


_logger = logging.getLogger(__name__)


class ExecResult:
    """Minimal ExecResult compatible with Harbor's ExecResult (Pydantic model).

    Uses plain attributes instead of Pydantic so it loads without harbor being
    installed.  Duck typing makes this interchangeable wherever ExecResult is used.
    """

    __slots__ = ("return_code", "stdout", "stderr")

    def __init__(
        self,
        return_code: int,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr

    def __repr__(self) -> str:
        return (
            f"ExecResult(return_code={self.return_code}, "
            f"stdout={self.stdout!r:.60}, stderr={self.stderr!r:.60})"
        )


class PawBenchEnvShim:
    """Duck-typed adapter wrapping a PawBench DockerEnvironment.

    This class satisfies the *structural* interface that Harbor's
    ``BaseInstalledAgent`` agents call on their environment object:

    * ``async exec(command, *, cwd, env, timeout_sec, user) -> ExecResult``
    * ``async upload_file(source_path, target_path)``
    * ``async upload_dir(source_dir, target_dir)``
    * ``default_user`` attribute (``None`` → run as root)
    * ``task_os`` property (always ``"linux"``)
    * ``logger`` attribute

    It does **not** inherit from Harbor's ``BaseEnvironment`` — that base class
    has a complex constructor that requires Harbor-internal objects.  Duck typing
    is sufficient because agents only call the above interface.
    """

    #: All PawBench benchmark containers run as root and do not restrict users.
    default_user: str | int | None = None

    #: Default workspace path inside every PawBench container.
    #: Harbor agents that don't set an explicit cwd will run commands here,
    #: which ensures they can find task input files without extra cd logic.
    WORKSPACE_DIR: str = "/app/working/workspaces/default"

    def __init__(
        self,
        docker_env: "DockerEnvironment",
        logger: logging.Logger | None = None,
    ) -> None:
        self._docker_env = docker_env
        self.logger = (logger or _logger).getChild("harbor_shim")

    # ── core property ─────────────────────────────────────────────────────────

    @property
    def task_os(self) -> str:
        """Return the OS string used by Harbor agents for path decisions."""
        return "linux"

    @property
    def workspace_dir(self) -> str:
        """Path to the workspace inside the container (harbour interface)."""
        return self.WORKSPACE_DIR

    # ── exec ─────────────────────────────────────────────────────────────────

    async def exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        """Execute *command* inside the PawBench Docker container.

        Translates to::

            docker exec [-u USER] [-w CWD] [-e K=V …] <container> bash -c <command>
        """
        container = self._docker_env.name
        cmd: list[str] = ["docker", "exec"]

        if user is not None:
            cmd += ["-u", str(user)]

        # Default to PawBench workspace so Harbor agents find task input files.
        effective_cwd = cwd if cwd is not None else self.WORKSPACE_DIR
        cmd += ["-w", effective_cwd]

        if env:
            for k, v in env.items():
                cmd += ["-e", f"{k}={v}"]

        cmd += [container, "bash", "-c", command]

        self.logger.debug("exec: %s", command[:200])

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            timeout = timeout_sec or 3600
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    process.communicate(), timeout=float(timeout)
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ExecResult(
                    return_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout}s",
                )

            return ExecResult(
                return_code=process.returncode or 0,
                stdout=stdout_b.decode(errors="replace") if stdout_b else "",
                stderr=stderr_b.decode(errors="replace") if stderr_b else "",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error("exec error: %s", exc)
            return ExecResult(return_code=-1, stdout="", stderr=str(exc))

    # ── file transfer ─────────────────────────────────────────────────────────

    async def upload_file(
        self,
        source_path: str | Path,
        target_path: str,
    ) -> None:
        """Copy *source_path* from the host into the container at *target_path*."""
        container = self._docker_env.name
        cmd = ["docker", "cp", str(source_path), f"{container}:{target_path}"]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"upload_file failed: {result.stderr.decode(errors='replace')}"
            )

    async def upload_dir(
        self,
        source_dir: str | Path,
        target_dir: str,
    ) -> None:
        """Copy *source_dir* from the host into the container at *target_dir*."""
        container = self._docker_env.name
        # docker cp copies the directory's *contents* when trailing slash is used.
        cmd = ["docker", "cp", f"{str(source_dir)}/.", f"{container}:{target_dir}"]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"upload_dir failed: {result.stderr.decode(errors='replace')}"
            )

    # ── convenience methods called by some Harbor agents ─────────────────────

    async def download_file(
        self,
        container_path: str,
        host_path: str | Path,
    ) -> None:
        """Copy *container_path* from the container to *host_path*."""
        container = self._docker_env.name
        cmd = ["docker", "cp", f"{container}:{container_path}", str(host_path)]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"download_file failed: {result.stderr.decode(errors='replace')}"
            )

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"PawBenchEnvShim(container={self._docker_env.name!r})"
