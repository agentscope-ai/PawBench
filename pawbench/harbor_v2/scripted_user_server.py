"""Standalone MCP server that replays authored user turns from JSONL."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP  # pyright: ignore[reportMissingImports]


MESSAGES_PATH = Path(os.environ.get("SCRIPTED_MESSAGES_PATH", "/app/task/messages.jsonl"))
STATE_PATH = Path(
    os.environ.get("USER_SIM_STATE_PATH", "/logs/agent/user_sim_state.json")
)


def _load_messages() -> list[str]:
    messages: list[str] = []
    for raw_line in MESSAGES_PATH.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        item = json.loads(raw_line)
        if item.get("role") == "user" and isinstance(item.get("content"), str):
            messages.append(item["content"])
    if not messages:
        raise RuntimeError(f"No authored user messages found in {MESSAGES_PATH}")
    return messages


class ScriptedConversation:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.started = False
        self.next_message_index = 0
        self.turn = 0
        self.termination_reason: str | None = None
        self.transcript: list[dict[str, str]] = []
        self._write_state()

    @property
    def conversation_over(self) -> bool:
        return self.termination_reason is not None

    def _write_state(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {
                    "task_dir": str(MESSAGES_PATH.parent),
                    "started": self.started,
                    "turn": self.turn,
                    "max_turns": len(self.messages),
                    "done": self.conversation_over,
                    "termination_reason": self.termination_reason,
                    "transcript": self.transcript,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def start(self) -> str:
        if self.started:
            return self.messages[0]
        self.started = True
        opening = self.messages[0]
        self.next_message_index = 1
        self.transcript.append({"source": "user", "text": opening})
        self._write_state()
        return opening

    def send(self, message: str) -> str:
        if not self.started:
            raise RuntimeError("Call start_conversation before messaging the user.")
        if not message or not message.strip():
            raise ValueError("message must not be empty.")
        if self.conversation_over:
            return json.dumps(self._status(), ensure_ascii=False, sort_keys=True)

        self.transcript.append({"source": "agent", "text": message})
        self.turn += 1

        user_message: str | None = None
        if self.next_message_index < len(self.messages):
            user_message = self.messages[self.next_message_index]
            self.next_message_index += 1
            self.transcript.append({"source": "user", "text": user_message})
        else:
            self.termination_reason = "script_complete"

        self._write_state()
        return json.dumps(
            self._status(user_message=user_message),
            ensure_ascii=False,
            sort_keys=True,
        )

    def end(self) -> str:
        if self.termination_reason is None:
            self.termination_reason = "agent_ended"
        self._write_state()
        return "Conversation ended."

    def _status(self, *, user_message: str | None = None) -> dict[str, Any]:
        return {
            "user_message": user_message,
            "conversation_over": self.conversation_over,
            "termination_reason": self.termination_reason,
            "turn": self.turn,
            "max_turns": len(self.messages),
        }


runtime = ScriptedConversation(_load_messages())
mcp = FastMCP("pawbench-scripted-user")


@mcp.tool()
def start_conversation() -> str:
    """Start the conversation and return the first authored user message."""
    return runtime.start()


@mcp.tool()
def send_message_to_user(message: str) -> str:
    """Record the agent response and return the next authored user message."""
    return runtime.send(message)


@mcp.tool()
def end_conversation() -> str:
    """End the conversation before the authored script is exhausted."""
    return runtime.end()


@mcp.tool()
def get_conversation_status() -> str:
    """Return the current conversation status as JSON."""
    return json.dumps(runtime._status(), ensure_ascii=False, sort_keys=True)


@mcp.tool()
def get_transcript() -> str:
    """Return the full alternating transcript as JSON."""
    return json.dumps(runtime.transcript, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
