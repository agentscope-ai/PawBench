# -*- coding: utf-8 -*-
"""User simulator subpackage for multi-turn PawBench / Harbor tasks.

Ported and slimmed from CuES-plus (commit
7f71d5cb3b8fba4f0ba90cee10d0b102f3afe2fc). Provides a persona-driven
:class:`UserAgent` plus a FastMCP sidecar server (see :mod:`mcp_server`) that
lets a Harbor agent-under-test talk to a simulated user through MCP tools.

The user simulator uses a dedicated ``USER_SIM_*`` credential set and never
falls back to the agent-under-test or judge credentials.
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
