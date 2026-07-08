# -*- coding: utf-8 -*-
"""User agent — simulates a human user across multi-turn dialogue.

Ported (slimmed) from CuES-plus ``src/client/user_agent.py``. Two public
entrypoints mirror the upstream contract:

* :meth:`opening` — produce the first user message.
* :meth:`respond_or_approve` — unified entry: detect an approval marker and
  answer with ``/approve`` or a refusal; otherwise reply in persona.

The cowork tool-loop path from upstream is intentionally omitted in this first
MCP-sidecar version.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from . import defaults
from .context import UserContext, is_approval_request
from .llm import LLMClient, LLMConfig
from .prompts import build_user_agent_approval_prompt, build_user_agent_system_prompt
from .redaction import sanitize_snippet
from .utils import extract_first_user_content

logger = logging.getLogger(__name__)

__all__ = ["UserAgent"]

_ARTIFACT_HISTORY_MESSAGE_CHAR_LIMIT = 4000


class UserAgent:
    """Simulated user handling both persona dialogue and tool approvals."""

    DONE_TOKEN = "[DONE]"
    APPROVE_TOKEN = "/approve"

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
        """Create a user agent.

        Parameters
        ----------
        llm_factory:
            Optional factory building an :class:`LLMClient` from an
            :class:`LLMConfig`; defaults to :class:`LLMClient`.
        persona_llm / approval_llm:
            Optional pre-built clients (used by tests to inject fakes). When
            provided they take precedence over *llm_factory* / config.
        """
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

    @property
    def done(self) -> bool:
        return self._done

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._messages)

    def artifact_history(self) -> list[dict[str, Any]]:
        """Return a bounded debug view for persisted trajectory metadata."""
        history: list[dict[str, Any]] = []
        for message in self._messages:
            if message.get("role") == "system":
                continue
            content = message.get("content")
            if isinstance(content, str):
                content_text = content
            else:
                try:
                    content_text = json.dumps(content, ensure_ascii=False, default=str)
                except TypeError:
                    content_text = str(content)
            item = {k: v for k, v in message.items() if k != "content"}
            item["content"] = content_text[:_ARTIFACT_HISTORY_MESSAGE_CHAR_LIMIT]
            item["content_chars"] = len(content_text)
            item["content_truncated"] = (
                len(content_text) > _ARTIFACT_HISTORY_MESSAGE_CHAR_LIMIT
            )
            history.append(item)
        return history

    # ------------------------------------------------------------------
    # public entrypoints
    # ------------------------------------------------------------------

    async def opening(self) -> str:
        """Generate the first user message from persona + task context."""
        seed = "请按照你的人设、当前心理状态和最近关注，自然地说出第一句开场白。"
        first_user_msg = extract_first_user_content(self.context.task_metadata or {})
        if first_user_msg:
            seed = (
                "下面是 builder 给的初始 user query，可以作为开场参考；"
                "请用你自己的语气重新表达，不要直接复制：\n\n" + first_user_msg
            )
        self._messages.append({"role": "user", "content": seed})
        resp = await self._persona_llm.chat(self._messages)
        text = (resp.choices[0].message.content or "").strip()
        self._messages.append({"role": "assistant", "content": text})
        return self._post_process(text)

    async def respond_or_approve(self, assistant_text: str) -> str:
        """Unified entry: auto-detect whether the assistant reply needs approval.

        Approval-decision messages do **not** join the persona history, keeping
        the two prompt tracks fully decoupled.
        """
        if is_approval_request(assistant_text):
            return await self.approve_tool_request(assistant_text)
        return await self._persona_respond(assistant_text)

    async def approve_tool_request(self, marker_text: str) -> str:
        """Handle only a tool-guardrail approval, without a persona reply."""
        return await self._decide_approval(marker_text)

    # ------------------------------------------------------------------
    # approval decision
    # ------------------------------------------------------------------

    async def _decide_approval(self, marker_text: str) -> str:
        """Let an independent LLM decide ``/approve`` vs refusal; fail closed."""
        try:
            resp = await self._approval_llm.chat(
                [
                    {"role": "system", "content": build_user_agent_approval_prompt()},
                    {"role": "user", "content": marker_text},
                ],
                temperature=0.0,
                max_tokens=256,
                retries=defaults.DEFAULT_LLM_RETRIES,
            )
            decision = (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("approval LLM failed: %s", sanitize_snippet(exc))
            return "我无法确认此操作是否安全，暂不批准。"

        if not decision:
            return "我无法确认此操作是否安全，暂不批准。"
        return decision

    # ------------------------------------------------------------------
    # persona dialogue
    # ------------------------------------------------------------------

    async def _persona_respond(self, assistant_text: str) -> str:
        """Generate the user's next message in persona mode."""
        self._messages.append({"role": "user", "content": assistant_text})
        resp = await self._persona_llm.chat(self._messages)
        text = (resp.choices[0].message.content or "").strip()
        self._messages.append({"role": "assistant", "content": text})
        return self._post_process(text)

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _post_process(self, text: str) -> str:
        stripped = text.strip()
        if self.DONE_TOKEN in stripped:
            self._done = True
        return stripped
