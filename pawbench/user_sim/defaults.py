# -*- coding: utf-8 -*-
"""User-sim defaults and provider resolution.

The user simulator uses a *dedicated* provider (``USER_SIM_*``) so that the
simulated-user LLM is never confused with the agent-under-test or the judge
(see docs/multi-turn-user-agent-integration.md §6). There is intentionally
**no** fallback to the agent / judge credentials — a missing key is an error
the caller must surface as fail-fast.
"""

from __future__ import annotations

import os

# LLM behaviour knobs.
DEFAULT_LLM_RETRIES = 3
DEFAULT_LLM_TIMEOUT = 120.0
DEFAULT_TEMPERATURE = 0.7

# Provider defaults, read from the dedicated USER_SIM_* environment contract.
DEFAULT_USER_MODEL = os.environ.get("USER_SIM_MODEL", "")
DEFAULT_BASE_URL = os.environ.get(
    "USER_SIM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)


def resolve_api_key(api_key: str | None) -> str:
    """Resolve the user-sim API key.

    Order: explicit arg → ``USER_SIM_API_KEY``. No fallback to agent/judge keys.
    """
    return (api_key or os.environ.get("USER_SIM_API_KEY", "") or "").strip()


def resolve_model(model: str | None) -> str:
    return (model or DEFAULT_USER_MODEL or "").strip()


def resolve_base_url(base_url: str | None) -> str:
    return (base_url or DEFAULT_BASE_URL or "").strip()
