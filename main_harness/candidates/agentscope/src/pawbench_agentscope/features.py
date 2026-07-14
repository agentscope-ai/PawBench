from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


HARNESS_CORE_ROOT = Path(__file__).resolve().parents[4]
if str(HARNESS_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_CORE_ROOT))

from scripts.feature_taxonomy import (  # noqa: E402
    FEATURE_IDS,
    FEATURES as TAXONOMY_FEATURES,
    H_TO_FEATURES,
    LEGACY_P0_FEATURE_IDS,
    LEGACY_TAXONOMY_VERSION,
    TAXONOMY_VERSION,
)

from pawbench_agentscope.models import (  # noqa: E402
    CompactionResult,
    CompactionStatus,
    FeatureManifest,
    TaskSpec,
    ToolAvailability,
    ToolErrorResult,
)


FEATURES: dict[str, FeatureManifest] = {
    feature_id: FeatureManifest(
        id=feature_id,
        name=entry.name_en,
        layer=entry.layer,
        taxonomy_version=TAXONOMY_VERSION,
        h_codes=list(entry.primary_codes),
        expected_reduce=list(entry.primary_codes),
        risks=[],
    )
    for feature_id, entry in TAXONOMY_FEATURES.items()
}


class FeatureConfig(BaseModel):
    enabled: set[str] = Field(default_factory=set)
    taxonomy_version: str = TAXONOMY_VERSION
    ablation_targets: dict[str, str] = Field(default_factory=dict)
    compaction_limit_chars: int = 12_000
    runtime_timeout_seconds: float = 300.0

    def on(self, feature_id: str) -> bool:
        return feature_id in self.enabled

    def target(self, feature_id: str, default: str | None = None) -> str | None:
        return self.ablation_targets.get(feature_id, default)

    @classmethod
    def none(cls) -> "FeatureConfig":
        return cls(enabled=set())

    @classmethod
    def all_enabled(cls) -> "FeatureConfig":
        return cls(enabled=set(FEATURE_IDS))

    @classmethod
    def controlled_off(cls, feature_id: str, *, target: str | None = None) -> "FeatureConfig":
        if feature_id not in FEATURES:
            raise ValueError(f"Unknown feature id: {feature_id}")
        targets = {feature_id: target} if target else {}
        return cls(enabled=set(FEATURE_IDS) - {feature_id}, ablation_targets=targets)

    def without(self, *feature_ids: str) -> "FeatureConfig":
        return self.model_copy(update={"enabled": self.enabled - set(feature_ids)})

    def validate_known(self) -> None:
        if self.taxonomy_version != TAXONOMY_VERSION:
            raise ValueError(
                f"AgentScope requires taxonomy {TAXONOMY_VERSION}; got {self.taxonomy_version}. "
                f"Historical runs must remain tagged {LEGACY_TAXONOMY_VERSION}."
            )
        unknown = sorted(self.enabled - set(FEATURES))
        if unknown:
            raise ValueError(f"Unknown feature ids: {unknown}")


def emit_feature_events(config: FeatureConfig, trace) -> None:
    config.validate_known()
    trace.append(
        "taxonomy_selected",
        {
            "taxonomy_version": TAXONOMY_VERSION,
            "enabled_features": sorted(config.enabled),
            "ablation_targets": config.ablation_targets,
        },
    )
    for feature_id in sorted(config.enabled):
        trace.append("feature_enabled", FEATURES[feature_id].model_dump())
    for feature_id in sorted(set(FEATURE_IDS) - config.enabled):
        trace.append(
            "feature_controlled_off",
            {
                "id": feature_id,
                "target": config.target(feature_id),
                "taxonomy_version": TAXONOMY_VERSION,
            },
        )


