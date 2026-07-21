from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pawbench_agentscope._atomic_io import atomic_write_text, exclusive_path_lock, read_text_no_follow
from pawbench_agentscope._portable_security import redact_sensitive_text, redact_sensitive_value


from pawbench_agentscope._portable_taxonomy import (
    FEATURE_IDS,
    FEATURES as TAXONOMY_FEATURES,
    H_TO_FEATURES,
    LEGACY_P0_FEATURE_IDS,
    LEGACY_TAXONOMY_VERSION,
    TAXONOMY_VERSION,
)

from pawbench_agentscope.models import (
    CompactionResult,
    CompactionStatus,
    FeatureManifest,
    TaskSpec,
    ToolAvailability,
    ToolErrorResult,
)


WORKSPACE_BINDING_PROMPT_MARKER = "Workspace root alias: ."
MAX_MEMORY_STORE_BYTES = 16 * 1024 * 1024
MAX_MEMORY_RECORDS = 10_000
MAX_MEMORY_KEY_CHARS = 512
MAX_MEMORY_VALUE_CHARS = 64 * 1024
MAX_SNAPSHOT_FILE_BYTES = 256 * 1024 * 1024
MAX_WORKSPACE_ENTRIES = 100_000
MAX_SNAPSHOT_HASH_BYTES = 512 * 1024 * 1024


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


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
        unknown = sorted(set(feature_ids) - set(FEATURE_IDS))
        if unknown:
            raise ValueError(f"Unknown feature ids: {unknown}")
        return self.model_copy(update={"enabled": self.enabled - set(feature_ids)})

    def validate_known(self) -> None:
        if self.taxonomy_version != TAXONOMY_VERSION:
            raise ValueError(
                f"AgentScope requires taxonomy {TAXONOMY_VERSION}; got {self.taxonomy_version}. "
                f"Historical runs must remain tagged {LEGACY_TAXONOMY_VERSION}."
            )
        if not isinstance(self.enabled, set) or not all(
            isinstance(feature_id, str) for feature_id in self.enabled
        ):
            raise ValueError("enabled must be a set of Feature IDs")
        unknown = sorted(self.enabled - set(FEATURES))
        if unknown:
            raise ValueError(f"Unknown feature ids: {unknown}")
        if not isinstance(self.ablation_targets, dict) or not all(
            isinstance(feature_id, str) and isinstance(target, str) and target
            for feature_id, target in self.ablation_targets.items()
        ):
            raise ValueError("ablation_targets must map Feature IDs to non-empty strings")
        unknown_targets = sorted(set(self.ablation_targets) - set(FEATURES))
        if unknown_targets:
            raise ValueError(f"Unknown ablation target Feature ids: {unknown_targets}")
        enabled_targets = sorted(set(self.ablation_targets) & self.enabled)
        if enabled_targets:
            raise ValueError(
                "ablation_targets may name only disabled Features: "
                f"{enabled_targets}"
            )
        if (
            isinstance(self.runtime_timeout_seconds, bool)
            or not isinstance(self.runtime_timeout_seconds, (int, float))
            or not math.isfinite(self.runtime_timeout_seconds)
            or self.runtime_timeout_seconds <= 0
        ):
            raise ValueError("runtime_timeout_seconds must be finite and positive")
        if (
            isinstance(self.compaction_limit_chars, bool)
            or not isinstance(self.compaction_limit_chars, int)
            or self.compaction_limit_chars < 1_000
        ):
            raise ValueError("compaction_limit_chars must be an integer of at least 1000")


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


def _path_inside_workspace(workspace_root: Path, path: Path) -> bool:
    root = workspace_root.resolve()
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents


def _bounded_workspace_paths(workspace_root: Path) -> list[Path]:
    root = workspace_root.resolve()
    paths: list[Path] = []

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current, directories, files in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=raise_walk_error,
    ):
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in [*directories, *sorted(files)]:
            paths.append(Path(current) / name)
            if len(paths) > MAX_WORKSPACE_ENTRIES:
                raise ValueError(
                    f"workspace exceeds {MAX_WORKSPACE_ENTRIES} filesystem entries"
                )
    return sorted(paths)


def external_workspace_symlinks(workspace_root: Path) -> list[str]:
    root = workspace_root.resolve()
    if not root.is_dir():
        return []
    return sorted(
        str(path.relative_to(root))
        for path in _bounded_workspace_paths(root)
        if path.is_symlink() and not _path_inside_workspace(root, path)
    )


