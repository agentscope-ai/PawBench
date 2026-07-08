# -*- coding: utf-8 -*-
"""Small pure helpers for the user simulator.

Slimmed from CuES-plus ``src/runtime/shared_utils.py``. The upstream
``flatten_multimodal_content`` depends on a ``multimodal`` module for modality
detection; here we inline a lightweight version that keeps text blocks and
replaces binary blocks (images / videos / audio) with a placeholder, which is
all the user simulator needs to summarise a builder-provided first user query.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

__all__ = ["flatten_multimodal_content", "extract_first_user_content"]

_MODALITY_KEYS = {
    "image_url": "image",
    "image": "image",
    "video_url": "video",
    "video": "video",
    "audio_url": "audio",
    "audio": "audio",
    "input_audio": "audio",
}


def _block_modality(block: Mapping[str, Any]) -> str:
    btype = str(block.get("type") or "")
    if btype in _MODALITY_KEYS:
        return _MODALITY_KEYS[btype]
    for key, modality in _MODALITY_KEYS.items():
        if key in block:
            return modality
    return ""


def flatten_multimodal_content(content: Any) -> str:
    """Collapse OpenAI multimodal content blocks into a plain-text summary.

    Text blocks are preserved verbatim; binary blocks become ``[image]`` etc.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        blocks = [content]
    elif isinstance(content, list):
        blocks = content
    else:
        return str(content or "")

    parts: list[str] = []
    for blk in blocks:
        if isinstance(blk, str):
            parts.append(blk)
            continue
        if not isinstance(blk, Mapping):
            continue
        btype = blk.get("type")
        if btype == "tool_result":
            nested = flatten_multimodal_content(blk.get("content"))
            tool_use_id = str(blk.get("tool_use_id") or blk.get("id") or "").strip()
            label = f"[tool_result id={tool_use_id}]" if tool_use_id else "[tool_result]"
            parts.append(f"{label} {nested}".strip())
            continue
        if btype == "tool_use":
            name = str(blk.get("name") or "").strip()
            tool_id = str(blk.get("id") or "").strip()
            raw_input = blk.get("input")
            try:
                args = json.dumps(
                    raw_input if raw_input is not None else {},
                    ensure_ascii=False,
                    default=str,
                )
            except TypeError:
                args = str(raw_input)
            if len(args) > 1200:
                args = args[:1200] + f"...<截断{len(args) - 1200}字>"
            details = []
            if name:
                details.append(f"name={name}")
            if tool_id:
                details.append(f"id={tool_id}")
            label = "[tool_use" + ((" " + " ".join(details)) if details else "") + "]"
            parts.append(f"{label} {args}".strip())
            continue

        text = blk.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif isinstance(blk.get("content"), str):
            parts.append(blk["content"])

        modality = _block_modality(blk)
        if modality:
            parts.append(f"[{modality}]")
        elif not isinstance(text, str) and not isinstance(blk.get("content"), str):
            parts.append(f"[{btype or 'block'}]")
    return " ".join(p for p in parts if p)


def extract_first_user_content(yaml_data: Mapping[str, Any]) -> str:
    """Extract the plain text of the first ``role=user`` message in task metadata."""
    msgs = yaml_data.get("messages") if isinstance(yaml_data, Mapping) else None
    if not isinstance(msgs, list):
        return ""
    for entry in msgs:
        if not isinstance(entry, Mapping) or entry.get("role") != "user":
            continue
        content = entry.get("content")
        if isinstance(content, (list, Mapping)):
            return flatten_multimodal_content(content).strip()
        if isinstance(content, str):
            return content.strip()
    return ""
