"""Portable Harbor-facing boundary for the Harness-core AgentScope runtime.

This module intentionally does not depend on Harbor.  It accepts the filesystem
contract used by a Harbor agent container, delegates execution to the existing
AgentScope runner, and emits the files a thin Harbor ``BaseAgent`` wrapper needs:

* ``result.json`` -- stable execution/result metadata;
* ``harness-core-trace.jsonl`` -- the native feature-aware Harness trace;
* ``trajectory.json`` -- an ATIF-v1.7 projection for Harbor and graders.

The Harbor registry class remains an integration concern.  Keeping that class
outside Harness-core lets the runtime run locally, in PawBench, or in Harbor
without changing Feature semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field

from pawbench_agentscope._atomic_io import atomic_write_text
from pawbench_agentscope._portable_security import redact_sensitive_text, redact_sensitive_value

from pawbench_agentscope.features import (
    FEATURE_IDS,
    FeatureConfig,
    workspace_state_hash,
)
from pawbench_agentscope.error_codes import classify_bridge_error
from pawbench_agentscope.models import RunResult, TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import (
    dashscope_model_from_env,
    run_task_sync,
)
from pawbench_agentscope.trajectory_audit import load_native_trace


BRIDGE_SCHEMA_VERSION = "harness-core-harbor-bridge/v1"
RESULT_SCHEMA_VERSION = "harness-core-harbor-result/v1"
PROVENANCE_SCHEMA_VERSION = "harness-core-provenance/v1"
ATIF_SCHEMA_VERSION = "ATIF-v1.7"
AGENT_NAME = "agentscope-lab"
AGENT_VERSION = "0.2.0"
DEFAULT_HARBOR_WORKSPACE_ROOTS = (
    PurePosixPath("/app"),
    PurePosixPath("/home/node/workspace"),
    PurePosixPath("/workspace"),
)
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_INSTRUCTION_BYTES = 512 * 1024
MAX_TASK_TOML_BYTES = 1024 * 1024
MAX_ARTIFACT_COUNT = 1024
MAX_ARTIFACT_PATH_CHARS = 4096
MAX_MODEL_NAME_BYTES = 256
MAX_RECEIPT_HASH_BYTES = 256 * 1024 * 1024
MAX_RESULT_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_TRAJECTORY_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_PROVENANCE_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_ERROR_MESSAGE_CHARS = 16_000


class HarborBridgeResult(BaseModel):
    """Stable result envelope consumed by a Harbor wrapper or local runner."""

    schema_version: str = RESULT_SCHEMA_VERSION
    task_id: str
    success: bool = True
    accepted: bool
    completion_ok: bool
    verification_gated: bool
    verifier: dict[str, Any]
    agent: dict[str, Any]
    taxonomy_version: str
    enabled_features: list[str] = Field(default_factory=list)
    disabled_features: list[str] = Field(default_factory=list)
    final_text: str = ""
    event_count: int = 0
    runtime_summary: dict[str, Any] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HarborTaskContract:
    """TaskSpec plus the Harbor fields that are not part of TaskSpec."""

    task: TaskSpec
    timeout_seconds: float
    source_artifacts: tuple[str, ...]
    skipped_log_artifacts: tuple[str, ...]


class _ReceiptTooLarge(ValueError):
    pass


class _BridgeOutputError(RuntimeError):
    pass


def _bounded_redacted_error(value: Any) -> str:
    text = redact_sensitive_text(str(value))
    if len(text) <= MAX_ERROR_MESSAGE_CHARS:
        return text
    half = (MAX_ERROR_MESSAGE_CHARS - 48) // 2
    return f"{text[:half]}...[truncated bridge error]...{text[-half:]}"


def _atomic_json(
    path: Path,
    payload: Any,
    *,
    maximum_bytes: int | None = None,
) -> None:
    safe_payload = redact_sensitive_value(payload)
    try:
        serialized = (
            json.dumps(
                safe_payload,
                ensure_ascii=False,
                indent=2,
                default=str,
                allow_nan=False,
            )
            + "\n"
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise _BridgeOutputError(
            f"could not serialize standard JSON output: {path.name}"
        ) from exc
    if maximum_bytes is not None and len(serialized.encode("utf-8")) > maximum_bytes:
        raise _BridgeOutputError(f"JSON output exceeds {maximum_bytes} bytes: {path.name}")
    atomic_write_text(path, serialized)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows, _ = load_native_trace(path)
    return rows


def _logs_directory(value: str | Path, *, create: bool) -> Path:
    requested = Path(value).expanduser()
    if requested.is_symlink():
        raise ValueError(f"logs directory must not be a symlink: {requested}")
    resolved = requested.resolve()
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    if not resolved.is_dir():
        raise ValueError(f"logs path must be a directory: {requested}")
    return resolved


def _validate_workspace_logs(workspace_root: str | Path, logs_dir: str | Path) -> tuple[Path, Path]:
    """Resolve and require separate task-state and bridge-output trees."""

    workspace = Path(workspace_root).expanduser().resolve()
    requested_logs = Path(logs_dir).expanduser()
    if requested_logs.is_symlink():
        raise ValueError(f"logs directory must not be a symlink: {requested_logs}")
    logs = requested_logs.resolve()
    if logs == workspace or logs in workspace.parents or workspace in logs.parents:
        raise ValueError("Harbor logs and task workspace must be disjoint")
    return workspace, logs


def _safe_runtime_context(logs_dir: str | Path) -> dict[str, Any]:
    try:
        logs = _logs_directory(logs_dir, create=False)
    except (OSError, ValueError):
        return {}
    return _last_runtime_error(logs / "harness-core-trace.jsonl")


def _last_runtime_error(path: Path) -> dict[str, Any]:
    try:
        rows = _read_jsonl(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    for row in reversed(rows):
        if row.get("type") == "runtime_error" and isinstance(row.get("payload"), Mapping):
            return dict(row["payload"])
    return {}


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_receipt(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    if path.is_symlink():
        raise ValueError(f"receipt file must not be a symlink: {path}")
    flags = os.O_RDONLY | os.O_NONBLOCK | int(getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"receipt path must be a regular file: {path}")
        if metadata.st_size > MAX_RECEIPT_HASH_BYTES:
            raise _ReceiptTooLarge(
                f"receipt file exceeds {MAX_RECEIPT_HASH_BYTES} bytes: {path}"
            )
        observed_size = 0
        while chunk := os.read(fd, 64 * 1024):
            observed_size += len(chunk)
            if observed_size > MAX_RECEIPT_HASH_BYTES:
                raise _ReceiptTooLarge(
                    f"receipt file exceeds {MAX_RECEIPT_HASH_BYTES} bytes: {path}"
                )
            digest.update(chunk)
    finally:
        os.close(fd)
    return {
        "path": str(path),
        "size": observed_size,
        "sha256": digest.hexdigest(),
    }


def _required_artifact_receipts(
    workspace: Path,
    required_artifacts: Sequence[str],
) -> dict[str, dict[str, Any]]:
    receipts: dict[str, dict[str, Any]] = {}
    root = workspace.resolve()
    for relative in sorted(set(required_artifacts)):
        lexical_path = root / relative
        if lexical_path.is_symlink():
            receipts[relative] = {"path": relative, "exists": False}
            continue
        try:
            parent = lexical_path.parent.resolve()
        except (OSError, RuntimeError):
            parent = None
        if parent is None or (parent != root and root not in parent.parents):
            receipts[relative] = {"path": relative, "exists": False}
            continue
        path = lexical_path
        try:
            receipt = _file_receipt(path)
        except _ReceiptTooLarge:
            raise
        except (FileNotFoundError, IsADirectoryError, OSError, ValueError):
            receipt = None
        if receipt is not None:
            receipt["path"] = relative
            receipts[relative] = receipt
        else:
            receipts[relative] = {
                "path": relative,
                "exists": False,
            }
    return receipts


def _trace_state_hashes(trace_rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for row in trace_rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if row.get("type") == "preflight_result" and isinstance(payload.get("state_hash"), str):
            hashes["before"] = str(payload["state_hash"])
        elif row.get("type") == "state_artifact_delta":
            after = payload.get("after")
            if isinstance(after, Mapping):
                hashes["after"] = workspace_state_hash(dict(after))
    return hashes


def _provenance_payload(
    *,
    contract: HarborTaskContract,
    feature_config: FeatureConfig,
    run_result: RunResult,
    model_name: str,
    max_iters: int,
    trace_path: Path,
    trajectory_path: Path,
    trace_rows: Sequence[Mapping[str, Any]],
    workspace: Path,
) -> dict[str, Any]:
    feature_payload = {
        "taxonomy_version": feature_config.taxonomy_version,
        "enabled": sorted(feature_config.enabled),
        "disabled": sorted(set(FEATURE_IDS) - set(feature_config.enabled)),
        "ablation_targets": dict(sorted(feature_config.ablation_targets.items())),
        "runtime_timeout_seconds": feature_config.runtime_timeout_seconds,
        "compaction_limit_chars": feature_config.compaction_limit_chars,
        "max_iters": max_iters,
    }
    task_payload = {
        "task_id": contract.task.task_id,
        "instruction": contract.task.instruction,
        "required_artifacts": contract.task.required_artifacts,
        "required_tools": contract.task.required_tools,
        "hidden_contract": contract.task.hidden_contract,
        "source_artifacts": contract.source_artifacts,
    }
    return redact_sensitive_value(
        {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "task_id": contract.task.task_id,
            "run_id": run_result.run_id,
            "agent": {
                "name": AGENT_NAME,
                "version": AGENT_VERSION,
                "runtime": "AgentScope",
                "model_name": model_name,
            },
            "input_hashes": {
                "instruction_sha256": hashlib.sha256(
                    contract.task.instruction.encode("utf-8")
                ).hexdigest(),
                "task_contract_sha256": _canonical_hash(task_payload),
                "feature_config_sha256": _canonical_hash(feature_payload),
            },
            "feature_config": feature_payload,
            "workspace_state_hashes": _trace_state_hashes(trace_rows),
            "required_artifacts": _required_artifact_receipts(
                workspace,
                contract.task.required_artifacts,
            ),
            "outputs": {
                "native_trace": _file_receipt(trace_path),
                "trajectory": _file_receipt(trajectory_path),
            },
            "result": {
                "accepted": run_result.accepted,
                "completion_ok": run_result.completion_ok,
                "verifier_ok": run_result.verifier.ok,
                "event_count": run_result.event_count,
            },
        }
    )


def _artifact_source(value: Any) -> str:
    if isinstance(value, str):
        source = value
    elif isinstance(value, Mapping) and isinstance(value.get("source"), str):
        source = str(value["source"])
    else:
        raise ValueError(
            f"unsupported Harbor artifact entry type: {type(value).__name__}"
        )
    if not source.strip():
        raise ValueError("Harbor artifact source cannot be empty")
    source = source.strip()
    if len(source) > MAX_ARTIFACT_PATH_CHARS or any(
        ord(character) < 32 or ord(character) == 127 for character in source
    ):
        raise ValueError("Harbor artifact source is too long or contains control characters")
    return source


def normalize_harbor_artifact(
    value: Any,
    *,
    workspace_root: Path,
    known_workspace_roots: Sequence[PurePosixPath] = DEFAULT_HARBOR_WORKSPACE_ROOTS,
) -> str | None:
    """Map a Harbor artifact source to a safe path relative to ``workspace_root``.

    ``/logs/agent`` artifacts belong to the bridge output channel and therefore
    are deliberately excluded from the AgentScope workspace verifier.
    """

    source = _artifact_source(value).replace("\\", "/")
    pure = PurePosixPath(source)
    if pure.is_absolute() and (
        pure == PurePosixPath("/logs/agent")
        or PurePosixPath("/logs/agent") in pure.parents
    ):
        return None

    relative: PurePosixPath | None = None
    if not pure.is_absolute():
        relative = pure
    else:
        actual_root = PurePosixPath(workspace_root.as_posix())
        for root in (actual_root, *known_workspace_roots):
            try:
                relative = pure.relative_to(root)
                break
            except ValueError:
                continue
    if relative is None:
        raise ValueError(
            "Harbor artifact must be relative to the configured workspace or one "
            f"of {[str(root) for root in known_workspace_roots]}: {source}"
        )
    if str(relative) in {"", "."} or ".." in relative.parts:
        raise ValueError(f"Harbor artifact is not a safe file path: {source}")

    candidate = (workspace_root.resolve() / Path(*relative.parts)).resolve()
    root = workspace_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Harbor artifact escapes workspace: {source}")
    return relative.as_posix()


def _read_bounded_task_file(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
    allowed_root: Path | None = None,
) -> bytes:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    resolved = path.resolve()
    if allowed_root is not None:
        root = allowed_root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"{label} escapes task root: {path}")
    flags = os.O_RDONLY | os.O_NONBLOCK | int(getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if path.is_symlink():
            raise ValueError(f"{label} must not be a symlink: {path}") from exc
        raise
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} must be a regular file: {path}")
        if metadata.st_size > maximum_bytes:
            raise ValueError(f"{label} exceeds {maximum_bytes} bytes")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if len(data) > maximum_bytes:
        raise ValueError(f"{label} exceeds {maximum_bytes} bytes")
    return data


def _decode_utf8(data: bytes, *, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc


def _load_task_toml(task_root: Path | None) -> dict[str, Any]:
    if task_root is None:
        return {}
    task_toml = task_root / "task.toml"
    if not task_toml.is_file():
        return {}
    data = _read_bounded_task_file(
        task_toml,
        maximum_bytes=MAX_TASK_TOML_BYTES,
        label="task.toml",
        allowed_root=task_root,
    )
    try:
        value = tomllib.loads(_decode_utf8(data, label="task.toml"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"task.toml is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"task.toml must contain one table: {task_toml}")
    return value


def _task_id(task_root: Path | None, task_toml: Mapping[str, Any], explicit: str | None) -> str:
    value = explicit
    if value is None:
        task_table = task_toml.get("task")
        if isinstance(task_table, Mapping) and isinstance(task_table.get("name"), str):
            value = str(task_table["name"]).rsplit("/", 1)[-1]
    if value is None and task_root is not None:
        value = task_root.name
    if value is None:
        raise ValueError("task_id is required when no task root is provided")
    if not _TASK_ID_PATTERN.fullmatch(value):
        raise ValueError("task_id must be one safe path component")
    return value


def _instruction(task_root: Path | None, explicit: str | None) -> str:
    if isinstance(explicit, str) and explicit.strip():
        value = explicit.strip()
        if len(value.encode("utf-8")) > MAX_INSTRUCTION_BYTES:
            raise ValueError(f"task instruction exceeds {MAX_INSTRUCTION_BYTES} bytes")
        return value
    if task_root is not None:
        path = task_root / "instruction.md"
        if path.is_file():
            value = _decode_utf8(
                _read_bounded_task_file(
                    path,
                    maximum_bytes=MAX_INSTRUCTION_BYTES,
                    label="instruction.md",
                    allowed_root=task_root,
                ),
                label="instruction.md",
            )
            if value.strip():
                return value.strip()
    raise ValueError("task instruction is required")


def load_harbor_task_contract(
    *,
    workspace_root: str | Path,
    task_root: str | Path | None = None,
    task_id: str | None = None,
    instruction: str | None = None,
    required_artifacts: Sequence[Any] | None = None,
) -> HarborTaskContract:
    """Load the portable subset of a Harbor task into an AgentScope TaskSpec."""

    workspace = Path(workspace_root).expanduser().resolve()
    root = Path(task_root).expanduser().resolve() if task_root is not None else None
    task_toml = _load_task_toml(root)
    raw_artifacts: Sequence[Any]
    if isinstance(required_artifacts, (str, bytes)):
        raise ValueError("required_artifacts must be a sequence, not one string")
    if required_artifacts is not None:
        raw_artifacts = required_artifacts
    else:
        configured = task_toml.get("artifacts", [])
        if not isinstance(configured, list):
            raise ValueError("task.toml artifacts must be an array")
        raw_artifacts = configured
    if len(raw_artifacts) > MAX_ARTIFACT_COUNT:
        raise ValueError(f"Harbor task has more than {MAX_ARTIFACT_COUNT} artifacts")

    normalized: list[str] = []
    source_artifacts: list[str] = []
    skipped_logs: list[str] = []
    for raw in raw_artifacts:
        source = _artifact_source(raw)
        source_artifacts.append(source)
        relative = normalize_harbor_artifact(raw, workspace_root=workspace)
        if relative is None:
            skipped_logs.append(source)
        elif relative not in normalized:
            normalized.append(relative)

    agent_table = task_toml.get("agent")
    timeout = 300.0
    if agent_table is not None and not isinstance(agent_table, Mapping):
        raise ValueError("task.toml agent must be a table")
    if isinstance(agent_table, Mapping):
        configured_timeout = agent_table.get("timeout_sec")
        if configured_timeout is not None and (
            not isinstance(configured_timeout, (int, float))
            or isinstance(configured_timeout, bool)
        ):
            raise ValueError("agent timeout_sec must be a number")
        if configured_timeout is not None:
            timeout = float(configured_timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("agent timeout must be finite and positive")

    spec = TaskSpec(
        task_id=_task_id(root, task_toml, task_id),
        instruction=_instruction(root, instruction),
        task_dir=workspace,
        required_artifacts=normalized,
        isolated_workspace=True,
        hidden_contract={
            "bridge_schema_version": BRIDGE_SCHEMA_VERSION,
            "harbor_task_root": str(root) if root is not None else None,
            "harbor_artifacts": source_artifacts,
        },
    )
    return HarborTaskContract(
        task=spec,
        timeout_seconds=timeout,
        source_artifacts=tuple(source_artifacts),
        skipped_log_artifacts=tuple(skipped_logs),
    )


def build_feature_config(
    *,
    enabled_features: Sequence[str] | None = None,
    disabled_features: Sequence[str] = (),
    ablation_targets: Mapping[str, str] | None = None,
    runtime_timeout_seconds: float = 300.0,
    compaction_limit_chars: int = 12_000,
) -> FeatureConfig:
    """Build one validated FeatureConfig from bridge-facing switches."""

    known = set(FEATURE_IDS)
    if isinstance(enabled_features, (str, bytes)) or isinstance(
        disabled_features, (str, bytes)
    ):
        raise ValueError("Feature switches must be sequences, not strings")
    enabled_values = list(enabled_features) if enabled_features is not None else None
    disabled_values = list(disabled_features)
    for label, values in (
        ("enabled_features", enabled_values or []),
        ("disabled_features", disabled_values),
    ):
        if any(not isinstance(feature_id, str) for feature_id in values):
            raise ValueError(f"{label} must contain only Feature ID strings")
        if len(values) != len(set(values)):
            raise ValueError(f"{label} must not contain duplicates")
    enabled = set(enabled_values) if enabled_values is not None else set(known)
    disabled = set(disabled_values)
    if enabled_values is not None and enabled & disabled:
        raise ValueError("explicit enabled_features and disabled_features must not overlap")
    unknown = sorted((enabled | disabled | set(ablation_targets or {})) - known)
    if unknown:
        raise ValueError(f"unknown Feature IDs: {unknown}")
    if (
        isinstance(runtime_timeout_seconds, bool)
        or not isinstance(runtime_timeout_seconds, (int, float))
        or not math.isfinite(runtime_timeout_seconds)
        or runtime_timeout_seconds <= 0
    ):
        raise ValueError("runtime_timeout_seconds must be finite and positive")
    if (
        isinstance(compaction_limit_chars, bool)
        or not isinstance(compaction_limit_chars, int)
        or compaction_limit_chars < 1_000
    ):
        raise ValueError("compaction_limit_chars must be an integer of at least 1000")
    config = FeatureConfig(
        enabled=enabled - disabled,
        ablation_targets=dict(ablation_targets or {}),
        runtime_timeout_seconds=float(runtime_timeout_seconds),
        compaction_limit_chars=compaction_limit_chars,
    )
    config.validate_known()
    return config


def _json_arguments(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(
            value,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except (TypeError, ValueError):
        return {"raw": redact_sensitive_text(value)}
    if isinstance(parsed, dict):
        return redact_sensitive_value(parsed)
    return {"value": redact_sensitive_value(parsed)}


def trace_to_atif(
    trace_rows: Sequence[Mapping[str, Any]],
    *,
    task: TaskSpec,
    run_result: RunResult,
    model_name: str,
) -> dict[str, Any]:
    """Project native AgentScope events into a dependency-free ATIF-v1.7 dict."""

    first_timestamp = next(
        (str(row.get("timestamp")) for row in trace_rows if row.get("timestamp")),
        datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "timestamp": first_timestamp,
            "source": "user",
            "message": redact_sensitive_text(task.instruction),
        }
    ]
    current_step: dict[str, Any] | None = None
    call_counts: dict[str, int] = {}
    active_call_ids: dict[str, str] = {}
    call_state: dict[str, dict[str, Any]] = {}

    def ensure_agent_step(timestamp: str) -> dict[str, Any]:
        nonlocal current_step
        if current_step is None:
            current_step = {
                "step_id": len(steps) + 1,
                "timestamp": timestamp,
                "source": "agent",
                "model_name": model_name,
                "message": "",
                "llm_call_count": 1,
            }
            steps.append(current_step)
        return current_step

    for row in trace_rows:
        if row.get("type") != "agentscope_event":
            continue
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        event_type = payload.get("type")
        timestamp = str(row.get("timestamp") or first_timestamp)
        if event_type == "MODEL_CALL_START":
            current_step = None
            ensure_agent_step(timestamp)
        elif event_type == "TEXT_BLOCK_DELTA":
            step = ensure_agent_step(timestamp)
            step["message"] += str(payload.get("delta") or "")
        elif event_type == "TOOL_CALL_START":
            step = ensure_agent_step(timestamp)
            raw_id = str(payload.get("tool_call_id") or f"call-{len(call_state) + 1}")
            occurrence = call_counts.get(raw_id, 0) + 1
            call_counts[raw_id] = occurrence
            canonical_id = raw_id if occurrence == 1 else f"{raw_id}:{occurrence}"
            active_call_ids[raw_id] = canonical_id
            state = {
                "step": step,
                "name": str(payload.get("tool_call_name") or "unknown_tool"),
                "arguments": "",
                "result": "",
                "result_observed": False,
                "result_ended": False,
                "result_state": None,
                "result_metadata": {},
            }
            call_state[canonical_id] = state
            step.setdefault("tool_calls", []).append(
                {
                    "tool_call_id": canonical_id,
                    "function_name": state["name"],
                    "arguments": {},
                }
            )
        elif event_type == "TOOL_CALL_DELTA":
            raw_id = str(payload.get("tool_call_id") or "")
            canonical_id = active_call_ids.get(raw_id)
            if canonical_id in call_state:
                call_state[canonical_id]["arguments"] += str(payload.get("delta") or "")
        elif event_type == "TOOL_RESULT_TEXT_DELTA":
            raw_id = str(payload.get("tool_call_id") or "")
            canonical_id = active_call_ids.get(raw_id)
            if canonical_id in call_state:
                call_state[canonical_id]["result_observed"] = True
                call_state[canonical_id]["result"] += str(payload.get("delta") or "")
        elif event_type == "TOOL_RESULT_START":
            raw_id = str(payload.get("tool_call_id") or "")
            canonical_id = active_call_ids.get(raw_id)
            if canonical_id in call_state:
                call_state[canonical_id]["result_observed"] = True
        elif event_type == "TOOL_RESULT_END":
            raw_id = str(payload.get("tool_call_id") or "")
            canonical_id = active_call_ids.get(raw_id)
            if canonical_id in call_state:
                state = call_state[canonical_id]
                state["result_observed"] = True
                state["result_ended"] = True
                state["result_state"] = payload.get("state")
                metadata = payload.get("metadata")
                state["result_metadata"] = metadata if isinstance(metadata, Mapping) else {}

    for canonical_id, state in call_state.items():
        step = state["step"]
        for tool_call in step.get("tool_calls", []):
            if tool_call["tool_call_id"] == canonical_id:
                tool_call["arguments"] = _json_arguments(state["arguments"])
                break
        if state["result_ended"]:
            projection_status = "complete"
            result_text = state["result"] or str(state["result_state"] or "completed")
        elif state["result_observed"]:
            projection_status = "incomplete"
            result_text = state["result"] or "[tool result started but did not finish]"
        else:
            projection_status = "missing"
            result_text = "[tool result missing from native trace]"
        observation_result: dict[str, Any] = {
            "source_call_id": canonical_id,
            "content": redact_sensitive_text(result_text),
        }
        extra = {
            "state": state["result_state"],
            "metadata": redact_sensitive_value(state["result_metadata"]),
        }
        if projection_status != "complete":
            extra["trace_projection_status"] = projection_status
        if any(value not in (None, {}, []) for value in extra.values()):
            observation_result["extra"] = extra
        step.setdefault("observation", {"results": []})["results"].append(observation_result)

    if len(steps) == 1:
        steps.append(
            {
                "step_id": 2,
                "timestamp": first_timestamp,
                "source": "agent",
                "model_name": model_name,
                "message": redact_sensitive_text(run_result.final_text),
                "llm_call_count": int(
                    run_result.runtime_summary.get("model_calls", 0)
                    if isinstance(run_result.runtime_summary, Mapping)
                    else 0
                ),
            }
        )
    elif not any(step.get("message") for step in steps if step.get("source") == "agent"):
        steps[-1]["message"] = redact_sensitive_text(run_result.final_text)

    return redact_sensitive_value(
        {
            "schema_version": ATIF_SCHEMA_VERSION,
            "session_id": run_result.run_id,
            "trajectory_id": f"{AGENT_NAME}:{run_result.run_id}",
            "agent": {
                "name": AGENT_NAME,
                "version": AGENT_VERSION,
                "model_name": model_name,
                "extra": {
                    "runtime": "AgentScope",
                    "taxonomy_version": run_result.taxonomy_version,
                    "enabled_features": run_result.enabled_features,
                },
            },
            "steps": steps,
            "final_metrics": {
                "total_steps": len(steps),
                "extra": {
                    "agentscope_event_count": run_result.event_count,
                    "accepted": run_result.accepted,
                    "verifier_ok": run_result.verifier.ok,
                },
            },
            "extra": {
                "bridge_schema_version": BRIDGE_SCHEMA_VERSION,
                "task_id": task.task_id,
            },
        }
    )


def _validated_model_name(model: Any, explicit: str | None) -> str:
    value = str(
        explicit
        or getattr(model, "model", None)
        or getattr(model, "model_name", None)
        or "unknown"
    )
    if (
        not value.strip()
        or value != value.strip()
        or len(value.encode("utf-8")) > MAX_MODEL_NAME_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(
            "model_name must be a trimmed control-free string of at most 256 UTF-8 bytes"
        )
    return value


def run_harbor_task(
    contract: HarborTaskContract,
    *,
    workspace_root: str | Path,
    logs_dir: str | Path,
    feature_config: FeatureConfig,
    model: Any,
    model_name: str | None = None,
    max_iters: int = 20,
) -> HarborBridgeResult:
    """Execute one task and materialize the portable Harbor output contract."""

    workspace, _ = _validate_workspace_logs(workspace_root, logs_dir)
    contract_workspace = contract.task.task_dir.expanduser().resolve()
    if contract_workspace != workspace:
        raise ValueError("Harbor task contract workspace does not match workspace_root")
    resolved_model_name = _validated_model_name(model, model_name)
    logs = _logs_directory(logs_dir, create=True)
    trace_path = logs / "harness-core-trace.jsonl"
    trajectory_path = logs / "trajectory.json"
    result_path = logs / "result.json"
    provenance_path = logs / "provenance.json"
    memory_path = logs / "harness-core-memory.json"

    result = run_task_sync(
        contract.task,
        workspace_root=workspace,
        trace_path=trace_path,
        feature_config=feature_config,
        model=model,
        max_iters=max_iters,
        memory_path=memory_path,
    )
    trace_rows = _read_jsonl(trace_path)
    atif = trace_to_atif(
        trace_rows,
        task=contract.task,
        run_result=result,
        model_name=resolved_model_name,
    )
    _atomic_json(
        trajectory_path,
        atif,
        maximum_bytes=MAX_TRAJECTORY_OUTPUT_BYTES,
    )
    provenance = _provenance_payload(
        contract=contract,
        feature_config=feature_config,
        run_result=result,
        model_name=resolved_model_name,
        max_iters=max_iters,
        trace_path=trace_path,
        trajectory_path=trajectory_path,
        trace_rows=trace_rows,
        workspace=workspace,
    )
    _atomic_json(
        provenance_path,
        provenance,
        maximum_bytes=MAX_PROVENANCE_OUTPUT_BYTES,
    )

    envelope = HarborBridgeResult(
        task_id=result.task_id,
        accepted=result.accepted,
        completion_ok=result.completion_ok,
        verification_gated=result.verification_gated,
        verifier=result.verifier.model_dump(mode="json"),
        agent={
            "name": AGENT_NAME,
            "version": AGENT_VERSION,
            "runtime": "AgentScope",
            "model_name": resolved_model_name,
        },
        taxonomy_version=result.taxonomy_version,
        enabled_features=result.enabled_features,
        disabled_features=sorted(set(FEATURE_IDS) - set(result.enabled_features)),
        final_text=result.final_text,
        event_count=result.event_count,
        runtime_summary=result.runtime_summary,
        files={
            "result": str(result_path),
            "trace": str(trace_path),
            "trajectory": str(trajectory_path),
            "provenance": str(provenance_path),
        },
    )
    _atomic_json(
        result_path,
        envelope.model_dump(mode="json"),
        maximum_bytes=MAX_RESULT_OUTPUT_BYTES,
    )
    return envelope


def _targets(values: Sequence[str]) -> dict[str, str]:
    targets: dict[str, str] = {}
    for value in values:
        feature_id, separator, target = value.partition("=")
        if not separator or not feature_id or not target:
            raise ValueError("--ablation-target must use FEATURE_ID=TARGET")
        if feature_id in targets:
            raise ValueError(f"duplicate --ablation-target for {feature_id}")
        targets[feature_id] = target
    return targets


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"{AGENT_NAME} {AGENT_VERSION}")
    parser.add_argument("--task-root", type=Path)
    parser.add_argument("--task-id")
    parser.add_argument("--instruction")
    parser.add_argument("--instruction-file", type=Path)
    parser.add_argument("--workspace-root", type=Path, default=Path(os.getenv("HARNESS_WORKSPACE_ROOT", "/app")))
    parser.add_argument("--logs-dir", type=Path, default=Path(os.getenv("HARNESS_AGENT_LOGS_DIR", "/logs/agent")))
    parser.add_argument("--artifact", action="append", default=None)
    parser.add_argument("--enable-feature", action="append", default=None)
    parser.add_argument("--disable-feature", action="append", default=[])
    parser.add_argument("--ablation-target", action="append", default=[])
    parser.add_argument("--runtime-timeout", type=float)
    parser.add_argument("--compaction-limit", type=int, default=12_000)
    parser.add_argument("--max-iters", type=int, default=20)
    parser.add_argument("--model")
    return parser


def _error_task_id(value: str | None) -> str:
    if value and _TASK_ID_PATTERN.fullmatch(value):
        return value
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "invalid-task")
    normalized = normalized.strip("-._") or "invalid-task"
    normalized = normalized[:128]
    return normalized if _TASK_ID_PATTERN.fullmatch(normalized) else "invalid-task"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _validate_workspace_logs(args.workspace_root, args.logs_dir)
        instruction = args.instruction
        if args.instruction_file is not None:
            if instruction is not None:
                raise ValueError("use only one of --instruction and --instruction-file")
            instruction = _decode_utf8(
                _read_bounded_task_file(
                    args.instruction_file,
                    maximum_bytes=MAX_INSTRUCTION_BYTES,
                    label="instruction file",
                ),
                label="instruction file",
            )
        contract = load_harbor_task_contract(
            workspace_root=args.workspace_root,
            task_root=args.task_root,
            task_id=args.task_id,
            instruction=instruction,
            required_artifacts=args.artifact,
        )
        timeout = args.runtime_timeout if args.runtime_timeout is not None else contract.timeout_seconds
        config = build_feature_config(
            enabled_features=args.enable_feature,
            disabled_features=args.disable_feature,
            ablation_targets=_targets(args.ablation_target),
            runtime_timeout_seconds=timeout,
            compaction_limit_chars=args.compaction_limit,
        )
        model = dashscope_model_from_env(model_name=args.model)
        result = run_harbor_task(
            contract,
            workspace_root=args.workspace_root,
            logs_dir=args.logs_dir,
            feature_config=config,
            model=model,
            model_name=args.model,
            max_iters=args.max_iters,
        )
    except Exception as exc:
        safe_error = _bounded_redacted_error(exc)
        classification = classify_bridge_error(
            error_type=type(exc).__name__,
            error=safe_error,
            runtime_context=_safe_runtime_context(args.logs_dir),
        )
        error = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "task_id": _error_task_id(
                args.task_id or (args.task_root.name if args.task_root else None)
            ),
            "success": False,
            "error_type": type(exc).__name__,
            "error": safe_error,
            **classification.model_dump(mode="json"),
        }
        try:
            _validate_workspace_logs(args.workspace_root, args.logs_dir)
            logs = _logs_directory(args.logs_dir, create=True)
        except (OSError, ValueError):
            logs = None
        if logs is not None:
            _atomic_json(
                logs / "result.json",
                error,
                maximum_bytes=MAX_RESULT_OUTPUT_BYTES,
            )
        print(json.dumps(error, ensure_ascii=False))
        return 1
    print(json.dumps(redact_sensitive_value(result.model_dump(mode="json")), ensure_ascii=False))
    # Task acceptance is reported in result.json; only runtime failure is a CLI error.
    return 0


__all__ = [
    "AGENT_NAME",
    "AGENT_VERSION",
    "ATIF_SCHEMA_VERSION",
    "BRIDGE_SCHEMA_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "HarborBridgeResult",
    "HarborTaskContract",
    "build_feature_config",
    "load_harbor_task_contract",
    "main",
    "normalize_harbor_artifact",
    "run_harbor_task",
    "trace_to_atif",
]


if __name__ == "__main__":
    raise SystemExit(main())
