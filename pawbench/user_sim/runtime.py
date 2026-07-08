# -*- coding: utf-8 -*-
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

logger = logging.getLogger("pawbench.user_sim")

__all__ = ["UserSimRuntime", "default_state_path", "default_task_dir", "default_max_turns"]


def default_state_path() -> Path:
    return Path(os.environ.get("USER_SIM_STATE_PATH", "/logs/agent/user_sim_state.json"))


def default_task_dir() -> Path:
    return Path(os.environ.get("USER_SIM_TASK_DIR", "/app/task"))


def default_max_turns() -> int:
    return int(os.environ.get("USER_SIM_MAX_TURNS", "20") or "20")


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
    ) -> None:
        self.task_dir = Path(task_dir)
        self.max_turns = max(1, max_turns if max_turns is not None else default_max_turns())
        self.state_path = Path(state_path) if state_path is not None else default_state_path()

        self.turn = 0
        self.started = False
        self.termination_reason: str | None = None
        self.transcript: list[dict[str, str]] = []

        if agent is not None:
            self.agent = agent
        else:
            context = load_user_context(self.task_dir)
            self.agent = UserAgent(
                context=context,
                temperature=(
                    temperature if temperature is not None else _resolve_temperature()
                ),
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
            "done": self.agent.done,
            "termination_reason": self.termination_reason,
            "transcript": self.transcript,
        }

    def _record(self, source: str, text: str) -> None:
        self.transcript.append({"source": source, "text": text})

    def _terminate(self, reason: str) -> None:
        if self.termination_reason is None:
            self.termination_reason = reason

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
            return self.transcript[0]["text"] if self.transcript else ""
        self.started = True
        opening = await self.agent.opening()
        self._record("user", opening)
        if self.agent.done:
            self._terminate("user_done")
        self._write_state()
        return opening

    async def send_message_to_user(self, message: str) -> str:
        if not self.started:
            raise RuntimeError("Call start_conversation before messaging the user.")
        if self.termination_reason is not None:
            return json.dumps(
                self._status_payload(), ensure_ascii=False, sort_keys=True
            )
        if not message or not message.strip():
            raise ValueError("message must not be empty.")

        self._record("agent", message)
        self.turn += 1

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
        self._terminate("agent_ended")
        self._write_state()
        return "Conversation ended."

    def get_conversation_status(self) -> str:
        return json.dumps(self._status_payload(), ensure_ascii=False, sort_keys=True)

    def get_transcript(self) -> str:
        return json.dumps(self.transcript, ensure_ascii=False)
