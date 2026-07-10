# -*- coding: utf-8 -*-
"""User agent for pawbench multi-turn tasks.

The dialogue + approval behaviour is reused verbatim from the CuES-plus
``UserAgent`` (imported via :mod:`pawbench.user_sim._cues`): ``opening``,
``respond_or_approve``, the approval decision and ``[DONE]`` detection are all
inherited from the upstream class.

pawbench only overrides *construction* for two reasons the upstream constructor
does not support:

* **Credential isolation (design doc §6).** The upstream ``UserAgent`` builds
  its LLM clients through ``src.defaults.resolve_api_key`` which falls back to
  ``DASHSCOPE_API_KEY`` / ``OPENAI_API_KEY``. The user simulator must use the
  dedicated ``USER_SIM_*`` credentials with **no** fallback to the
  agent-under-test / judge keys, so we wire the clients ourselves.
* **Test injection.** Offline tests inject fake LLM clients
  (``persona_llm`` / ``approval_llm`` / ``llm_factory``) so they run without a
  network or real key.
"""

from __future__ import annotations

from typing import Any, Callable

from . import defaults
from ._cues import UpstreamUserAgent, build_user_agent_system_prompt
from .context import UserContext
from .llm import LLMClient, LLMConfig

__all__ = ["UserAgent"]


class UserAgent(UpstreamUserAgent):
    """CuES ``UserAgent`` wired to pawbench's credential-isolated LLM client.

    All dialogue logic is inherited from the upstream class; this subclass only
    replaces ``__init__`` so the persona / approval LLM clients honour the
    dedicated ``USER_SIM_*`` contract (and can be faked in tests).
    """

    def __init__(
        self,
        *,
        context: UserContext,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = defaults.DEFAULT_TEMPERATURE,
        llm_factory: Callable[[LLMConfig], LLMClient] | None = None,
        persona_llm: LLMClient | None = None,
        approval_llm: LLMClient | None = None,
    ) -> None:
        # NB: intentionally NOT calling ``super().__init__`` — the upstream
        # constructor builds its own clients via ``src.defaults`` (which falls
        # back to shared agent/judge keys). We reproduce the minimal attribute
        # setup the inherited methods rely on, using isolated USER_SIM_* creds.
        self.context = context
        self.system_prompt = build_user_agent_system_prompt(context.as_dict())

        factory = llm_factory or LLMClient
        resolved_model = defaults.resolve_model(model)
        resolved_key = defaults.resolve_api_key(api_key)
        resolved_base = defaults.resolve_base_url(base_url)

        # persona dialogue LLM
        self._persona_llm = persona_llm or factory(
            LLMConfig(
                model=resolved_model,
                api_key=resolved_key,
                base_url=resolved_base,
                temperature=temperature,
            )
        )
        # approval decision LLM (deterministic)
        self._approval_llm = approval_llm or factory(
            LLMConfig(
                model=resolved_model,
                api_key=resolved_key,
                base_url=resolved_base,
                temperature=0.0,
            )
        )

        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
        ]
        self._done = False
