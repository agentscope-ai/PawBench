# -*- coding: utf-8 -*-
"""``.user/`` context loading for the user simulator.

Ported (slimmed) from CuES-plus ``src/client/user_agent.py``. The cowork /
``.patch`` machinery is intentionally dropped for the first version of the MCP
sidecar integration (Strategy A); only persona/dialogue context needed for
multi-turn simulation is kept.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import flatten_multimodal_content

__all__ = [
    "APPROVAL_MARKERS",
    "UserContext",
    "is_approval_request",
    "load_user_context",
]

_CONTEXT_FILE_CHAR_LIMIT = 6000
_CONTEXT_GROUP_CHAR_LIMIT = 10000
_TASK_MESSAGE_CHAR_LIMIT = 3000
_TASK_FIELD_CHAR_LIMIT = 6000
_TASK_MESSAGE_LIMIT = 6


APPROVAL_MARKERS: tuple[str, ...] = (
    "Waiting for approval",
    "等待审批",
    "Type `/approve` to approve",
    "输入 `/approve` 批准执行",
)


def is_approval_request(text: str) -> bool:
    if not text:
        return False
    return any(m in text for m in APPROVAL_MARKERS)


# ---------------------------------------------------------------------------
# context truncation helpers
# ---------------------------------------------------------------------------


def _truncate_context_text(text: str, *, max_chars: int, label: str) -> str:
    text = str(text or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n...<{label} 截断到 {max_chars} 字>"


def _read_context_file(path: Path, *, max_chars: int, label: str) -> str:
    if not path.is_file():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(max(0, max_chars) + 1)
    except OSError:
        return ""
    return _truncate_context_text(text, max_chars=max_chars, label=label)


def _compact_task_value(value: Any, *, max_chars: int, label: str) -> Any:
    if value in (None, "", [], {}):
        return value
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        rendered = str(value)
    if len(rendered) <= max_chars:
        return value
    return _truncate_context_text(rendered, max_chars=max_chars, label=label)


def _compact_task_messages(messages: Any) -> Any:
    if not isinstance(messages, list):
        return _compact_task_value(
            messages, max_chars=_TASK_FIELD_CHAR_LIMIT, label="task.messages"
        )
    compact: list[dict[str, Any]] = []
    for message in messages[:_TASK_MESSAGE_LIMIT]:
        if not isinstance(message, dict):
            compact.append({
                "content": _truncate_context_text(
                    str(message), max_chars=_TASK_MESSAGE_CHAR_LIMIT, label="task.message"
                ),
            })
            continue
        item: dict[str, Any] = {}
        for key in ("role", "name"):
            if message.get(key):
                item[key] = message[key]
        content = flatten_multimodal_content(message.get("content"))
        if content:
            item["content"] = _truncate_context_text(
                content, max_chars=_TASK_MESSAGE_CHAR_LIMIT, label="task.message.content"
            )
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            item["tool_calls"] = len(tool_calls)
        compact.append(item)
    if len(messages) > _TASK_MESSAGE_LIMIT:
        compact.append({"messages_omitted": len(messages) - _TASK_MESSAGE_LIMIT})
    return compact


def _compact_task_metadata(task_yaml: dict[str, Any]) -> dict[str, Any]:
    return {
        "messages": _compact_task_messages(task_yaml.get("messages")),
        "metadata": _compact_task_value(
            task_yaml.get("metadata"), max_chars=_TASK_FIELD_CHAR_LIMIT, label="task.metadata"
        ),
        "evaluation": _compact_task_value(
            task_yaml.get("evaluation"),
            max_chars=_TASK_FIELD_CHAR_LIMIT,
            label="task.evaluation",
        ),
    }


# ---------------------------------------------------------------------------
# UserContext
# ---------------------------------------------------------------------------


@dataclass
class UserContext:
    """Aggregated ``.user/`` context injected into the user-agent system prompt."""

    persona: str = ""
    profile: dict[str, Any] | None = None
    long_term_memory: str = ""
    domain_knowledge: str = ""
    recent_focus: str = ""
    preferences: str = ""
    timeline: str = ""
    prior_queries: str = ""
    prior_interactions: str = ""
    state_init: str = ""
    latent_goals: str = ""
    dialogue_policy: str = ""
    world_model: str = ""
    private_rules: str = ""
    task_metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _read_group(dir_path: Path, *, label: str) -> str:
    if not dir_path.is_dir():
        return ""
    chunks: list[str] = []
    for f in sorted(dir_path.glob("*"))[:5]:
        text = _read_context_file(
            f, max_chars=_CONTEXT_FILE_CHAR_LIMIT, label=f"{label}/{f.name}"
        )
        if text:
            chunks.append(text)
    return _truncate_context_text(
        "\n\n---\n\n".join(chunks), max_chars=_CONTEXT_GROUP_CHAR_LIMIT, label=label
    )


def load_user_context(
    task_dir: Path, *, task_metadata: dict[str, Any] | None = None
) -> UserContext:
    """Read ``.user/`` and ``task.yaml`` into a :class:`UserContext`.

    If *task_metadata* is already parsed by the caller, task.yaml is not re-read.
    """
    task_dir = Path(task_dir)
    user_dir = task_dir / ".user"

    def _read(rel: str) -> str:
        return _read_context_file(
            user_dir / rel, max_chars=_CONTEXT_FILE_CHAR_LIMIT, label=f".user/{rel}"
        )

    def _read_json(rel: str) -> dict[str, Any] | None:
        path = user_dir / rel
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    if task_metadata is not None:
        task_metadata = _compact_task_metadata(task_metadata)
    else:
        task_yaml = task_dir / "task.yaml"
        if task_yaml.is_file():
            try:
                import yaml  # local import: only needed when reading task.yaml

                data = yaml.safe_load(task_yaml.read_text(encoding="utf-8")) or {}
                task_metadata = _compact_task_metadata(data if isinstance(data, dict) else {})
            except Exception:  # noqa: BLE001
                task_metadata = None

    long_term_memory = _read_group(user_dir / "memory", label=".user/memory") or _read("memory.md")

    return UserContext(
        persona=_read("persona.md"),
        profile=_read_json("profile.json"),
        long_term_memory=long_term_memory,
        domain_knowledge=_read("domain_knowledge.md"),
        recent_focus=_read("recent_focus.md"),
        preferences=_read("preferences.md"),
        timeline=_read("timeline.md"),
        prior_queries=_read("prior_queries.md"),
        prior_interactions=_read_group(user_dir / "history", label=".user/history"),
        state_init=_read_group(user_dir / "state", label=".user/state"),
        latent_goals=_read("latent_goals.md"),
        dialogue_policy=_read("dialogue_policy.md"),
        world_model=_read("world_model.md"),
        private_rules=_read("private_rules.md"),
        task_metadata=task_metadata,
    )
