# -*- coding: utf-8 -*-
"""Thin async OpenAI-compatible client for the user simulator.

This is a deliberately slimmed replacement for the full CuES-plus
``src/runtime/llm.py`` (which carries an adaptive multi-key AIMD rate limiter
meant for large-scale data generation). The user simulator issues at most one
completion per dialogue turn, so a small wrapper with bounded retries is
sufficient and keeps the subpackage dependency-light.

The ``chat`` return value mirrors the OpenAI SDK response shape
(``resp.choices[0].message.content``) so :class:`UserAgent` code stays close to
the upstream implementation, and offline tests can inject a fake with the same
shape via :func:`make_chat_result`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Sequence

from . import defaults
from .redaction import sanitize_snippet

logger = logging.getLogger(__name__)

__all__ = ["LLMConfig", "LLMClient", "make_chat_result"]


def make_chat_result(text: str) -> SimpleNamespace:
    """Build a minimal object shaped like an OpenAI ChatCompletion response.

    Useful for tests and for any code that needs to fabricate a response
    carrying just ``choices[0].message.content``.
    """
    message = SimpleNamespace(content=text, role="assistant")
    choice = SimpleNamespace(message=message, finish_reason="stop", index=0)
    return SimpleNamespace(choices=[choice])


@dataclass
class LLMConfig:
    """Connection + sampling config for one logical LLM role."""

    model: str
    api_key: str
    base_url: str = ""
    temperature: float = defaults.DEFAULT_TEMPERATURE
    timeout: float = defaults.DEFAULT_LLM_TIMEOUT
    max_retries: int = defaults.DEFAULT_LLM_RETRIES
    extra: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """Minimal async chat client backed by ``openai.AsyncOpenAI``."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        if not config.model:
            raise ValueError(
                "user-sim LLM model is empty; set USER_SIM_MODEL or pass model= explicitly"
            )
        if not config.api_key:
            raise ValueError(
                "user-sim LLM api_key is empty; set USER_SIM_API_KEY (no fallback to "
                "agent/judge keys is allowed)"
            )
        # Imported lazily so importing this module never hard-requires openai
        # (e.g. when only the fake client is used in tests).
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url or None,
            timeout=config.timeout,
            max_retries=0,  # we do our own bounded retry with backoff
        )

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        retries: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run one chat completion with bounded exponential backoff."""
        attempts = self.config.max_retries if retries is None else retries
        attempts = max(1, int(attempts))
        temp = self.config.temperature if temperature is None else temperature

        params: dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": temp,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        params.update(self.config.extra)
        params.update(kwargs)

        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._client.chat.completions.create(**params)
            except Exception as exc:  # noqa: BLE001 - surfaced after retries
                last_exc = exc
                if attempt + 1 >= attempts:
                    break
                backoff = min(2.0 ** attempt, 10.0)
                logger.warning(
                    "user-sim LLM call failed (attempt %d/%d): %s; retrying in %.1fs",
                    attempt + 1,
                    attempts,
                    sanitize_snippet(exc),
                    backoff,
                )
                await asyncio.sleep(backoff)

        assert last_exc is not None
        raise last_exc
