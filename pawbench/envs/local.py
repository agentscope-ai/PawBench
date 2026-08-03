# -*- coding: utf-8 -*-
"""Local (in-process) environment for pawbench.

When ``PAWBENCH_ENV=local`` is set, this environment replaces
``DockerEnvironment`` so that agent commands run directly in the
current process via ``subprocess`` — no Docker spawn required.

This is used in two scenarios:
1. Inside an Agent-Platform Pod: the Pod container is already the
   isolated environment; spawning a child container is unnecessary.
2. Local development with ``PAWBENCH_ENV=local``: run without Docker
   to iterate faster (requires the agent CLI to be installed locally).

Path mapping
------------
Container-style absolute paths like ``/app/working/workspaces/default/output``
are remapped to ``<workspace_root>/output`` so all workspace file operations
land in a single temp directory that can be inspected after the run.

The constant ``CONTAINER_WORKSPACE_BASE`` must match the path used in
``DockerEnvironment`` and the agents' setup/run scripts.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from pawbench.envs.base import BaseEnvironment

# Must match the path pre-created in all three Dockerfiles and used
# by every agent inside the container.
CONTAINER_WORKSPACE_BASE = "/app/working/workspaces/default"

# Other container-internal paths that need remapping on the host.
_CONTAINER_SECRET_BASE = "/app/working.secret"
_CONTAINER_WORKING_BASE = "/app/working"

# True when we are already *inside* a benchmark container (AP cluster pod).
# In that case every path is a real container path — no remapping is needed.
# Detection: Dockerfiles set COPAW_RUNNING_IN_CONTAINER=1, and Docker also
# creates /.dockerenv in every container.
_IN_CONTAINER: bool = bool(
    os.environ.get("COPAW_RUNNING_IN_CONTAINER")
    or os.path.exists("/.dockerenv")
)


class LocalEnvironment(BaseEnvironment):
    """Execute agent commands in the current process environment.

    Args:
        name:              Logical name (used for logging).
        workspace_root:    Directory that acts as the agent workspace.
                           Defaults to a fresh temp directory created in
                           ``start()``.  Pass an explicit path when you
                           want to reuse a pre-populated workspace.
        environment_vars:  Extra env-vars injected into every subprocess.
        command_timeout:   Default timeout (seconds) for each shell command.
    """

    def __init__(
        self,
        name: str,
        workspace_root: Optional[Path | str] = None,
        environment_vars: Optional[Dict[str, str]] = None,
        command_timeout: int = 600,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self._workspace_root_arg: Optional[Path] = (
            Path(workspace_root) if workspace_root else None
        )
        self.workspace_root: Path  # set in start()
        self.environment_vars: Dict[str, str] = environment_vars or {}
        self.command_timeout = command_timeout
        self._is_running = False
        self._owns_workspace = False  # True if we created the temp dir

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if _IN_CONTAINER:
            # Running inside the benchmark container on an AP cluster pod.
            # All paths are real container paths, so use the canonical workspace
            # directory directly — no temp-dir indirection required.
            self.workspace_root = (
                self._workspace_root_arg
                if self._workspace_root_arg is not None
                else Path(CONTAINER_WORKSPACE_BASE)
            )
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            for sub in ("output", "sessions"):
                (self.workspace_root / sub).mkdir(parents=True, exist_ok=True)
            Path(_CONTAINER_SECRET_BASE).mkdir(parents=True, exist_ok=True)
            self._is_running = True
            return

        if self._workspace_root_arg is not None:
            self.workspace_root = self._workspace_root_arg
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            self._owns_workspace = False
        else:
            self.workspace_root = Path(
                tempfile.mkdtemp(prefix=f"pawbench_local_{self.name}_")
            )
            self._owns_workspace = True

        # Pre-create the standard subdirectories that agents expect.
        for sub in ("output", "sessions"):
            (self.workspace_root / sub).mkdir(parents=True, exist_ok=True)

        # Pre-create secret / working sidecars so commands referencing them work.
        self._secret_root.mkdir(parents=True, exist_ok=True)

        self._is_running = True

    @property
    def _secret_root(self) -> Path:
        """Local equivalent of /app/working.secret."""
        return self.workspace_root.parent / (self.workspace_root.name + "_secret")

    @property
    def _working_root(self) -> Path:
        """Local equivalent of /app/working (parent of the workspace tree)."""
        return self.workspace_root.parent / (self.workspace_root.name + "_working")

    async def stop(self) -> None:
        """No container to stop.  Optionally clean up the temp workspace."""
        self._is_running = False
        # Temp workspaces are NOT deleted here — the backend needs to read
        # them after stop() for grading.  The backend handles cleanup via
        # PAWBENCH_KEEP_WORKSPACE / shutil.rmtree after grading.

    # ── path remapping ────────────────────────────────────────────────────────

    def _remap_command(self, command: str) -> str:
        """Replace container-absolute paths with local workspace equivalents.

        When running *inside* a benchmark container (``_IN_CONTAINER=True``),
        all paths are real container paths — no substitution is needed.

        Order matters: most-specific prefixes must be substituted first.
        """
        if _IN_CONTAINER:
            return command
        ws = str(self.workspace_root)
        # /app/working/workspaces/default  →  workspace_root
        command = command.replace(CONTAINER_WORKSPACE_BASE, ws)
        # /app/working.secret  →  workspace_root_secret
        command = command.replace(_CONTAINER_SECRET_BASE, str(self._secret_root))
        # /app/working  →  workspace_root_working
        command = command.replace(_CONTAINER_WORKING_BASE, str(self._working_root))
        return command

    # ── command execution ─────────────────────────────────────────────────────

    async def execute_command(
        self, command: str, timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """Run *command* in a subprocess, cwd = workspace_root.

        Container-absolute paths (``/app/working/...``) are transparently
        rewritten to the corresponding local paths before execution.
        """
        command = self._remap_command(command)
        wait_timeout = timeout if timeout is not None else self.command_timeout
        env = {**os.environ, **self.environment_vars}
        # Set DISPLAY if not already set (for GUI/screenshot tasks on Linux).
        env.setdefault("DISPLAY", ":99")

        try:
            # Run via bash (not the default /bin/sh → dash on Debian) so that
            # bash-only constructs work, most importantly sourcing nvm
            # (``. ~/.nvm/nvm.sh``) which Harbor agents like codex use to put
            # their nvm-installed CLI on PATH.  This mirrors the DockerEnvironment
            # path which execs ``bash -c <command>``.
            bash_bin = shutil.which("bash") or "/bin/bash"
            proc = await asyncio.create_subprocess_exec(
                bash_bin,
                "-c",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace_root),
                env=env,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=wait_timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise TimeoutError(
                    f"Command timed out after {wait_timeout}s: {command[:120]}"
                )

            return {
                "stdout": stdout_b.decode(errors="replace"),
                "stderr": stderr_b.decode(errors="replace"),
                "returncode": proc.returncode,
                "success": proc.returncode == 0,
            }
        except TimeoutError:
            raise
        except Exception as exc:
            return {
                "stdout": "",
                "stderr": str(exc),
                "returncode": -1,
                "success": False,
            }

    # ── file operations ───────────────────────────────────────────────────────

    def _resolve(self, container_path: str) -> Path:
        """Map a container-style absolute path to the local workspace.

        When running *inside* a benchmark container (``_IN_CONTAINER=True``),
        every path is already a real filesystem path — return it unchanged.

        Mapping table for local-dev mode (most-specific prefix checked first):
          /app/working/workspaces/default/…  →  workspace_root/…
          /app/working.secret/…              →  workspace_root_secret/…
          /app/working/…                     →  workspace_root_working/…
          other absolute path                →  Path(container_path)  (real OS path)
          relative path                      →  workspace_root/<path>
        """
        if _IN_CONTAINER:
            return Path(container_path)
        p = container_path
        if p.startswith(CONTAINER_WORKSPACE_BASE):
            rel = p[len(CONTAINER_WORKSPACE_BASE):].lstrip("/")
            return self.workspace_root / rel if rel else self.workspace_root
        if p.startswith(_CONTAINER_SECRET_BASE):
            rel = p[len(_CONTAINER_SECRET_BASE):].lstrip("/")
            return self._secret_root / rel if rel else self._secret_root
        if p.startswith(_CONTAINER_WORKING_BASE):
            rel = p[len(_CONTAINER_WORKING_BASE):].lstrip("/")
            return self._working_root / rel if rel else self._working_root
        # For other absolute paths (e.g. /tmp/..., /usr/local/...) return as-is.
        # Only relative paths fall into the workspace.
        if p.startswith("/"):
            return Path(p)
        return self.workspace_root / p

    async def copy_to(self, source: Path, destination: str) -> bool:
        """Copy a host-side file into the (local) workspace."""
        dest_path = self._resolve(destination)
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(dest_path))
            return True
        except Exception:
            return False

    async def copy_from(self, source: str, destination: Path) -> bool:
        """Copy a file from the (local) workspace to a host path."""
        src_path = self._resolve(source)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_path), str(destination))
            return True
        except Exception:
            return False

    async def write_file(self, path: str, content: str) -> bool:
        """Write *content* to a file at the mapped local path."""
        dest = self._resolve(path)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            return True
        except Exception:
            return False

    async def read_file(self, path: str) -> Optional[str]:
        """Read file content from the mapped local path."""
        src = self._resolve(path)
        try:
            return src.read_text(encoding="utf-8")
        except Exception:
            return None

    # ── workspace collection ──────────────────────────────────────────────────

    def collect_workspace(self, dest: Path) -> None:
        """Copy entire workspace_root into *dest* (equivalent to docker cp).

        Called by the backend instead of ``docker cp`` when PAWBENCH_ENV=local.

        Mirrors the two-step collection that DockerEnvironment performs:
        1. Primary: full workspace tree  (= docker cp .../default/.)
        2. Secondary: flatten output/ to dest root  (= docker cp .../default/output/.)
           so graders that look at the workspace root can find output files directly.
        """
        dest.mkdir(parents=True, exist_ok=True)

        # Diagnostic: list workspace_root contents before copying
        ws_files = sorted(self.workspace_root.rglob("*")) if self.workspace_root.is_dir() else []
        ws_regular = [f for f in ws_files if f.is_file()]
        print(f"[collect_workspace] workspace_root={self.workspace_root}  "
              f"files={len(ws_regular)}  "
              f"sample={[str(f.relative_to(self.workspace_root)) for f in ws_regular[:8]]}",
              flush=True)

        # Step 1: full workspace tree
        copy_err: "shutil.Error | None" = None
        try:
            shutil.copytree(str(self.workspace_root), str(dest), dirs_exist_ok=True)
        except shutil.Error as exc:
            copy_err = exc
            # shutil.copytree copies all regular files before raising shutil.Error
            # for files that can't be copied (e.g. Unix socket files created by
            # Chromium in browser/user_data/).  All regular files are already in
            # *dest* at this point, so the error is non-fatal — continue to grading.
            print(f"[collect_workspace] shutil.Error (non-fatal): {exc}", flush=True)

        # Diagnostic: list dest after step 1
        dest_files = [f for f in dest.rglob("*") if f.is_file()]
        print(f"[collect_workspace] after step1  dest files={len(dest_files)}  "
              f"sample={[str(f.relative_to(dest)) for f in dest_files[:8]]}",
              flush=True)

        # Step 2: flatten output/ → dest root (mirrors DockerEnvironment secondary cp)
        output_dir = self.workspace_root / "output"
        if output_dir.is_dir():
            try:
                shutil.copytree(str(output_dir), str(dest), dirs_exist_ok=True)
            except shutil.Error:
                pass

    # ── properties ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._is_running
