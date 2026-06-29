# -*- coding: utf-8 -*-
"""Base agent interface for pawbench."""

import abc
from pathlib import Path
from typing import Any, Dict, List, Optional

from pawbench.envs.base import BaseEnvironment


class BaseAgent(abc.ABC):
    """Abstract base for all benchmark agents."""

    def __init__(self, name: str, **kwargs: Any):
        self.name = name
        self.config = kwargs

    @abc.abstractmethod
    async def setup(self, environment: BaseEnvironment) -> None: ...

    @abc.abstractmethod
    async def run(self, instruction: str, environment: BaseEnvironment) -> Dict[str, Any]: ...

    @abc.abstractmethod
    async def teardown(self, environment: BaseEnvironment) -> None: ...

    @property
    @abc.abstractmethod
    def version(self) -> Optional[str]: ...


class ContainerAgent(BaseAgent):
    """Base for agents that need installation inside the environment."""

    async def setup(self, environment: BaseEnvironment) -> None:
        await self.install(environment)

    async def install(self, environment: BaseEnvironment) -> None:
        raise NotImplementedError

    async def run(self, instruction: str, environment: BaseEnvironment) -> Dict[str, Any]:
        raise NotImplementedError

    async def teardown(self, environment: BaseEnvironment) -> None:
        pass

    async def post_run_collect(self, environment: BaseEnvironment) -> None:
        """Hook called by the backend after run() completes.

        Override in subclasses to sync agent-internal directories into the
        standard workspace path before the backend does ``docker cp``.
        The default implementation is a no-op.
        """

    def get_system_prompt(self) -> Optional[str]:
        """Return the system prompt used for this agent's last run.

        Subclasses populate ``self._last_system_prompt`` inside
        ``post_run_collect()`` (while the container is still reachable) so
        that ``backend.py`` can prepend it to the transcript after the
        container stops.  The default implementation returns ``None``.
        """
        return getattr(self, "_last_system_prompt", None)

    def extract_transcript(
        self,
        local_workspace: "Path | None",
        stdout: str,
    ) -> "List[Dict[str, Any]]":
        """Build an OpenClaw-compatible transcript list from this agent's run.

        The default implementation delegates to the shared session-JSON parser
        in ``agents/transcript.py``, which handles the qwenpaw-memory format
        that all current agents normalise their sessions to during ``run()``.
        Subclasses may override to implement custom extraction logic.
        """
        from pawbench.agents.transcript import build_transcript_from_session
        return build_transcript_from_session(local_workspace, stdout)

    def _bench_wrap_instruction(self, instruction: str) -> str:
        """Optionally wrap the task instruction before passing to the agent CLI.

        The default implementation returns the instruction unchanged.  Subclasses
        may override to prepend workspace context, append grading hints, etc.
        """
        return instruction

    # ── shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    async def _sync_workspace_to_output(
        environment: BaseEnvironment,
        workspace: str,
    ) -> None:
        """Copy every non-bytecode file from *workspace* into its output/ subdir.

        The grader reads ``workspace/output/`` for files produced by the agent.
        Running this after the agent finishes ensures even files written to the
        workspace root are discoverable.
        """
        sync_cmd = (
            f"WORKSPACE={workspace}\n"
            f'DEST="$WORKSPACE/output"\n'
            f'mkdir -p "$DEST"\n'
            f'find "$WORKSPACE" -maxdepth 3 -type f '
            f"! -path '*/site-packages/*' ! -name '*.pyc' ! -path '*/output/*' "
            f"2>/dev/null | while read -r f; do\n"
            f'  bn=$(basename "$f")\n'
            f'  dest_file="$DEST/$bn"\n'
            f'  [ ! -s "$dest_file" ] && [ -s "$f" ] '
            f'&& cp "$f" "$dest_file" 2>/dev/null || true\n'
            f"done\n"
        )
        await environment.execute_command(sync_cmd, timeout=60)
