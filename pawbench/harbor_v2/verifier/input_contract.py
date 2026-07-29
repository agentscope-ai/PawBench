"""Validation for trajectory inputs consumed by OpenJudge."""

from __future__ import annotations

from typing import Any


def validate_atif(payload: Any) -> dict[str, Any]:
    """Return a valid ATIF object or raise a precise contract error."""
    if not isinstance(payload, dict):
        raise ValueError("OpenJudge trajectory must be an ATIF JSON object")
    schema = payload.get("schema_version")
    if not isinstance(schema, str) or not schema.startswith("ATIF-v"):
        raise ValueError(f"Invalid or missing ATIF schema_version: {schema!r}")
    if not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
        raise ValueError("ATIF trajectory requires a non-empty session_id")
    agent = payload.get("agent")
    if not isinstance(agent, dict) or not agent.get("name") or not agent.get("version"):
        raise ValueError("ATIF trajectory requires agent.name and agent.version")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("ATIF trajectory requires at least one step")
    seen_ids: set[int] = set()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError(f"ATIF step {index} must be an object")
        step_id = step.get("step_id")
        if not isinstance(step_id, int) or step_id < 1 or step_id in seen_ids:
            raise ValueError(f"ATIF step {index} has an invalid or duplicate step_id")
        seen_ids.add(step_id)
        if step.get("source") not in {"system", "user", "agent"}:
            raise ValueError(f"ATIF step {step_id} has an invalid source")
        if "message" not in step:
            raise ValueError(f"ATIF step {step_id} is missing message")
    return payload


__all__ = ["validate_atif"]
