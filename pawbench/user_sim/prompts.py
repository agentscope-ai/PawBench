# -*- coding: utf-8 -*-
"""User-agent prompt assembly.

Slimmed from CuES-plus ``src/runtime/prompts.py`` to only the two builders the
user simulator needs: the persona system prompt and the approval prompt.
"""

from __future__ import annotations

import json
from typing import Mapping

from . import defaults

__all__ = [
    "build_user_agent_system_prompt",
    "build_user_agent_approval_prompt",
]

_PLACEHOLDERS = (
    "persona",
    "profile",
    "long_term_memory",
    "domain_knowledge",
    "recent_focus",
    "preferences",
    "timeline",
    "prior_queries",
    "prior_interactions",
    "state_init",
    "latent_goals",
    "dialogue_policy",
    "world_model",
    "private_rules",
    "task_metadata",
)


def build_user_agent_system_prompt(user_context: Mapping[str, object | None]) -> str:
    """Render ``user_agent_system.md`` with ``.user/`` derived context.

    Missing keys render as ``"(无)"``; dict/list values are pretty-printed JSON.
    """
    template = (defaults.PROMPTS_DIR / "user_agent_system.md").read_text(encoding="utf-8")

    def _g(key: str) -> str:
        value = user_context.get(key)
        if value is None or value == "":
            return "(无)"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2, default=str)
        return str(value)

    for key in _PLACEHOLDERS:
        template = template.replace("{" + key + "}", _g(key))
    return template


def build_user_agent_approval_prompt() -> str:
    """Read the approval prompt template."""
    return (defaults.PROMPTS_DIR / "user_agent_approval.md").read_text(encoding="utf-8")