def safe_join(workspace_root: Path, rel_path: str) -> Path:
    root = workspace_root.resolve()
    path = (root / rel_path).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"path escapes workspace: {rel_path}")
    return path


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_workspace(workspace_root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    if not workspace_root.exists():
        return snapshot
    for path in sorted(workspace_root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = str(path.relative_to(workspace_root))
        stat = path.stat()
        snapshot[rel] = {
            "size": stat.st_size,
            "sha256": _file_hash(path),
        }
    return snapshot


def diff_workspace_snapshots(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    before_keys = set(before)
    after_keys = set(after)
    changed = sorted(path for path in before_keys & after_keys if before[path] != after[path])
    return {
        "created": sorted(after_keys - before_keys),
        "deleted": sorted(before_keys - after_keys),
        "changed": changed,
        "before": {path: before[path] for path in sorted(before)},
        "after": {path: after[path] for path in sorted(after)},
    }


def workspace_state_hash(snapshot: dict[str, dict[str, Any]]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def preflight_workspace(task: TaskSpec, workspace_root: Path, *, apply_reset: bool) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "exists": workspace_root.exists(),
        "is_directory": workspace_root.is_dir(),
        "writable": os.access(workspace_root, os.W_OK) if workspace_root.exists() else False,
        "binaries": {name: shutil.which(name) for name in task.required_binaries},
        "env_vars": {name: bool(os.getenv(name)) for name in task.required_env_vars},
    }
    reset_events: list[dict[str, str]] = []
    if apply_reset and task.reset_paths:
        if not task.isolated_workspace:
            reset_events.append({"status": "refused", "reason": "workspace_not_marked_isolated"})
        else:
            for rel_path in task.reset_paths:
                path = safe_join(workspace_root, rel_path)
                if path.is_dir():
                    shutil.rmtree(path)
                    reset_events.append({"path": rel_path, "status": "directory_removed"})
                elif path.exists():
                    path.unlink()
                    reset_events.append({"path": rel_path, "status": "file_removed"})
                else:
                    reset_events.append({"path": rel_path, "status": "already_absent"})
    snapshot = snapshot_workspace(workspace_root)
    checks["reset_events"] = reset_events
    checks["state_hash"] = workspace_state_hash(snapshot)
    checks["ready"] = bool(
        checks["exists"]
        and checks["is_directory"]
        and checks["writable"]
        and all(checks["binaries"].values())
        and all(checks["env_vars"].values())
        and not any(event.get("status") == "refused" for event in reset_events)
    )
    return checks


def discover_context_sources(workspace_root: Path) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for name in ("SKILL.md", "AGENTS.md", "README.md"):
        for path in workspace_root.rglob(name):
            if ".git" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            found.append(
                {
                    "path": str(path.relative_to(workspace_root)),
                    "text": text[:8_000],
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
    return found[:12]


def compact_context(text: str, *, limit: int, enabled: bool) -> CompactionResult:
    if len(text) <= limit:
        return CompactionResult(
            mode="unchanged",
            text=text,
            before_size=len(text),
            after_size=len(text),
        )
    if not enabled:
        truncated = text[:limit]
        return CompactionResult(
            mode="truncated",
            text=truncated,
            before_size=len(text),
            after_size=len(truncated),
        )

    fact_lines = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\b(must|required|never|expected|workspace|artifact|constraint)\b", line, flags=re.I)
    ]
    unique_facts = list(dict.fromkeys(line for line in fact_lines if line))[:24]
    summary_budget = max(400, limit // 4)
    summary = "\n".join(unique_facts)[:summary_budget]
    head_budget = max(200, (limit - len(summary) - 180) * 2 // 3)
    tail_budget = max(200, limit - len(summary) - head_budget - 180)
    marker = "\n\n[COMPACTED CONTEXT: extractive reconstruction]\n"
    reconstructed = text[:head_budget] + marker + summary + "\n[RECENT CONTEXT]\n" + text[-tail_budget:]
    reconstructed = reconstructed[:limit]
    return CompactionResult(
        mode="compacted",
        text=reconstructed,
        before_size=len(text),
        after_size=len(reconstructed),
        summary=summary,
        preserved_fact_hashes=[hashlib.sha256(line.encode("utf-8")).hexdigest() for line in unique_facts],
    )


class PersistentMemoryStore:
    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "records": []}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload.get("records"), list):
            raise ValueError(f"Malformed memory store: {self.path}")
        return payload

    def query(self, query: str, *, limit: int = 4) -> list[dict[str, Any]]:
        records = self._load()["records"]
        terms = set(re.findall(r"[a-z0-9_.-]+", query.lower()))
        ranked: list[tuple[int, dict[str, Any]]] = []
        for record in records:
            haystack = json.dumps(record, ensure_ascii=False).lower()
            score = sum(term in haystack for term in terms)
            if score:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (-item[0], -int(item[1].get("version", 0))))
        return [record for _, record in ranked[:limit]]

    def upsert(self, key: str, value: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self._load()
        records = payload["records"]
        previous = next((record for record in records if record.get("key") == key), None)
        version = int(previous.get("version", 0)) + 1 if previous else 1
        record = {
            "key": key,
            "value": value,
            "version": version,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "metadata": metadata or {},
        }
        payload["records"] = [item for item in records if item.get("key") != key] + [record]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self.path)
        return record


def prepare_prompt(
    task: TaskSpec,
    workspace_root: Path,
    config: FeatureConfig,
    trace,
    *,
    memory_records: list[dict[str, Any]] | None = None,
) -> str:
    lines = [task.instruction]
    source_manifest: list[dict[str, Any]] = [
        {
            "type": "task_instruction",
            "sha256": hashlib.sha256(task.instruction.encode("utf-8")).hexdigest(),
        }
    ]
    if task.required_artifacts:
        artifact_contract = "\n".join(f"- {path}" for path in task.required_artifacts)
        lines += ["", "Required artifacts:", artifact_contract]
        source_manifest.append(
            {
                "type": "artifact_contract",
                "sha256": hashlib.sha256(artifact_contract.encode("utf-8")).hexdigest(),
            }
        )
    if config.on("F1.1"):
        lines += [
            "",
            f"Workspace root: {workspace_root}",
            "Resolve all relative paths under this workspace root.",
        ]
        trace.append("workspace_binding", {"workspace_root": str(workspace_root), "cwd": str(workspace_root)})
    if config.on("F5.1"):
        sources = discover_context_sources(workspace_root)
        for source in sources:
            lines.append(f"\n# {source['path']}\n{source['text']}")
            source_manifest.append({key: source[key] for key in ("path", "sha256")})
    if config.on("F5.2") and memory_records:
        lines.append("\nRetrieved persistent memory:")
        for record in memory_records:
            lines.append(f"- {record['key']}@v{record['version']}: {record['value']}")
            source_manifest.append({"type": "memory", "key": record["key"], "version": record["version"]})
    if config.on("F2.3"):
        lines.append("\nWhen a tool call fails, use its structured status and actionable error before retrying.")
    if config.on("F3.1"):
        lines.append("\nFinish only after producing the requested artifacts; explicitly report completion or abort.")
    if config.on("F4.3"):
        lines.append("\nAn independent verifier will inspect artifacts and may gate acceptance.")

    raw_prompt = "\n".join(lines)
    result = compact_context(
        raw_prompt,
        limit=max(1_000, config.compaction_limit_chars),
        enabled=config.on("F5.3"),
    )
    trace.append(
        "context_assembly",
        {
            "sources": source_manifest,
            "source_order": list(range(len(source_manifest))),
            "before_size": result.before_size,
            "after_size": result.after_size,
            "token_estimate": result.after_size // 4,
        },
    )
    trace.append(
        "compaction_result",
        {
            "mode": result.mode,
            "before_size": result.before_size,
            "after_size": result.after_size,
            "summary": result.summary,
            "preserved_fact_hashes": result.preserved_fact_hashes,
        },
    )
    return result.text


def check_tool_availability(required: list[str], enabled: list[str]) -> ToolAvailability:
    return ToolAvailability(
        enabled_tools=enabled,
        missing_required_tools=[name for name in required if name not in enabled],
    )


def tool_error(tool: str, error_type: str, message: str, *, recoverable: bool = True) -> str:
    return ToolErrorResult(
        tool=tool,
        error_type=error_type,
        message=message,
        recoverable=recoverable,
        suggested_next_action=(
            "Retry with a narrower command, valid relative path, or smaller file scope."
            if recoverable
            else None
        ),
    ).model_dump_json()


def normalize_tool_feedback(error: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"raw_error": error}
    return {
        "ok": False,
        "tool": error.get("tool"),
        "error_type": error.get("state") or error.get("error_type") or "tool_error",
        "message": error.get("message") or json.dumps(error.get("metadata", {}), ensure_ascii=False),
        "recoverable": True,
        "suggested_next_action": "Inspect the status, correct the smallest failing input, and retry once.",
    }


def check_compaction(token_estimate: int, threshold: int = 96_000) -> CompactionStatus:
    return CompactionStatus(
        enabled=True,
        token_estimate=token_estimate,
        threshold=threshold,
        should_compact=token_estimate >= threshold,
    )


def budget_policy(config: FeatureConfig, requested_max_iters: int) -> dict[str, Any]:
    absolute_max_iters = 80
    if config.on("F3.2"):
        max_iters = max(1, min(requested_max_iters, absolute_max_iters))
        tool_result_limit = 20_000
        timeout_seconds = max(1.0, min(config.runtime_timeout_seconds, 900.0))
        mode = "enforced"
    else:
        max_iters = max(1, min(max(requested_max_iters * 2, 40), absolute_max_iters))
        tool_result_limit = 100_000
        timeout_seconds = min(max(config.runtime_timeout_seconds * 2, 600.0), 900.0)
        mode = "enlarged_with_absolute_cap"
    return {
        "mode": mode,
        "max_iters": max_iters,
        "absolute_max_iters": absolute_max_iters,
        "tool_result_limit": tool_result_limit,
        "timeout_seconds": timeout_seconds,
    }


def completion_decision(runtime_summary: dict[str, Any], *, enabled: bool) -> tuple[bool, str]:
    if not enabled:
        return True, "framework_baseline"
    summaries = [runtime_summary]
    if "retry" in runtime_summary:
        summaries = [runtime_summary.get("initial", {}), runtime_summary.get("retry", {})]
    final = summaries[-1]
    if final.get("exceed_max_iters"):
        return False, "max_iters_exceeded"
    if final.get("permission_required"):
        return False, "permission_pause"
    if final.get("timed_out"):
        return False, "runtime_timeout"
    return True, str(final.get("finished_reason") or "reply_end")


def trace_runtime_contracts(
    task: TaskSpec,
    enabled_tools: list[str],
    config: FeatureConfig,
    budget: dict[str, Any],
    trace,
) -> None:
    trace.append(
        "tool_availability",
        {
            **check_tool_availability(task.required_tools, enabled_tools).model_dump(),
            "selected_hidden_tool": config.target("F2.2", "write_file") if not config.on("F2.2") else None,
        },
    )
    trace.append("budget_policy", budget)
    trace.append(
        "isolation_policy",
        {
            "mode": "enhanced" if config.on("F1.3") else "minimal_safety_floor",
            "never_unsandboxed": True,
        },
    )
    trace.append(
        "diagnostic_trace_policy",
        {
            "diagnostic_events_enabled": config.on("F4.1"),
            "outer_audit_always_enabled": True,
        },
    )


def serialize_config(config: FeatureConfig) -> str:
    return config.model_dump_json()


__all__ = [
    "FEATURE_IDS",
    "FEATURES",
    "H_TO_FEATURES",
    "LEGACY_P0_FEATURE_IDS",
    "TAXONOMY_VERSION",
    "FeatureConfig",
    "PersistentMemoryStore",
    "budget_policy",
    "check_compaction",
    "check_tool_availability",
    "compact_context",
    "completion_decision",
    "diff_workspace_snapshots",
    "discover_context_sources",
    "emit_feature_events",
    "normalize_tool_feedback",
    "preflight_workspace",
    "prepare_prompt",
    "safe_join",
    "snapshot_workspace",
    "tool_error",
    "trace_runtime_contracts",
]
