# -*- coding: utf-8 -*-
"""AgentFactory — instantiate Harbor-bridge agents from an ``agent_config``.

PawBench runs **every** agent through the Harbor bridge
(:class:`~pawbench.agents.impl.harbor_bridge_agent.HarborBridgeAgent`), which
wraps any ``harbor.agents.installed.*`` ``BaseInstalledAgent`` and runs it
inside the ``pawbench-base`` Docker image (Python 3.12 + harbor-framework).

Agent selection uses the short Harbor agent name, with an optional ``harbor:``
prefix (kept for backwards compatibility)::

    --agents qwenpaw          # == harbor:qwenpaw (default)
    --agents harbor:hermes
    --agents harbor:codex
    --agents harbor:aider
    ...

See :mod:`pawbench.agents.impl.harbor_bridge_agent` for the full list of
supported agents.
"""

from __future__ import annotations

import os
from typing import Any

from pawbench.agents.constants import PAWBENCH_BASE_IMAGE

_HARBOR_PREFIX = "harbor:"

# Default agent used when ``--agents`` / ``agent_config["agent_type"]`` is unset.
DEFAULT_AGENT_TYPE = "harbor:qwenpaw"


def _strip_prefix(agent_type: str) -> str:
    """Return the bare Harbor agent name (``harbor:`` prefix stripped if present)."""
    if agent_type.startswith(_HARBOR_PREFIX):
        return agent_type[len(_HARBOR_PREFIX):]
    return agent_type


class AgentFactory:
    """Instantiate Harbor-bridge agents.

    Every ``agent_type`` — with or without the ``harbor:`` prefix — is routed to
    :class:`~pawbench.agents.impl.harbor_bridge_agent.HarborBridgeAgent` and run
    using ``PAWBENCH_BASE_IMAGE``.  There are no built-in (non-bridged) agents.
    """

    @classmethod
    def create(cls, agent_config: dict[str, Any]) -> Any:
        """Instantiate the Harbor-bridge agent for *agent_config['agent_type']*."""
        agent_type = agent_config.get("agent_type", DEFAULT_AGENT_TYPE)
        return cls._create_harbor_agent(agent_type, agent_config)

    @classmethod
    def _create_harbor_agent(
        cls,
        agent_type: str,
        agent_config: dict[str, Any],
    ) -> Any:
        """Create a :class:`~pawbench.agents.impl.harbor_bridge_agent.HarborBridgeAgent`."""
        from pawbench.agents.impl.harbor_bridge_agent import HarborBridgeAgent

        harbor_agent_name = _strip_prefix(agent_type)
        return HarborBridgeAgent(
            harbor_agent_name=harbor_agent_name,
            model=agent_config["model"],
            api_key=agent_config.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
            base_url=agent_config.get("base_url") or os.environ.get(
                "OPENAI_BASE_URL", ""
            ),
            version=agent_config.get("version"),
            thinking_level=agent_config.get("thinking_level"),
            multi_agent=agent_config.get("multi_agent"),
        )

    @classmethod
    def default_image_for_type(cls, agent_type: str) -> str:
        """Return the default Docker image name for *agent_type*.

        All agents run through the Harbor bridge, so this is always the
        ``pawbench-base`` image.
        """
        return PAWBENCH_BASE_IMAGE

    @classmethod
    def known_agent_types(cls) -> list[str]:
        """Return all supported Harbor agent names (no ``harbor:`` prefix)."""
        from pawbench.agents.impl.harbor_bridge_agent import HARBOR_AGENT_REGISTRY

        return sorted(HARBOR_AGENT_REGISTRY)
