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


def merge_claude_code_guidance(existing: str | None = None) -> str:
    """Append PawBench's execution rules without discarding caller guidance."""
    existing = (existing or "").strip()
    if not existing:
        return CLAUDE_CODE_EXECUTION_GUIDANCE.strip()
    return f"{existing}\n\n{CLAUDE_CODE_EXECUTION_GUIDANCE.strip()}"


def quoted_claude_code_guidance(existing: str | None = None) -> str:
    """Return guidance safely quoted for Harbor's shell-built CLI flags."""
    return shlex.quote(merge_claude_code_guidance(existing))