def _regular_file_receipt(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        return None
    flags = os.O_RDONLY | os.O_NONBLOCK | int(getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    digest = hashlib.sha256()
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            return None
        if metadata.st_size > MAX_SNAPSHOT_FILE_BYTES:
            return {
                "size": metadata.st_size,
                "sha256": None,
                "hash_status": "skipped_too_large",
            }
        observed_size = 0
        while chunk := os.read(fd, 64 * 1024):
            observed_size += len(chunk)
            if observed_size > MAX_SNAPSHOT_FILE_BYTES:
                final_size = os.fstat(fd).st_size
                return {
                    "size": max(final_size, observed_size),
                    "sha256": None,
                    "hash_status": "skipped_too_large",
                }
            digest.update(chunk)
        return {"size": observed_size, "sha256": digest.hexdigest()}
    finally:
        os.close(fd)


def snapshot_workspace(workspace_root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    root = workspace_root.resolve()
    if not root.exists():
        return snapshot
    hashed_bytes = 0
    for path in _bounded_workspace_paths(root):
        if not _path_inside_workspace(root, path):
            continue
        receipt = _regular_file_receipt(path)
        if receipt is not None:
            if receipt.get("sha256") is not None:
                hashed_bytes += int(receipt["size"])
                if hashed_bytes > MAX_SNAPSHOT_HASH_BYTES:
                    raise ValueError(
                        f"workspace snapshot exceeds {MAX_SNAPSHOT_HASH_BYTES} hashed bytes"
                    )
            snapshot[str(path.relative_to(root))] = receipt
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


def _reset_candidate(workspace_root: Path, rel_path: str) -> tuple[Path | None, str | None]:
    """Resolve a reset target without following any user-controlled symlink."""

    if not isinstance(rel_path, str) or not rel_path.strip() or "\x00" in rel_path:
        return None, "invalid_reset_path"
    relative = Path(rel_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None, "path_escapes_workspace"
    root = workspace_root.resolve()
    candidate = root.joinpath(*relative.parts)
    if candidate == root:
        return None, "workspace_root_reset_forbidden"
    cursor = root
    for component in relative.parts[:-1]:
        cursor = cursor / component
        if cursor.is_symlink():
            return None, "symlink_parent_refused"
    try:
        parent = candidate.parent.resolve()
    except (OSError, RuntimeError):
        return None, "path_escapes_workspace"
    if parent != root and root not in parent.parents:
        return None, "path_escapes_workspace"
    if candidate.is_symlink():
        try:
            target = candidate.resolve()
        except (OSError, RuntimeError):
            return None, "path_escapes_workspace"
        if target != root and root not in target.parents:
            return None, "path_escapes_workspace"
    return candidate, None


def preflight_workspace(task: TaskSpec, workspace_root: Path, *, apply_reset: bool) -> dict[str, Any]:
    traversal_error: str | None = None
    try:
        external_symlinks = external_workspace_symlinks(workspace_root)
    except (OSError, ValueError) as exc:
        external_symlinks = []
        traversal_error = str(exc)
    checks: dict[str, Any] = {
        "exists": workspace_root.exists(),
        "is_directory": workspace_root.is_dir(),
        "writable": os.access(workspace_root, os.W_OK) if workspace_root.exists() else False,
        "binaries": {name: shutil.which(name) for name in task.required_binaries},
        "env_vars": {name: bool(os.getenv(name)) for name in task.required_env_vars},
        "external_symlinks": external_symlinks,
        "workspace_scan_error": traversal_error,
    }
    reset_events: list[dict[str, str]] = []
    if apply_reset and task.reset_paths:
        if not task.isolated_workspace:
            reset_events.append({"status": "refused", "reason": "workspace_not_marked_isolated"})
        else:
            for rel_path in task.reset_paths:
                path, refusal = _reset_candidate(workspace_root, rel_path)
                if path is None:
                    reset_events.append(
                        {"path": rel_path, "status": "refused", "reason": str(refusal)}
                    )
                    continue
                if path.is_symlink():
                    path.unlink()
                    reset_events.append({"path": rel_path, "status": "symlink_removed"})
                elif path.is_dir():
                    shutil.rmtree(path)
                    reset_events.append({"path": rel_path, "status": "directory_removed"})
                elif path.exists():
                    path.unlink()
                    reset_events.append({"path": rel_path, "status": "file_removed"})
                else:
                    reset_events.append({"path": rel_path, "status": "already_absent"})
    snapshot_error: str | None = None
    try:
        snapshot = snapshot_workspace(workspace_root)
    except (OSError, ValueError) as exc:
        snapshot = {}
        snapshot_error = str(exc)
    checks["reset_events"] = reset_events
    checks["state_hash"] = workspace_state_hash(snapshot)
    checks["snapshot_error"] = snapshot_error
    checks["ready"] = bool(
        checks["exists"]
        and checks["is_directory"]
        and checks["writable"]
        and all(checks["binaries"].values())
        and all(checks["env_vars"].values())
        and not checks["external_symlinks"]
        and not checks["workspace_scan_error"]
        and not checks["snapshot_error"]
        and not any(event.get("status") == "refused" for event in reset_events)
    )
    return checks


def discover_context_sources(workspace_root: Path) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    root = workspace_root.resolve()
    paths = _bounded_workspace_paths(root)
    for name in ("SKILL.md", "AGENTS.md", "README.md"):
        for path in paths:
            if path.name != name or not _path_inside_workspace(root, path):
                continue
            resolved = path.resolve()
            flags = os.O_RDONLY | os.O_NONBLOCK | int(getattr(os, "O_NOFOLLOW", 0))
            try:
                fd = os.open(resolved, flags)
            except OSError:
                continue
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                text = os.read(fd, 32_769).decode("utf-8", errors="replace")[:8_000]
            finally:
                os.close(fd)
            found.append(
                {
                    "path": str(path.relative_to(root)),
                    "text": text,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
            if len(found) >= 12:
                return found
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
        if self.path.is_symlink():
            raise ValueError(f"Memory store must not be a symlink: {self.path}")
        if not self.path.exists():
            return {"schema_version": 1, "records": []}
        source_text = read_text_no_follow(
            self.path,
            max_bytes=MAX_MEMORY_STORE_BYTES,
        )
        try:
            payload = json.loads(
                source_text,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON number: {value}")
                ),
            )
        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ValueError(f"Malformed memory store: {self.path}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError(f"Malformed memory store: {self.path}")
        records = payload.get("records")
        if not isinstance(records, list) or len(records) > MAX_MEMORY_RECORDS:
            raise ValueError(f"Malformed memory store: {self.path}")
        observed_keys: set[str] = set()
        for index, record in enumerate(records):
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("key"), str)
                or not record["key"]
                or len(record["key"]) > MAX_MEMORY_KEY_CHARS
                or not isinstance(record.get("value"), str)
                or len(record["value"]) > MAX_MEMORY_VALUE_CHARS
                or isinstance(record.get("version"), bool)
                or not isinstance(record.get("version"), int)
                or record["version"] < 1
                or not isinstance(record.get("metadata", {}), dict)
                or not isinstance(record.get("updated_at"), str)
                or not record["updated_at"]
            ):
                raise ValueError(f"Malformed memory store: {self.path}")
            try:
                datetime.fromisoformat(record["updated_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"Malformed memory store: {self.path}") from exc
            safe_record = redact_sensitive_value(record)
            if not isinstance(safe_record, dict) or safe_record["key"] in observed_keys:
                raise ValueError(f"Malformed memory store: {self.path}")
            records[index] = safe_record
            observed_keys.add(safe_record["key"])
        return payload

    def query(self, query: str, *, limit: int = 4) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise ValueError("memory query must be a string")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("memory query limit must be an integer in [1, 100]")
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
        if not isinstance(key, str) or not key or len(key) > MAX_MEMORY_KEY_CHARS:
            raise ValueError(f"memory key must contain 1 to {MAX_MEMORY_KEY_CHARS} characters")
        if not isinstance(value, str) or len(value) > MAX_MEMORY_VALUE_CHARS:
            raise ValueError(f"memory value must contain at most {MAX_MEMORY_VALUE_CHARS} characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("memory metadata must be an object")
        try:
            json.dumps(metadata or {}, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("memory metadata must be finite JSON data") from exc
        safe_metadata = redact_sensitive_value(metadata or {})
        if not isinstance(safe_metadata, dict):
            raise ValueError("memory metadata must be an object")
        safe_key = redact_sensitive_text(key)
        safe_value = redact_sensitive_text(value)
        with exclusive_path_lock(self.path):
            payload = self._load()
            records = payload["records"]
            previous = next(
                (record for record in records if record.get("key") == safe_key),
                None,
            )
            if previous is None and len(records) >= MAX_MEMORY_RECORDS:
                raise ValueError(f"memory store cannot exceed {MAX_MEMORY_RECORDS} records")
            version = int(previous.get("version", 0)) + 1 if previous else 1
            record = {
                "key": safe_key,
                "value": safe_value,
                "version": version,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "metadata": safe_metadata,
            }
            payload["records"] = [
                item for item in records if item.get("key") != safe_key
            ] + [record]
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ) + "\n"
            if len(serialized.encode("utf-8")) > MAX_MEMORY_STORE_BYTES:
                raise ValueError(f"memory store cannot exceed {MAX_MEMORY_STORE_BYTES} bytes")
            atomic_write_text(
                self.path,
                serialized,
            )
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
            WORKSPACE_BINDING_PROMPT_MARKER,
            "Use task-relative paths; the harness binds them under this workspace root.",
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
    if (
        isinstance(requested_max_iters, bool)
        or not isinstance(requested_max_iters, int)
        or requested_max_iters < 1
    ):
        raise ValueError("requested_max_iters must be a positive integer")
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
    if final.get("finished_reason") == "interrupted":
        return False, "interrupted"
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
            "workspace_guard": "AgentScope middleware plus sanitized subprocess environment",
            "os_level_sandbox_enforced_here": False,
            "strong_containment_owner": "Harbor container and network policy",
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
    "WORKSPACE_BINDING_PROMPT_MARKER",
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
