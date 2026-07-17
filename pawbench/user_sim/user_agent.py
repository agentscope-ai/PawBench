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
        authored_turns: list[str] | None = None,
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

        # ── Guided (hybrid) turns ────────────────────────────────────────────
        # A purely persona-driven user derives every turn from persona/latent
        # goals, so the *concrete* deliverables authored for a task (exact file
        # paths, field names, formats — which live only in ``messages.jsonl``)
        # can be dropped, making the task under-specified and breaking grading
        # even for a competent agent. When authored turns are supplied we keep
        # the persona *voice / reactivity* but ground each turn in the authored
        # requirement so those hard constraints are always conveyed.
        self._authored_turns: list[str] = [t for t in (authored_turns or []) if t.strip()]
        self._authored_idx = 0

    _DIRECTOR_TAG = "[对话导演提示 · 仅你可见]"

    async def _guided_reply(self, seed: str) -> str:
        self._messages.append({"role": "user", "content": seed})
        resp = await self._persona_llm.chat(self._messages)
        text = (resp.choices[0].message.content or "").strip()
        self._messages.append({"role": "assistant", "content": text})
        return text

    async def opening(self) -> str:
        """Persona-voiced opening grounded in the first authored turn.

        Falls back to the upstream persona-only opening when no authored turns
        are supplied.
        """
        if not self._authored_turns:
            return await super().opening()
        seed = (
            f"{self._DIRECTOR_TAG}\n"
            "这是对话的开场。请用你自己的语气、结合人设与当前情绪，自然地说出第一句话，"
            "内容要完整传达下面这段诉求；其中所有具体信息（文件路径、参数、字段名、数量、"
            "格式要求等）必须原样保留，不得遗漏，也不要凭空编造额外要求：\n\n"
            + self._authored_turns[0]
        )
        text = await self._guided_reply(seed)
        self._authored_idx = 1
        return text.strip()

    async def _persona_respond(self, assistant_text: str) -> str:
        """Persona reply that advances through the authored turns when present."""
        if not self._authored_turns:
            return await super()._persona_respond(assistant_text)

        if self._authored_idx < len(self._authored_turns):
            seed = (
                f"{assistant_text}\n\n{self._DIRECTOR_TAG}\n"
                "先结合你的人设，对助手上面的回复做出简短、自然的反应（认可/追问/纠正皆可），"
                "然后提出你本轮的新诉求。本轮诉求如下，其中所有具体信息（文件路径、参数、字段名、"
                "数量、格式要求等）必须原样保留、不得遗漏，也不要凭空编造额外要求：\n\n"
                + self._authored_turns[self._authored_idx]
            )
            text = await self._guided_reply(seed)
            self._authored_idx += 1
            return text.strip()

        # All authored turns delivered and the agent has replied to the last —
        # let the persona verify satisfaction and close the conversation.
        seed = (
            f"{assistant_text}\n\n{self._DIRECTOR_TAG}\n"
            "对方已回应了你此前提出的全部诉求。如果你已满意，就自然地表达感谢并结束对话，"
            f"并在消息最末单独另起一行输出 {self.DONE_TOKEN}。如果仍有你先前明确提出、"
            "但显然未被满足的关键交付物，可简短地再强调一次。"
        )
        text = await self._guided_reply(seed)
        self._done = True
        return text.strip()
