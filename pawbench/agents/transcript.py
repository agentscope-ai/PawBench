# -*- coding: utf-8 -*-
"""Transcript extraction utilities shared by all agent harnesses.

All current agents normalise their session data to the qwenpaw
``agent.memory.content`` format during ``run()`` and write a JSON file under
``<workspace>/sessions/``.  ``build_transcript_from_session`` reads that file
and converts it to the OpenClaw event list consumed by the grader.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_transcript_from_session(
    local_workspace: "Path | None",
    stdout: str,
) -> "list[dict[str, Any]]":
    """Build an OpenClaw-compatible transcript from a completed agent run.

    Three sources are tried in order:

    1. **Structured session JSON** in ``<local_workspace>/sessions/*.json``
       (preferred).  All current agents write a session file in the qwenpaw
       ``agent.memory.content`` format before returning from ``run()``.
    2. **Stdout chunk scan** (legacy fallback).  Scan every line of stdout for
       complete JSON objects following the ``{"events": [...]}`` envelope.
    3. **Stdout tail** (last-resort fallback).  Wrap the last 40 000 chars of
       raw stdout in a single text message.
    """
    # ── 1. Prefer structured session JSON ────────────────────────────────────
    if local_workspace is not None:
        try:
            session_events = _events_from_session_dir(local_workspace / "sessions")
        except Exception:
            session_events = []
        if session_events:
            return session_events

    # ── 2. Legacy stdout chunk scan ──────────────────────────────────────────
    tool_calls: list[str] = []
    tool_results: list[str] = []
    assistant_texts: list[str] = []

    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        events_list: list[dict] = []
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    events_list.extend(item.get("events", []))
        elif isinstance(obj, dict):
            events_list = obj.get("events", [])

        for ev in events_list:
            role = ev.get("role")
            content = ev.get("content") or []

            if role == "assistant":
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "plugin_call":
                            data = part.get("data", {})
                            name = data.get("name", "?")
                            args = json.dumps(
                                data.get("arguments", {}), ensure_ascii=False
                            )
                            tool_calls.append(f"{name}({args[:300]})")
                        elif part.get("type") == "text":
                            t = part.get("text", "").strip()
                            if t:
                                assistant_texts.append(t)

            elif role == "tool":
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "data":
                        output = part.get("data", {}).get("output", "")
                        if output:
                            tool_results.append(str(output)[:400])

    events: list[dict[str, Any]] = []
    for i, call in enumerate(tool_calls):
        events.append({
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"[tool_call] {call}"}],
            },
        })
        if i < len(tool_results):
            events.append({
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"[tool_result] {tool_results[i]}"}],
                },
            })

    if assistant_texts:
        events.append({
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": assistant_texts[-1]}],
                "usage": {},
            },
        })

    if events:
        return events

    # ── 3. Stdout tail fallback ───────────────────────────────────────────────
    tail = stdout[-40_000:] if len(stdout) > 40_000 else stdout
    if not tail.strip():
        return []
    return [{
        "type": "message",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": tail}],
            "usage": {},
        },
    }]


# ── session-dir reader ────────────────────────────────────────────────────────

def _events_from_session_dir(sessions_dir: Path) -> "list[dict[str, Any]]":
    """Read session data and convert it to OpenClaw transcript events.

    Three extraction paths are tried in order:

    1. **openclaw trajectory JSONL** — ``*.trajectory.jsonl`` written by the
       openclaw CLI.  The last ``model.completed`` event contains a
       ``messagesSnapshot`` with the full multi-turn conversation.
    2. **qwenpaw native** — ``agent.memory.content`` typed-block format.
       Used by qwenpaw (native), openclaw (converted during ``run()``), and
       hermes (synthesised by ``_write_synthetic_session``).
    3. **CoPaw-Pro CLI** — ``agent._model_trajectory[*].{messages, response}``
       carries OpenAI-Chat-style messages.  Fallback for CLI-mode runs.
    """
    if not sessions_dir.is_dir():
        return []

    # ── 0a. codex stream-json log ─────────────────────────────────────────────
    events = _codex_stream_events(sessions_dir)
    if events:
        return events

    # ── 0b. claude-code stream-json log ──────────────────────────────────────
    events = _claude_code_stream_events(sessions_dir)
    if events:
        return events

    # ── 1. openclaw trajectory JSONL (highest fidelity) ──────────────────────
    events = _trajectory_jsonl_events(sessions_dir)
    if events:
        return events

    # ── 1b. openclaw plain session JSONL (non-trajectory, openclaw ≥ 2026.x) ──
    events = _openclaw_session_jsonl_events(sessions_dir)
    if events:
        return events

    # ── 1c. hermes native session JSONL (OpenAI-chat message format) ─────────
    events = _hermes_session_jsonl_events(sessions_dir)
    if events:
        return events

    # ── 2 & 3. Session JSON (qwenpaw / openclaw-native / openai-chat) ─────────
    candidates = sorted(
        (p for p in sessions_dir.glob("*.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue

        # mini-swe-agent trajectory: {"trajectory_format": "mini-swe-agent-*",
        #   "messages": [...OpenAI-chat format...], "info": {...}}
        events = _mini_swe_trajectory_events(data)
        if events:
            return events

        events = _qwenpaw_native_events(data)
        if events:
            return events

        events = _qwenpaw_v2_native_events(data)
        if events:
            return events

        # OpenClaw sessions write JSONL events directly into agent.memory.content;
        # detect and return them as-is (they are already in transcript format).
        events = _openclaw_native_events(data)
        if events:
            return events

        msgs = _openai_messages_from_trajectory(data)
        if msgs:
            translated = _openai_messages_to_events(msgs)
            if translated:
                usage = _openai_total_usage_from_trajectory(data)
                _add_usage_to_last_assistant(translated, usage)
                return translated

    # ── last-resort raw-text fallback ─────────────────────────────────────────
    # None of the structured parsers above recognised the session.  This happens
    # when an agent's CLI emits a log format we don't model yet (e.g. a new
    # claude-code / codex stream-json shape, or a plain-text trajectory).  Rather
    # than hand the grader an empty transcript — which makes the LLM judge score
    # everything 0 with "no transcript provided" — salvage whatever human-readable
    # text the agent log contains so the judge can still evaluate the deliverable.
    events = _raw_log_text_fallback(sessions_dir)
    if events:
        return events

    return []


# ── raw-text fallback (format-agnostic last resort) ──────────────────────────

# Known per-agent session/log filenames, in rough preference order.  These are
# the files post_run_collect copies into ``<workspace>/sessions/``.
_RAW_LOG_NAMES = (
    "claude-code.txt",
    "codex.txt",
    "mini-swe-agent.txt",
    "aider.txt",
    "hermes-session.jsonl",
    "hermes.txt",
    "openclaw.txt",
    "agent.txt",
)

# Field names that commonly carry human-readable model output across CLIs.
_RAW_TEXT_KEYS = ("text", "result", "content", "message", "output", "thinking")


def _strings_from_json_obj(obj: Any) -> "list[str]":
    """Recursively pull human-readable string values from a parsed JSON object.

    Only keys in ``_RAW_TEXT_KEYS`` are harvested so we don't dump ids / metadata.
    """
    out: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in _RAW_TEXT_KEYS and isinstance(v, str):
                    s = v.strip()
                    if s:
                        out.append(s)
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(obj)
    return out


def _raw_log_text_fallback(sessions_dir: Path) -> "list[dict[str, Any]]":
    """Wrap the raw content of an agent log file into a single assistant message.

    Fires only when every structured parser returned nothing.  Tries each known
    log file in ``_RAW_LOG_NAMES``; for JSONL stream logs it extracts text-bearing
    fields, otherwise it falls back to the raw (truncated) file text.  Returns an
    empty list when no log file has any usable content.
    """
    if not sessions_dir.is_dir():
        return []

    for name in _RAW_LOG_NAMES:
        path = sessions_dir / name
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not raw.strip():
            continue

        # Prefer text harvested from JSON lines (stream-json logs).
        harvested: list[str] = []
        any_json = False
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            any_json = True
            harvested.extend(_strings_from_json_obj(obj))

        if harvested:
            text = "\n".join(harvested)
        elif any_json:
            # JSON lines existed but carried no recognised text fields — skip this
            # file and try the next (avoid dumping pure metadata at the judge).
            continue
        else:
            text = raw

        text = text.strip()
        if not text:
            continue
        if len(text) > 40_000:
            text = text[-40_000:]
        return [{
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "usage": {},
            },
        }]

    return []


# ── openclaw trajectory JSONL parser ─────────────────────────────────────────

def _trajectory_jsonl_events(
    sessions_dir: Path,
) -> "list[dict[str, Any]]":
    """Extract transcript events from openclaw ``*.trajectory.jsonl`` files.

    OpenClaw writes one trajectory JSONL per session.  The last
    ``model.completed`` event in that file carries a ``messagesSnapshot``
    with the full conversation (user turns, assistant thinking/text/toolCall
    blocks, and toolResult turns).
    """
    candidates = sorted(
        (p for p in sessions_dir.glob("*.trajectory.jsonl") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            events = _parse_openclaw_trajectory(path)
        except Exception:
            events = []
        if events:
            return events
    return []


def _openclaw_session_jsonl_events(
    sessions_dir: Path,
) -> "list[dict[str, Any]]":
    """Extract transcript events from plain openclaw session JSONL files.

    OpenClaw writes one session JSONL per conversation (not a trajectory file).
    Each line is a JSON object with ``type`` in
    ``{message, toolCall, toolResult, session}``.  We skip metadata lines
    (``type=session``) and return the rest as-is, since they are already in
    the transcript event format consumed by the grader.
    """
    candidates = sorted(
        (
            p
            for p in sessions_dir.glob("*.jsonl")
            if p.is_file() and not p.name.endswith(".trajectory.jsonl")
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            events = _parse_openclaw_session_jsonl(path)
        except Exception:
            events = []
        if events:
            return events
    return []


def _parse_openclaw_session_jsonl(path: Path) -> "list[dict[str, Any]]":
    """Parse a single openclaw plain session JSONL file into transcript events."""
    _VALID_TYPES = {"message", "toolCall", "toolResult", "tool_call", "tool_result"}
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        otype = obj.get("type")
        if otype == "session":
            continue  # metadata line, skip
        if otype in _VALID_TYPES:
            events.append(obj)
    return events


def _parse_openclaw_trajectory(path: Path) -> "list[dict[str, Any]]":
    """Parse a single openclaw trajectory JSONL file into transcript events.

    Does a single forward pass over all lines to:
    * Keep track of the latest ``messagesSnapshot`` (for transcript events).
    * Accumulate token usage from every ``model.completed`` event (for cost
      accounting).  Each ``model.completed.data.usage`` dict is normalised via
      ``_normalize_usage_dict`` so all key styles are handled.

    The accumulated usage is attached to the last assistant event so that
    ``_extract_usage_from_transcript`` in ``backend.py`` can sum it up.
    """
    last_snapshot: "list | None" = None
    accumulated: dict[str, int] = {}

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict) or obj.get("type") != "model.completed":
            continue
        data = obj.get("data") or {}
        snapshot = data.get("messagesSnapshot")
        if isinstance(snapshot, list) and snapshot:
            last_snapshot = snapshot
        usage = _normalize_usage_dict(data.get("usage"))
        for k, v in usage.items():
            accumulated[k] = accumulated.get(k, 0) + v

    if not last_snapshot:
        return []
    events = _openclaw_snapshot_to_events(last_snapshot)
    _add_usage_to_last_assistant(events, accumulated)
    return events


def _openclaw_snapshot_to_events(
    snapshot: "list[dict[str, Any]]",
) -> "list[dict[str, Any]]":
    """Convert a ``messagesSnapshot`` list to standard transcript events.

    The snapshot uses roles ``user``, ``assistant``, and ``toolResult``, with
    content blocks of types ``text``, ``thinking``, and ``toolCall``.
    """
    events: list[dict[str, Any]] = []
    for msg in snapshot:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content") or []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        elif not isinstance(content, list):
            continue

        if role == "user":
            text = _join_text_blocks(content)
            if text:
                events.append({
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    },
                })

        elif role == "assistant":
            text_chunks: list[str] = []
            tool_call_items: list[dict[str, Any]] = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "thinking":
                    txt = b.get("thinking") or b.get("text") or ""
                    if txt:
                        text_chunks.append(f"[thinking]\n{txt}")
                elif btype == "text":
                    txt = b.get("text") or ""
                    if txt:
                        text_chunks.append(txt)
                elif btype in ("toolCall", "tool_use"):
                    name = b.get("name") or ""
                    if not name:
                        continue
                    raw_args = (
                        b.get("arguments") if btype == "toolCall"
                        else b.get("input")
                    )
                    tool_call_items.append({
                        "type": "toolCall",
                        "name": name,
                        "arguments": _parse_tool_args(raw_args),
                    })

            content_items: list[dict[str, Any]] = []
            if text_chunks:
                content_items.append(
                    {"type": "text", "text": "\n\n".join(text_chunks)}
                )
            content_items.extend(tool_call_items)
            if content_items:
                events.append({
                    "type": "message",
                    "message": {"role": "assistant", "content": content_items},
                })

        elif role in ("toolResult", "tool"):
            text = _join_text_blocks(content)
            if text:
                events.append({
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "content": [{"type": "text", "text": text}],
                    },
                })

    return events


# ── hermes native session format ─────────────────────────────────────────────

def _hermes_session_jsonl_events(sessions_dir: Path) -> "list[dict[str, Any]]":
    """Extract transcript events from ``hermes-session.jsonl``.

    Hermes CLI's session export is OpenAI-Chat-format messages (``role`` /
    ``content`` / ``tool_calls`` / ``tool_call_id``), written either as one
    JSON object per line or as a single line wrapping the whole conversation
    in ``{"messages": [...]}`` — see harbor's own
    ``Hermes._convert_hermes_session_to_atif()`` for the same two shapes.

    Without this, ``hermes-session.jsonl`` falls through every structured
    parser above (its lines don't carry a ``type`` field, and the file's
    ``.jsonl`` extension means the ``*.json`` glob in step 2 never matches
    it either) and lands in ``_raw_log_text_fallback()``, which collapses
    the *entire* multi-turn conversation into a single assistant message —
    tanking ``transcript_length`` to 1 and spuriously tripping the
    SHORT_TRANSCRIPT anomaly check on every run regardless of how much the
    agent actually did.
    """
    path = sessions_dir / "hermes-session.jsonl"
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    messages: list[dict[str, Any]] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("messages"), list):
            messages.extend(m for m in parsed["messages"] if isinstance(m, dict))
        elif isinstance(parsed, dict) and parsed.get("role"):
            messages.append(parsed)

    if not messages:
        return []

    events = _openai_messages_to_events(messages)
    if events:
        usage = _hermes_total_usage(messages)
        _add_usage_to_last_assistant(events, usage)
    return events


def _hermes_total_usage(messages: "list[dict[str, Any]]") -> "dict[str, int]":
    """Accumulate token usage across every assistant message's ``usage`` field."""
    accumulated: dict[str, int] = {}
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        usage = _normalize_usage_dict(m.get("usage"))
        for k, v in usage.items():
            accumulated[k] = accumulated.get(k, 0) + v
    return accumulated


# ── mini-swe-agent trajectory format ─────────────────────────────────────────

def _mini_swe_trajectory_events(
    session_data: "dict[str, Any]",
) -> "list[dict[str, Any]]":
    """Parse mini-swe-agent trajectory into OpenClaw transcript events.

    Format: ``{"trajectory_format": "mini-swe-agent-*", "messages": [...],
    "info": {...}}``

    Messages use OpenAI Chat format (role/content/tool_calls/reasoning_content).
    ``reasoning_content`` is preserved as a ``[thinking]`` text block.
    """
    if not isinstance(session_data, dict):
        return []
    traj_format = session_data.get("trajectory_format", "")
    # Detect by explicit format tag or by structural fingerprint
    if not (
        isinstance(traj_format, str) and traj_format.startswith("mini-swe")
    ) and not (
        "messages" in session_data
        and "info" in session_data
        and "agent" not in session_data
        and isinstance(session_data.get("messages"), list)
    ):
        return []
    msgs = session_data.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return []

    events: list[dict[str, Any]] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")

        if role == "user":
            text = _flatten_content(m.get("content"))
            if text:
                events.append({
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    },
                })

        elif role == "assistant":
            content_items: list[dict[str, Any]] = []
            reasoning = m.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                content_items.append(
                    {"type": "text", "text": f"[thinking]\n{reasoning.strip()}"}
                )
            text = _flatten_content(m.get("content"))
            if text:
                content_items.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                norm = _normalize_tool_call(tc)
                if norm is not None:
                    name, args = norm
                    content_items.append(
                        {"type": "toolCall", "name": name, "arguments": args}
                    )
            if content_items:
                events.append({
                    "type": "message",
                    "message": {"role": "assistant", "content": content_items},
                })

        elif role in ("tool", "function"):
            text = _flatten_content(m.get("content"))
            if text:
                events.append({
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "content": [{"type": "text", "text": text}],
                    },
                })

    return events


# ── codex stream-json format ─────────────────────────────────────────────────

def _codex_stream_events(
    sessions_dir: Path,
) -> "list[dict[str, Any]]":
    """Parse a codex stream-json log file into OpenClaw transcript events.

    Codex writes newline-delimited JSON (one object per line) to codex.txt.
    Relevant event types:
    * ``item.completed`` with ``item.type == "reasoning"``   → thinking block
    * ``item.completed`` with ``item.type == "agent_message"`` → assistant text
    * ``item.completed`` with ``item.type == "command_execution"`` → tool call+result
    * ``turn.completed`` with ``usage``                       → token usage
    """
    log_file = sessions_dir / "codex.txt"
    if not log_file.is_file():
        return []

    raw_lines: list[dict] = []
    try:
        for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                raw_lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []

    if not raw_lines:
        return []

    events: list[dict[str, Any]] = []
    accumulated_usage: dict[str, int] = {}

    for ev in raw_lines:
        etype = ev.get("type", "")

        if etype == "item.completed":
            item = ev.get("item") or {}
            itype = item.get("type", "")

            if itype == "reasoning":
                text = (item.get("text") or "").strip()
                if text:
                    events.append({
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": f"[thinking]\n{text}"}],
                        },
                    })

            elif itype == "agent_message":
                text = (item.get("text") or "").strip()
                if text:
                    events.append({
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": text}],
                        },
                    })

            elif itype == "command_execution":
                cmd = (item.get("command") or "").strip()
                output = (item.get("aggregated_output") or "").strip()
                exit_code = item.get("exit_code")
                if cmd:
                    events.append({
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{
                                "type": "toolCall",
                                "name": "shell",
                                "arguments": {"command": cmd},
                            }],
                        },
                    })
                    result_text = output or f"exit_code={exit_code}"
                    if result_text:
                        events.append({
                            "type": "message",
                            "message": {
                                "role": "toolResult",
                                "content": [{"type": "text", "text": result_text}],
                            },
                        })

        elif etype == "turn.completed":
            usage_raw = ev.get("usage") or {}
            # codex uses input_tokens / output_tokens
            prompt = (
                usage_raw.get("input_tokens") or
                usage_raw.get("prompt_tokens") or
                0
            )
            completion = (
                usage_raw.get("output_tokens") or
                usage_raw.get("completion_tokens") or
                0
            )
            try:
                accumulated_usage["prompt_tokens"] = (
                    accumulated_usage.get("prompt_tokens", 0) + int(prompt)
                )
                accumulated_usage["completion_tokens"] = (
                    accumulated_usage.get("completion_tokens", 0) + int(completion)
                )
            except (TypeError, ValueError):
                pass

    if not events:
        return []

    _add_usage_to_last_assistant(events, accumulated_usage)
    return events


# ── claude-code stream-json format ───────────────────────────────────────────

def _claude_code_stream_events(
    sessions_dir: Path,
) -> "list[dict[str, Any]]":
    """Parse a claude-code stream-json log file into OpenClaw transcript events.

    Claude Code writes a stream-json file (one JSON object per line) with
    event types: ``system``, ``assistant``, ``tool_use``, ``tool_result``,
    ``result``.  The ``assistant`` event embeds a standard Anthropic
    ``message`` object whose ``content`` is a list of text / tool_use blocks.
    """
    log_file = sessions_dir / "claude-code.txt"
    if not log_file.is_file():
        return []

    raw_events: list[dict] = []
    try:
        for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                raw_events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []

    if not raw_events:
        return []

    events: list[dict[str, Any]] = []
    pending_tool_use_id: str | None = None

    for ev in raw_events:
        etype = ev.get("type", "")

        # ── assistant turn (may contain text + tool_use blocks) ───────────────
        if etype == "assistant":
            msg = ev.get("message") or {}
            content_blocks = msg.get("content") or []
            if isinstance(content_blocks, str):
                content_blocks = [{"type": "text", "text": content_blocks}]

            assistant_content: list[dict[str, Any]] = []
            for block in content_blocks:
                btype = block.get("type", "")
                if btype == "text":
                    text = (block.get("text") or "").strip()
                    if text:
                        assistant_content.append({"type": "text", "text": text})
                elif btype == "thinking":
                    text = (block.get("thinking") or "").strip()
                    if text:
                        assistant_content.append(
                            {"type": "text", "text": f"[thinking]\n{text}"}
                        )
                elif btype == "tool_use":
                    name = block.get("name", "")
                    args = block.get("input") or {}
                    pending_tool_use_id = block.get("id")
                    if name:
                        assistant_content.append(
                            {"type": "toolCall", "name": name, "arguments": args}
                        )

            if assistant_content:
                events.append({
                    "type": "message",
                    "message": {"role": "assistant", "content": assistant_content},
                })

        # ── tool result ───────────────────────────────────────────────────────
        elif etype == "tool_result":
            content = ev.get("content") or []
            if isinstance(content, str):
                text = content.strip()
            else:
                text = _flatten_content(content)
            if text:
                events.append({
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "content": [{"type": "text", "text": text}],
                    },
                })

        # ── user message (task prompt) ────────────────────────────────────────
        elif etype == "user":
            content = ev.get("message", {}).get("content") or ev.get("content") or ""
            text = _flatten_content(content) if not isinstance(content, str) else content
            if text.strip():
                events.append({
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": text.strip()}],
                    },
                })

        # ── final result (claude-code --print summary) ───────────────────────
        # The terminal ``result`` event carries the assistant's final answer in
        # its ``result`` field.  Capturing it ensures short runs (which may emit
        # only system + result events) still produce a non-empty transcript.
        elif etype == "result":
            text = ev.get("result")
            if isinstance(text, str) and text.strip():
                events.append({
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": text.strip()}],
                    },
                })

    return events


# ── qwenpaw memory format ─────────────────────────────────────────────────────

def _qwenpaw_native_events(
    session_data: "dict[str, Any]",
) -> "list[dict[str, Any]]":
    """Translate ``agent.memory.content`` into OpenClaw transcript events.

    Each turn in ``content`` is either:
    * ``[msg_dict, ...]`` — qwenpaw HTTP API native (turn[0] is the message)
    * ``msg_dict``        — openclaw converted JSONL (turn itself is the message)
    """
    if not isinstance(session_data, dict):
        return []
    agent = session_data.get("agent")
    if not isinstance(agent, dict):
        return []
    memory = agent.get("memory")
    content = memory.get("content") if isinstance(memory, dict) else None
    if not isinstance(content, list) or not content:
        return []

    events: list[dict[str, Any]] = []
    for turn in content:
        if isinstance(turn, list) and turn:
            msg = turn[0]
        elif isinstance(turn, dict):
            msg = turn
        else:
            continue
        if not isinstance(msg, dict):
            continue

        role = msg.get("role")
        blocks = msg.get("content")
        if isinstance(blocks, dict):
            blocks = [blocks]
        elif isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        elif not isinstance(blocks, list):
            blocks = []

        if role == "user":
            text = _join_text_blocks(blocks)
            if text:
                events.append({
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    },
                })

        elif role == "assistant":
            text_chunks: list[str] = []
            tool_call_items: list[dict[str, Any]] = []

            reasoning = msg.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                text_chunks.append(f"[thinking]\n{reasoning.strip()}")

            for b in blocks:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "thinking":
                    txt = b.get("thinking") or b.get("text") or ""
                    if txt:
                        text_chunks.append(f"[thinking]\n{txt}")
                elif btype == "text":
                    txt = b.get("text") or ""
                    if txt:
                        text_chunks.append(txt)
                elif btype == "tool_use":
                    name = b.get("name") or ""
                    if not name:
                        continue
                    raw_input = b.get("input")
                    if raw_input is None:
                        raw_input = b.get("raw_input")
                    tool_call_items.append({
                        "type": "toolCall",
                        "name": name,
                        "arguments": _parse_tool_args(raw_input),
                    })

            content_items: list[dict[str, Any]] = []
            if text_chunks:
                content_items.append(
                    {"type": "text", "text": "\n\n".join(text_chunks)}
                )
            content_items.extend(tool_call_items)

            if content_items:
                msg_out: dict[str, Any] = {
                    "role": "assistant",
                    "content": content_items,
                }
                usage = _normalize_usage_dict(msg.get("usage"))
                if usage:
                    msg_out["usage"] = usage
                events.append({"type": "message", "message": msg_out})

        elif role == "system":
            for b in blocks:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                text = _join_tool_output(b.get("output"))
                if not text:
                    continue
                events.append({
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "content": [{"type": "text", "text": text}],
                    },
                })

    return events


# ── qwenpaw 2.x memory format ─────────────────────────────────────────────────

def _qwenpaw_v2_native_events(
    session_data: "dict[str, Any]",
) -> "list[dict[str, Any]]":
    """Translate QwenPaw 2.x ``agent.state.context`` turns into transcript events.

    QwenPaw 2.0 replaced the 1.x ``agent.memory.content`` list-of-turn-lists
    format with a flat ``agent.state.context`` list where each turn already
    carries a top-level ``role`` (``user``/``assistant``) and a ``content``
    block list. Block types were also renamed: ``tool_use`` → ``tool_call``,
    and ``tool_result`` blocks are now nested *inside* the assistant turn
    that produced the call (rather than a separate ``system`` turn as in
    1.x). We walk each turn's blocks in order, flushing the accumulated
    text/tool-call content into an assistant event whenever a tool_result is
    encountered, so the resulting event order mirrors the actual
    think → call → result → continue sequence.
    """
    if not isinstance(session_data, dict):
        return []
    agent = session_data.get("agent")
    if not isinstance(agent, dict):
        return []
    state = agent.get("state")
    context = state.get("context") if isinstance(state, dict) else None
    if not isinstance(context, list) or not context:
        return []

    events: list[dict[str, Any]] = []
    for turn in context:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        blocks = turn.get("content")
        if isinstance(blocks, dict):
            blocks = [blocks]
        elif not isinstance(blocks, list):
            continue

        if role == "user":
            text = _join_text_blocks(blocks)
            if text:
                events.append({
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    },
                })
            continue

        if role != "assistant":
            continue

        pending_text: list[str] = []
        pending_tool_calls: list[dict[str, Any]] = []

        def _flush() -> None:
            content_items: list[dict[str, Any]] = []
            if pending_text:
                content_items.append(
                    {"type": "text", "text": "\n\n".join(pending_text)}
                )
            content_items.extend(pending_tool_calls)
            if content_items:
                events.append({
                    "type": "message",
                    "message": {"role": "assistant", "content": content_items},
                })
            pending_text.clear()
            pending_tool_calls.clear()

        for b in blocks:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "thinking":
                txt = b.get("thinking") or b.get("text") or ""
                if txt:
                    pending_text.append(f"[thinking]\n{txt}")
            elif btype == "text":
                txt = b.get("text") or ""
                if txt:
                    pending_text.append(txt)
            elif btype == "tool_call":
                name = b.get("name") or ""
                if name:
                    pending_tool_calls.append({
                        "type": "toolCall",
                        "name": name,
                        "arguments": _parse_tool_args(b.get("input")),
                    })
            elif btype == "tool_result":
                _flush()
                text = _join_tool_output(b.get("output"))
                if text:
                    events.append({
                        "type": "message",
                        "message": {
                            "role": "toolResult",
                            "content": [{"type": "text", "text": text}],
                        },
                    })

        _flush()

        usage = _normalize_usage_dict(turn.get("usage"))
        if usage:
            _add_usage_to_last_assistant(events, usage)

    return events


# ── openclaw native event format ─────────────────────────────────────────────

def _openclaw_native_events(
    session_data: "dict[str, Any]",
) -> "list[dict[str, Any]]":
    """Extract openclaw-format events stored directly in agent.memory.content.

    OpenClaw sessions write JSONL events (type=message/toolCall/toolResult)
    directly into ``agent.memory.content``.  Those events are already in the
    expected transcript format, so we return them as-is.

    Detection heuristic: every item in content must have a ``"type"`` field
    whose value is one of the known openclaw event types.  A single item
    with an unknown type causes the function to return ``[]`` so the caller
    falls through to the next parser.
    """
    if not isinstance(session_data, dict):
        return []
    agent = session_data.get("agent")
    if not isinstance(agent, dict):
        return []
    memory = agent.get("memory")
    if not isinstance(memory, dict):
        return []
    content = memory.get("content")
    if not isinstance(content, list) or not content:
        return []

    _OPENCLAW_TYPES = {"message", "toolCall", "toolResult", "tool_call", "tool_result"}
    events = []
    for item in content:
        if not isinstance(item, dict):
            return []
        item_type = item.get("type")
        if item_type not in _OPENCLAW_TYPES:
            return []  # not openclaw event format (probably qwenpaw turns)
        events.append(item)

    return events


# ── OpenAI Chat / _model_trajectory format ────────────────────────────────────

def _openai_messages_from_trajectory(
    session_data: "dict[str, Any]",
) -> "list[dict[str, Any]]":
    """Pull OpenAI Chat-style messages from ``agent._model_trajectory``.

    Used as a fallback for CoPaw-Pro CLI-mode sessions only.
    """
    if not isinstance(session_data, dict):
        return []
    agent = session_data.get("agent")
    if not isinstance(agent, dict):
        return []

    msgs: list[dict[str, Any]] = []
    for entry in agent.get("_model_trajectory") or []:
        if not isinstance(entry, dict):
            continue
        for m in entry.get("messages") or []:
            if isinstance(m, dict) and m.get("role"):
                msgs.append(m)
        resp = entry.get("response")
        if isinstance(resp, list):
            for m in resp:
                if isinstance(m, dict) and m.get("role"):
                    msgs.append(m)
        elif isinstance(resp, dict) and resp.get("role"):
            msgs.append(resp)
    return msgs


def _openai_total_usage_from_trajectory(
    session_data: "dict[str, Any]",
) -> "dict[str, int]":
    """Accumulate token usage across all entries in ``agent._model_trajectory``.

    Each trajectory entry may expose usage at:
    * ``entry["usage"]``           — top-level usage for this API call.
    * ``entry["response"]["usage"]`` — usage embedded in the response object.

    All formats are normalised via ``_normalize_usage_dict``.
    """
    if not isinstance(session_data, dict):
        return {}
    agent = session_data.get("agent")
    if not isinstance(agent, dict):
        return {}
    accumulated: dict[str, int] = {}
    for entry in agent.get("_model_trajectory") or []:
        if not isinstance(entry, dict):
            continue
        for usage_source in (
            entry.get("usage"),
            entry.get("response", {}).get("usage") if isinstance(entry.get("response"), dict) else None,
        ):
            usage = _normalize_usage_dict(usage_source)
            for k, v in usage.items():
                accumulated[k] = accumulated.get(k, 0) + v
    return accumulated


def _openai_messages_to_events(
    msgs: "list[dict[str, Any]]",
) -> "list[dict[str, Any]]":
    """Translate OpenAI-Chat-style messages into OpenClaw transcript events."""
    events: list[dict[str, Any]] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")

        if role == "user":
            text = _flatten_content(m.get("content"))
            if text:
                events.append({
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    },
                })

        elif role == "assistant":
            text = _flatten_content(m.get("content"))
            content_items: list[dict[str, Any]] = []
            if text:
                content_items.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                norm = _normalize_tool_call(tc)
                if norm is not None:
                    name, args = norm
                    content_items.append(
                        {"type": "toolCall", "name": name, "arguments": args}
                    )
            if "function_call" in m and not m.get("tool_calls"):
                norm = _normalize_tool_call({"function_call": m["function_call"]})
                if norm is not None:
                    name, args = norm
                    content_items.append(
                        {"type": "toolCall", "name": name, "arguments": args}
                    )
            if content_items:
                events.append({
                    "type": "message",
                    "message": {"role": "assistant", "content": content_items},
                })

        elif role in ("tool", "function"):
            text = _flatten_content(m.get("content"))
            if text:
                events.append({
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "content": [{"type": "text", "text": text}],
                    },
                })
        # role == "system" intentionally skipped

    return events


# ── low-level helpers ─────────────────────────────────────────────────────────

def _join_text_blocks(blocks: "list[Any]") -> str:
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            txt = b.get("text") or ""
            if txt:
                parts.append(txt)
        elif isinstance(b, str) and b:
            parts.append(b)
    return "\n".join(parts)


def _join_tool_output(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if isinstance(item, dict):
                txt = item.get("text") or item.get("content")
                if txt:
                    parts.append(str(txt))
            elif isinstance(item, str) and item:
                parts.append(item)
        return "\n".join(parts)
    if isinstance(output, dict):
        txt = output.get("text") or output.get("content")
        return str(txt) if txt else ""
    return str(output)


def _flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                txt = item.get("text") or item.get("content")
                if txt:
                    parts.append(str(txt))
        return "\n".join(parts)
    return str(content)


def _parse_tool_args(raw_input: Any) -> "dict[str, Any]":
    if raw_input is None:
        return {}
    if isinstance(raw_input, dict):
        return raw_input
    if isinstance(raw_input, str):
        try:
            parsed = json.loads(raw_input)
            return parsed if isinstance(parsed, dict) else {"_raw": parsed}
        except (json.JSONDecodeError, ValueError):
            return {"_raw": raw_input}
    return {"_raw": raw_input}


def _normalize_tool_call(tc: Any) -> "tuple[str, dict[str, Any]] | None":
    if not isinstance(tc, dict):
        return None
    fn = tc.get("function") or tc.get("function_call")
    if isinstance(fn, dict):
        name = fn.get("name") or ""
        raw_args = fn.get("arguments")
    else:
        name = tc.get("name") or ""
        raw_args = tc.get("arguments") if "arguments" in tc else tc.get("input")
    if not name:
        return None
    return name, _parse_tool_args(raw_args)


# ── token-usage helpers ───────────────────────────────────────────────────────

def _normalize_usage_dict(raw: "Any") -> "dict[str, int]":
    """Normalize token-usage data from any known API format to a canonical dict.

    Recognised key styles:
    * OpenAI snake_case   – ``prompt_tokens``   / ``completion_tokens``
    * Anthropic snake_case – ``input_tokens``    / ``output_tokens``
    * OpenClaw camelCase  – ``inputTokens``      / ``outputTokens``

    Returns ``{}`` when no positive token counts are found.
    """
    if not isinstance(raw, dict):
        return {}
    prompt = (
        raw.get("prompt_tokens") or
        raw.get("input_tokens") or
        raw.get("inputTokens") or
        0
    )
    completion = (
        raw.get("completion_tokens") or
        raw.get("output_tokens") or
        raw.get("outputTokens") or
        0
    )
    try:
        p, c = int(prompt), int(completion)
    except (TypeError, ValueError):
        return {}
    return {"prompt_tokens": p, "completion_tokens": c} if (p or c) else {}


def _add_usage_to_last_assistant(
    events: "list[dict[str, Any]]",
    usage: "dict[str, int]",
) -> None:
    """Attach *usage* in-place to the last assistant event in *events*.

    Used by parsers that accumulate per-session usage (e.g. openclaw
    trajectory) and want to make it accessible to
    ``_extract_usage_from_transcript`` without changing any return signatures.
    No-op when *usage* is empty or no assistant event is found.
    """
    if not usage:
        return
    for ev in reversed(events):
        msg = ev.get("message")
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            msg["usage"] = usage
            return
