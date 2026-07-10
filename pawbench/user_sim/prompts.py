# -*- coding: utf-8 -*-
"""User-agent prompt builders — re-exported from the CuES-plus upstream.

The prompt templates and rendering logic live in CuES-plus
(``src/runtime/prompts.py`` + ``src/runtime/prompts/client/*.md``); pawbench
imports them via :mod:`pawbench.user_sim._cues` rather than bundling a private
copy of the templates.
"""

from __future__ import annotations

from ._cues import (
    build_user_agent_approval_prompt,
    build_user_agent_system_prompt,
)

__all__ = [
    "build_user_agent_approval_prompt",
    "build_user_agent_system_prompt",
]
