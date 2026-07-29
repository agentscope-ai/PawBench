"""Conversation runtime for the user simulator.

This module deliberately has **no** dependency on ``fastmcp`` so it can be unit
tested on the host (with an injected fake LLM / agent). :mod:`mcp_server` wraps
this runtime with MCP tools inside the sidecar container.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from . import defaults
from .context import load_user_context
from .user_agent import UserAgent
from .workspace_patch import WorkspacePatchApplier, find_patch_dir

logger = logging.getLogger("pawbench.user_sim")

__all__ = [
    "UserSimRuntime",
    "default_state_path",
    "default_task_dir",
    "default_max_turns",
    "default_workspace_root",
]


def default_state_path() -> Path:
    return Path(os.environ.get("USER_SIM_STATE_PATH", "/logs/agent/user_sim_state.json"))


def default_task_dir() -> Path:
    return Path(os.environ.get("USER_SIM_TASK_DIR", "/app/task"))


def default_max_turns() -> int:
    return int(os.environ.get("USER_SIM_MAX_TURNS", "20") or "20")


def default_workspace_root() -> Path:
    return Path(os.environ.get("USER_SIM_WORKSPACE_ROOT", "/workspace"))


def load_authored_user_turns(task_dir: Path) -> list[str]:
    """Return the authored user turns from ``<task_dir>/messages.jsonl``.

    These ground the generative user simulator so a task's concrete deliverables
    (exact file paths / field names / formats — authored only in
    ``messages.jsonl``) are always conveyed, while the persona still controls
    voice and reactivity. Returns ``[]`` when the file is absent or malformed,
    in which case the simulator falls back to a persona-only conversation.
    """
    path = Path(task_dir) / "messages.jsonl"
    if not path.is_file():
        return []
    turns: list[str] = []
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            item = json.loads(raw_line)
            if (
                isinstance(item, dict)
                and item.get("role") == "user"
                and isinstance(item.get("content"), str)
                and item["content"].strip()
            ):
                turns.append(item["content"])
    except (OSError, json.JSONDecodeError):
        return []
    return turns


def _resolve_temperature() -> float:
    raw = os.environ.get("USER_SIM_TEMPERATURE", "").strip()
    if not raw:
        return defaults.DEFAULT_TEMPERATURE
    try:
        return float(raw)
    except ValueError:
        return defaults.DEFAULT_TEMPERATURE


class UserSimRuntime:
    """Holds one :class:`UserAgent` conversation plus lifecycle / transcript state."""

    def __init__(
        self,
        task_dir: Path | str,
        *,
        max_turns: int | None = None,
        agent: UserAgent | None = None,
        temperature: float | None = None,
        state_path: Path | str | None = None,
        workspace_root: Path | str | None = None,
    ) -> None:
        self.task_dir = Path(task_dir)
        self.max_turns = max(1, max_turns if max_turns is not None else default_max_turns())
        self.state_path = Path(state_path) if state_path is not None else default_state_path()

        self.turn = 0
        self.started = False
        self.termination_reason: str | None = None
        self.transcript: list[dict[str, str]] = []
        self.workspace_events: list[dict[str, object]] = []
        self.patch_applier: WorkspacePatchApplier | None = None
        if find_patch_dir(self.task_dir) is not None:
            self.patch_applier = WorkspacePatchApplier(
                self.task_dir,
                workspace_root or default_workspace_root(),
            )

        if agent is not None:
            self.agent = agent
        else:
            context = load_user_context(self.task_dir)
            self.agent = UserAgent(
                context=context,
                temperature=(temperature if temperature is not None else _resolve_temperature()),
                authored_turns=load_authored_user_turns(self.task_dir),
            )
        self._write_state()

    # -- persistence --------------------------------------------------

    def _write_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.state_payload(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - best-effort persistence
            logger.warning("failed to persist user-sim state: %s", exc)

    def state_payload(self) -> dict[str, object]:
        return {
            "task_dir": str(self.task_dir),
            "started": self.started,
            "turn": self.turn,
            "max_turns": self.max_turns,
            # ``agent.done`` is an internal persona signal.  The externally
            # visible protocol is complete only after the runtime records an
            # explicit termination reason.
            "done": self.termination_reason is not None,
            "termination_reason": self.termination_reason,
            "transcript": self.transcript,
            "workspace_events": self.workspace_events,
        }

    def _record(self, source: str, text: str) -> None:
        self.transcript.append({"source": source, "text": text})

    def _terminate(self, reason: str) -> None:
        if self.termination_reason is None:
            self.termination_reason = reason

    def _apply_workspace_patch(self, authored_turn: int) -> None:
        if self.patch_applier is None:
            return
        events = self.patch_applier.apply_turn(authored_turn)
        for event in events:
            self.workspace_events.append({"turn": authored_turn, **event})

    def _status_payload(self, *, user_message: str | None = None) -> dict[str, object]:
        return {
            "user_message": user_message,
            "conversation_over": self.termination_reason is not None,
            "termination_reason": self.termination_reason,
            "turn": self.turn,
            "max_turns": self.max_turns,
        }

    # -- lifecycle ----------------------------------------------------

    async def start_conversation(self) -> str:
        if self.started:
            opening = self.transcript[0]["text"] if self.transcript else None
            return json.dumps(
                self._status_payload(user_message=opening),
                ensure_ascii=False,
                sort_keys=True,
            )
        self.started = True
        # The opening turn may declare initial user-side workspace changes.
        self._apply_workspace_patch(1)
        opening = await self.agent.opening()
        self._record("user", opening)
        self._write_state()
        return json.dumps(
            self._status_payload(user_message=opening),
            ensure_ascii=False,
            sort_keys=True,
        )

    async def send_message_to_user(self, message: str) -> str:
        if not self.started:
            raise RuntimeError("Call start_conversation before messaging the user.")
        if self.termination_reason is not None:
            return json.dumps(self._status_payload(), ensure_ascii=False, sort_keys=True)
        if not message or not message.strip():
            raise ValueError("message must not be empty.")

        self._record("agent", message)
        self.turn += 1

        # A malformed/misconfigured persona can emit its done marker in the
        # opening.  Still require one delivered assistant turn, then close
        # cleanly without asking an already-finished persona for another reply.
        if self.agent.done:
            self._terminate("user_done")
            self._write_state()
            return json.dumps(self._status_payload(), ensure_ascii=False, sort_keys=True)

        # self.turn counts assistant replies.  After reply 1, the simulator is
        # about to emit authored user turn 2, whose filesystem changes must be
        # visible before that message is returned to the agent.
        self._apply_workspace_patch(self.turn + 1)
        reply = await self.agent.respond_or_approve(message)
        self._record("user", reply)

        if self.agent.done:
            self._terminate("user_done")
        elif self.turn >= self.max_turns:
            self._terminate("max_turns")

        self._write_state()
        return json.dumps(
            self._status_payload(user_message=reply), ensure_ascii=False, sort_keys=True
        )

    def end_conversation(self) -> str:
        if not any(turn.get("source") == "agent" for turn in self.transcript):
            return json.dumps(
                {
                    **self._status_payload(),
                    "error": (
                        "Cannot end the conversation before delivering at least "
                        "one send_message_to_user response."
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        self._terminate("agent_ended")
        self._write_state()
        return json.dumps(self._status_payload(), ensure_ascii=False, sort_keys=True)

    def get_conversation_status(self) -> str:
        return json.dumps(self._status_payload(), ensure_ascii=False, sort_keys=True)

    def get_transcript(self) -> str:
        return json.dumps(self.transcript, ensure_ascii=False)
