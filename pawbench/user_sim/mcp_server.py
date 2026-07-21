# -*- coding: utf-8 -*-
"""FastMCP sidecar exposing a simulated user over MCP tools.

Runs *inside a Harbor environment sidecar container* (Strategy A of
docs/multi-turn-user-agent-integration.md). The Harbor agent-under-test talks
to the simulated user through MCP tools:

* ``start_conversation()`` — begin the dialogue; returns the user's opening
  message.
* ``send_message_to_user(message)`` — send one assistant message; returns a
  JSON string with the user's reply plus ``conversation_over`` / ``turn``.
* ``end_conversation()`` — end the dialogue early.
* ``get_conversation_status()`` / ``get_transcript()`` — read-only inspection.

The full transcript is also persisted to ``USER_SIM_STATE_PATH`` so grading can
reconstruct the multi-turn conversation.

Configuration comes entirely from the dedicated ``USER_SIM_*`` environment
contract (no fallback to agent / judge credentials); see
:mod:`pawbench.user_sim.defaults` and :mod:`pawbench.user_sim.runtime`.
"""

from __future__ import annotations

import logging
import os

from fastmcp import FastMCP

from .runtime import UserSimRuntime, default_max_turns, default_task_dir

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pawbench.user_sim")

mcp = FastMCP("pawbench-user-sim")

runtime = UserSimRuntime(default_task_dir(), max_turns=default_max_turns())


@mcp.tool()
async def start_conversation() -> str:
    """Required first task action; returns the user's authoritative opening request.

    Do not inspect or edit the workspace before calling this tool. After handling
    the returned request, reply through ``send_message_to_user`` rather than a
    normal final response.
    """
    return await runtime.start_conversation()


@mcp.tool()
async def send_message_to_user(message: str) -> str:
    """The only channel that delivers an assistant response to the user.

    Returns a JSON string: ``user_message`` is the user's reply,
    ``conversation_over`` is true once the user is satisfied (or limits are hit),
    and ``turn`` / ``max_turns`` report progress. If ``conversation_over`` is
    false, continue the task from ``user_message`` and call this tool again after
    the next turn. Do not finish the run until it returns true.
    """
    return await runtime.send_message_to_user(message)


@mcp.tool()
def end_conversation() -> str:
    """End the conversation early once the user's need is resolved."""
    return runtime.end_conversation()


@mcp.tool()
def get_conversation_status() -> str:
    """Return JSON with turn count, limits, and whether the conversation is over."""
    return runtime.get_conversation_status()


@mcp.tool()
def get_transcript() -> str:
    """Return the full user/agent transcript as a JSON array (for verification)."""
    return runtime.get_transcript()


def main() -> None:
    host = os.environ.get("USER_SIM_HOST", "0.0.0.0")
    port = int(os.environ.get("USER_SIM_PORT", "8000") or "8000")
    logger.info("starting pawbench user-sim MCP server on %s:%s", host, port)
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
