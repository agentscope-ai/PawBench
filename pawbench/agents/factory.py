# -*- coding: utf-8 -*-
"""AgentFactory — registry mapping agent_type → (AgentClass, default_docker_image).

Adding a new built-in agent type requires:
1. Create a new :class:`ContainerAgent` subclass under ``impl/``.
2. Add one entry to :attr:`AgentFactory._REGISTRY` here.

Harbor agents (20+ agents) are handled transparently via the ``harbor:``
prefix — no per-agent entry is needed::

    --agents harbor:hermes
    --agents harbor:codex
    --agents harbor:aider
    ...

See :mod:`pawbench.agents.impl.harbor_bridge_agent` for the full list.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pawbench.agents.constants import (
    QWENPAW_DEFAULT_IMAGE,
    HERMES_DEFAULT_IMAGE,
    OPENCLAW_DEFAULT_IMAGE,
    PAWBENCH_BASE_IMAGE,
)

if TYPE_CHECKING:
    pass

_HARBOR_PREFIX = "harbor:"


class AgentFactory:
    """Registry mapping agent_type → (AgentClass, default_docker_image).

    Imports of concrete agent classes are deferred to :meth:`_populate` so
    that this module can be imported without triggering circular dependencies.

    Harbor agents
    ~~~~~~~~~~~~~
    Any ``agent_type`` starting with ``harbor:`` (e.g. ``harbor:hermes``) is
    routed to :class:`~pawbench.agents.impl.harbor_bridge_agent.HarborBridgeAgent`
    and run using ``PAWBENCH_BASE_IMAGE`` (Python 3.12 + harbor-framework).
    No registry entry is needed for individual Harbor agents.
    """

    # Populated lazily to avoid import-time circular dependencies.
    _REGISTRY: "dict[str, tuple[type, str]]" = {}

    @classmethod
    def _populate(cls) -> None:
        if cls._REGISTRY:
            return
        from pawbench.agents.impl.qwenpaw_agent import QwenPawAgent
        from pawbench.agents.impl.openclaw_agent import OpenClawAgent
        from pawbench.agents.impl.hermes_agent import HermesAgent
        cls._REGISTRY = {
            "qwenpaw":  (QwenPawAgent,   QWENPAW_DEFAULT_IMAGE),
            "openclaw": (OpenClawAgent,  OPENCLAW_DEFAULT_IMAGE),
            "hermes":   (HermesAgent,    HERMES_DEFAULT_IMAGE),
        }

    @classmethod
    def create(cls, agent_config: dict[str, Any]) -> Any:
        """Instantiate the agent class for *agent_config['agent_type']*."""
        cls._populate()
        agent_type = agent_config.get("agent_type", "qwenpaw")

        # ── Harbor-bridge agents ──────────────────────────────────────────────
        if agent_type.startswith(_HARBOR_PREFIX):
            return cls._create_harbor_agent(agent_type, agent_config)

        # ── Built-in agents ───────────────────────────────────────────────────
        entry = cls._REGISTRY.get(agent_type)
        if entry is None:
            raise ValueError(
                f"Unknown agent_type: {agent_type!r}. "
                f"Known built-in types: {list(cls._REGISTRY)}. "
                f"Harbor agents: use 'harbor:<name>' (e.g. harbor:hermes, harbor:codex)."
            )
        AgentCls, _ = entry
        return AgentCls(
            model=agent_config["model"],
            api_key=agent_config.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
            base_url=agent_config.get("base_url") or os.environ.get(
                "OPENAI_BASE_URL", "https://api.openai.com/v1"
            ),
            api_model_name=agent_config.get("api_model_name"),
        )

    @classmethod
    def _create_harbor_agent(
        cls,
        agent_type: str,
        agent_config: dict[str, Any],
    ) -> Any:
        """Create a :class:`~pawbench.agents.impl.harbor_bridge_agent.HarborBridgeAgent`."""
        from pawbench.agents.impl.harbor_bridge_agent import HarborBridgeAgent

        harbor_agent_name = agent_type[len(_HARBOR_PREFIX):]
        return HarborBridgeAgent(
            harbor_agent_name=harbor_agent_name,
            model=agent_config["model"],
            api_key=agent_config.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
            base_url=agent_config.get("base_url") or os.environ.get(
                "OPENAI_BASE_URL", ""
            ),
            version=agent_config.get("version"),
            thinking_level=agent_config.get("thinking_level"),
        )

    @classmethod
    def default_image_for_type(cls, agent_type: str) -> str:
        """Return the default Docker image name for *agent_type*."""
        if agent_type.startswith(_HARBOR_PREFIX):
            return PAWBENCH_BASE_IMAGE
        cls._populate()
        entry = cls._REGISTRY.get(agent_type)
        if entry is None:
            return QWENPAW_DEFAULT_IMAGE
        _, image = entry
        return image

    @classmethod
    def known_agent_types(cls) -> list[str]:
        """Return all registered built-in agent type names (no harbor: prefix)."""
        cls._populate()
        return list(cls._REGISTRY)
