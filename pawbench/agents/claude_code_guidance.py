"""Execution guidance shared by PawBench's Claude Code adapters."""

from __future__ import annotations

import shlex

CLAUDE_CODE_EXECUTION_GUIDANCE = """PawBench task execution rules:

- The current working directory is the task root. Resolve every path in the user
  request literally relative to that directory. For example, `workspace/foo`
  means `<cwd>/workspace/foo`, not `<cwd>/foo`. Before finishing, verify that
  every requested deliverable exists at its exact requested path.
- Inspect the relevant source files before deriving facts. For binary or
  structured formats such as XLSX, DOCX, PDF, SQLite, Parquet, RTF, EML, and MSG,
  use an appropriate Python library or command-line parser. If a direct read
  fails, switch parsers; never guess, interpolate, or fabricate missing values.
  PawBench may provide pre-extracted readable mirrors under
  `<cwd>/.pawbench-extracted/` using the original relative path plus `.txt`;
  inspect those mirrors whenever they exist.
- Cross-check computed values against authoritative sources and explicitly
  resolve conflicts or stale data. Do not claim a file was validated unless you
  actually ran the validation and checked its result.
- Preserve the requested output semantics. Runtime configuration files should
  contain directly consumable values; validation metadata such as types,
  defaults, and numeric bounds normally belongs in the accompanying schema.
  If the request genuinely conflicts with that convention, ask the user to
  clarify rather than silently changing the format.
"""

CLAUDE_USER_SIM_START_TOOL = "mcp__user-sim__start_conversation"
CLAUDE_USER_SIM_SEND_TOOL = "mcp__user-sim__send_message_to_user"

MULTI_TURN_EXECUTION_GUIDANCE = f"""Multi-turn user-simulator rules for Claude Code:

- The task registers an MCP server named `user-sim`. Your first action must be
  `{CLAUDE_USER_SIM_START_TOOL}`. Its JSON `user_message` is authoritative.
- Deliver every response through `{CLAUDE_USER_SIM_SEND_TOOL}`, process the
  returned `user_message`, and continue until `conversation_over` is true.
- Use these exact namespaced tool names. Bare `start_conversation` and
  `send_message_to_user` are not valid Claude Code tools.
- A normal final response does not reach the simulated user. Do not finish the
  run while the conversation is still open.
"""


def merge_claude_code_guidance(
    existing: str | None = None,
    *,
    multi_turn: bool = False,
) -> str:
    """Append PawBench's execution rules without discarding caller guidance."""
    sections = [
        section
        for section in (
            (existing or "").strip(),
            CLAUDE_CODE_EXECUTION_GUIDANCE.strip(),
            MULTI_TURN_EXECUTION_GUIDANCE.strip() if multi_turn else "",
        )
        if section
    ]
    return "\n\n".join(sections)


def quoted_claude_code_guidance(
    existing: str | None = None,
    *,
    multi_turn: bool = False,
) -> str:
    """Return guidance safely quoted for Harbor's shell-built CLI flags."""
    return shlex.quote(merge_claude_code_guidance(existing, multi_turn=multi_turn))
