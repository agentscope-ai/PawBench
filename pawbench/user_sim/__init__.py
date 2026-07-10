# -*- coding: utf-8 -*-
"""User simulator subpackage for multi-turn PawBench / Harbor tasks.

The persona-driven :class:`UserAgent`, ``UserContext`` and prompt builders are
imported **directly from CuES-plus** (``examples/CuES-plus/src``) via
:mod:`pawbench.user_sim._cues`, rather than maintaining a private fork. pawbench
keeps only the thin integration layer around it:

* :class:`UserAgent` — subclass that wires the upstream agent to the dedicated
  ``USER_SIM_*`` credentials (no fallback to agent/judge keys) and allows
  injecting fake LLM clients in tests.
* :mod:`runtime` + :mod:`mcp_server` — the FastMCP sidecar server that lets a
  Harbor agent-under-test talk to the simulated user through MCP tools.
"""

from __future__ import annotations

from .context import (
    APPROVAL_MARKERS,
    UserContext,
    is_approval_request,
    load_user_context,
)
from .llm import LLMClient, LLMConfig, make_chat_result
from .prompts import build_user_agent_approval_prompt, build_user_agent_system_prompt
from .user_agent import UserAgent

__all__ = [
    "APPROVAL_MARKERS",
    "LLMClient",
    "LLMConfig",
    "UserAgent",
    "UserContext",
    "build_user_agent_approval_prompt",
    "build_user_agent_system_prompt",
    "is_approval_request",
    "load_user_context",
    "make_chat_result",
]
