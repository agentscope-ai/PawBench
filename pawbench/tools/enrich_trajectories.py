"""Fuse the user-sim dialogue into ``agent/trajectory.json`` in chronological order.

For multi-turn (UA) tasks, the agent talks to the simulated user through MCP
tools (``user-sim__start_conversation`` / ``...__send_message_to_user``, named
differently per harness). The user's replies already live inside the raw
trajectory as deeply-nested / re-escaped JSON strings attached to each tool
call's ``observation`` — this script decodes them in place and inserts clean
``source: "user"`` (and ``source: "agent_to_user"``) steps right after the
tool call that produced them, so a single file tells the whole story without
cross-referencing ``agent/user_sim_state.json``.

Usage:
    python -m pawbench.tools.enrich_trajectories PATH [PATH ...]
    python -m pawbench.tools.enrich_trajectories results/**/agent/trajectory.json

Each ``trajectory.json`` is left untouched; output is written next to it as
``trajectory.enriched.json`` (or to ``--out-suffix``/``--in-place``).
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

# Tool names that carry the user-sim dialogue, across the different harness
# naming conventions observed in practice (qwenpaw/openclaw: "user-sim__x",
# hermes: "mcp_user_sim_x", claude-code: "mcp__user-sim__x", codex: none —
# codex injects "user" steps natively and needs no extraction).
_START_SUFFIXES = ("start_conversation",)
_SEND_SUFFIXES = ("send_message_to_user",)


def _is_user_sim_call(function_name: str, suffixes: tuple[str, ...]) -> bool:
    name = (function_name or "").lower()
    return "user" in name and "sim" in name and any(name.endswith(s) for s in suffixes)


def _deep_find(obj: Any, key: str) -> Any:
    """Recursively unwrap nested dict/str layers to find *key*.

    Harnesses wrap the MCP result differently (raw dict; ``{"result": "<json
    string>"}``; ``{"structuredContent": {...}}``; free text like
    ``"<untrusted_tool_result ...>\\n...\\n{...}"`` prefixed before the JSON).
    Rather than special-case each wrapper, recursively descend into dict
    values and, for strings, locate and decode the first embedded JSON object
    — this handles arbitrary nesting/escaping depth uniformly.
    """
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for pref in ("result", "content", "structuredContent"):
            if pref in obj:
                found = _deep_find(obj[pref], key)
                if found is not None:
                    return found
        for value in obj.values():
            found = _deep_find(value, key)
            if found is not None:
                return found
        return None
    if isinstance(obj, str):
        idx = obj.find("{")
        decoder = json.JSONDecoder()
        while idx != -1:
            try:
                parsed, _end = decoder.raw_decode(obj, idx)
            except json.JSONDecodeError:
                idx = obj.find("{", idx + 1)
                continue
            found = _deep_find(parsed, key)
            if found is not None:
                return found
            idx = obj.find("{", idx + 1)
        return None
    return None


def _extract_outgoing_message(arguments: Any) -> str | None:
    """Pull the agent's own text out of a send_message_to_user tool call."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments.strip() or None
    if isinstance(arguments, dict):
        for key in ("message", "text", "content"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def enrich_steps(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Return a new steps list with user-sim dialogue expanded in place.

    Codex-style harnesses already inject native ``source: "user"`` steps for
    each simulator turn and expose no user-sim tool calls, so this is a no-op
    for them (``inserted`` stays 0).
    """
    new_steps: list[dict[str, Any]] = []
    inserted = 0
    next_id = 1

    def _next_step(source: str, text: str) -> dict[str, Any]:
        nonlocal next_id
        step = {"step_id": next_id, "source": source, "message": text}
        next_id += 1
        return step

    for step in steps:
        renumbered = dict(step)
        renumbered["step_id"] = next_id
        next_id += 1
        new_steps.append(renumbered)

        tool_calls = step.get("tool_calls") or []
        observation = step.get("observation") or {}
        results = observation.get("results") if isinstance(observation, dict) else None
        results_by_id = {
            r.get("source_call_id"): r
            for r in (results or [])
            if isinstance(r, dict)
        }

        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function_name") or tc.get("name") or ""
            call_id = tc.get("tool_call_id")
            result = results_by_id.get(call_id, {})
            content = result.get("content") if isinstance(result, dict) else None

            if _is_user_sim_call(fn, _SEND_SUFFIXES):
                outgoing = _extract_outgoing_message(tc.get("arguments"))
                if outgoing:
                    new_steps.append(_next_step("agent_to_user", outgoing))
                    inserted += 1

            if _is_user_sim_call(fn, _START_SUFFIXES) or _is_user_sim_call(fn, _SEND_SUFFIXES):
                user_message = _deep_find(content, "user_message") if content else None
                if isinstance(user_message, str) and user_message.strip():
                    new_steps.append(_next_step("user", user_message))
                    inserted += 1

    return new_steps, inserted


def enrich_file(traj_path: Path, out_path: Path) -> int:
    data = json.loads(traj_path.read_text(encoding="utf-8"))
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise ValueError(f"{traj_path}: no 'steps' array")
    new_steps, inserted = enrich_steps(steps)
    data["steps"] = new_steps
    if inserted:
        data.setdefault("final_metrics", {})["total_steps"] = len(new_steps)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return inserted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="trajectory.json path(s) or glob(s)")
    parser.add_argument(
        "--out-suffix",
        default=".enriched.json",
        help="Suffix appended to the stem for the output file (default: .enriched.json)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite trajectory.json instead of writing a sibling file",
    )
    args = parser.parse_args(argv)

    files: list[Path] = []
    for pattern in args.paths:
        matches = glob.glob(pattern, recursive=True)
        files.extend(Path(m) for m in matches) if matches else files.append(Path(pattern))

    if not files:
        print("No files matched.", file=sys.stderr)
        return 1

    total_inserted = 0
    for traj_path in files:
        if not traj_path.is_file():
            print(f"skip (not found): {traj_path}", file=sys.stderr)
            continue
        out_path = traj_path if args.in_place else traj_path.with_name(
            traj_path.stem + args.out_suffix
        )
        try:
            inserted = enrich_file(traj_path, out_path)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"skip ({exc}): {traj_path}", file=sys.stderr)
            continue
        total_inserted += inserted
        print(f"{traj_path} -> {out_path} (+{inserted} dialogue steps)")

    print(f"Done. {len(files)} file(s), {total_inserted} dialogue steps inserted total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
