"""Detect real sub-agent delegation in normalized and native agent traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .multi_agent import (
    MultiAgentConfig,
    SUPPORTED_MULTI_AGENT_HARNESSES,
    normalize_harness_name,
    resolve_for_harness,
)


_DELEGATION_TOOLS: dict[str, frozenset[str]] = {
    "claude-code": frozenset({"Task", "SendMessage"}),
    "codex": frozenset({"spawn_agent", "spawn_agents_on_csv"}),
    "openclaw": frozenset({"sessions_spawn"}),
}


def detect_delegation(
    transcript: list[Any],
    harness: str,
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return execution evidence for actual sub-agent tool calls.

    Normalized transcripts are preferred so the same call is not counted again
    from native logs. Native artifacts are used only as a fallback when the
    transcript contains no delegation evidence.
    """
    name = normalize_harness_name(harness)
    tools = _DELEGATION_TOOLS.get(name, frozenset())
    evidence = _evidence_from_objects(transcript, tools, "transcript")

    if not evidence and artifact_dir is not None and tools:
        evidence = _evidence_from_artifacts(Path(artifact_dir), name, tools)

    return {
        "harness": name,
        "delegation_count": len(evidence),
        "delegation_tools": sorted({item["tool"] for item in evidence}),
        "evidence": evidence,
    }


def evaluate_multi_agent_run(
    raw_config: dict[str, Any] | MultiAgentConfig | None,
    harness: str,
    transcript: list[Any],
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve mode, detect delegation, and report forced-mode compliance."""
    if isinstance(raw_config, MultiAgentConfig):
        cfg = raw_config
    else:
        cfg = MultiAgentConfig.from_dict(raw_config)
    cfg = resolve_for_harness(cfg, harness)
    detection = detect_delegation(transcript, harness, artifact_dir)
    forced_violation = (
        cfg.effective_mode == "forced"
        and detection["delegation_count"] == 0
    )
    return {
        "requested_mode": cfg.requested_mode,
        "effective_mode": cfg.effective_mode,
        "supported": (
            normalize_harness_name(harness) in SUPPORTED_MULTI_AGENT_HARNESSES
        ),
        **detection,
        "forced_violation": forced_violation,
    }


def _evidence_from_objects(
    objects: Iterable[Any],
    tools: frozenset[str],
    source: str,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def visit(value: Any, location: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{location}[{index}]")
            return
        if not isinstance(value, dict):
            return

        event_type = str(value.get("type") or "")
        tool_name = value.get("name")
        if event_type in {
            "toolCall", "tool_call", "tool_use", "function_call",
        } and isinstance(tool_name, str) and tool_name in tools:
            key = (tool_name, location)
            if key not in seen:
                seen.add(key)
                evidence.append(
                    {"source": source, "location": location, "tool": tool_name}
                )

        for key, item in value.items():
            visit(item, f"{location}.{key}")

    for index, obj in enumerate(objects):
        visit(obj, f"$[{index}]")
    return evidence


def _evidence_from_artifacts(
    root: Path,
    harness: str,
    tools: frozenset[str],
) -> list[dict[str, Any]]:
    if not root.exists():
        return []

    patterns = {
        "claude-code": (
            "claude-code.txt",
            "sessions/claude-code.txt",
            "sessions/**/*.jsonl",
        ),
        "codex": (
            "codex.txt",
            "sessions/codex.txt",
            "sessions/**/*.jsonl",
        ),
        "openclaw": (
            "openclaw.session.jsonl",
            "sessions/openclaw.session.jsonl",
        ),
    }.get(harness, ())

    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            evidence: list[dict[str, Any]] = []
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line_number, line in enumerate(handle, 1):
                        try:
                            value = json.loads(line)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        found = _evidence_from_objects(
                            [value], tools, str(path)
                        )
                        for item in found:
                            item["line"] = line_number
                        evidence.extend(found)
            except OSError:
                continue
            if evidence:
                return evidence
    return []
