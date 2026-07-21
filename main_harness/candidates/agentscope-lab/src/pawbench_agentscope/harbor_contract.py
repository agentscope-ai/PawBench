"""Dependency-free validation for the Harness-core ↔ Harbor handoff.

Harbor remains the authority for full ATIF validation. These checks enforce the
portable subset produced by :mod:`pawbench_agentscope.harbor_bridge` so bridge
regressions are caught before Boyin's Harbor-owned wrapper is involved.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from pydantic import ValidationError

from pawbench_agentscope.features import FEATURE_IDS, TAXONOMY_VERSION
from pawbench_agentscope.error_codes import ERROR_CODES, ERROR_SCHEMA_VERSION
from pawbench_agentscope.harbor_bridge import (
    ATIF_SCHEMA_VERSION,
    BRIDGE_SCHEMA_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    HarborBridgeResult,
)
from pawbench_agentscope._atomic_io import read_text_no_follow
from pawbench_agentscope.trajectory_audit import MAX_TRACE_BYTES, load_native_trace


SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_SUCCESS_FILES = {"result", "trace", "trajectory", "provenance"}
ATIF_ROOT_FIELDS = {
    "schema_version",
    "session_id",
    "trajectory_id",
    "agent",
    "steps",
    "final_metrics",
    "extra",
}
ATIF_AGENT_FIELDS = {"name", "version", "model_name", "tool_definitions", "extra"}
ATIF_STEP_FIELDS = {
    "step_id",
    "timestamp",
    "source",
    "model_name",
    "reasoning_effort",
    "message",
    "reasoning_content",
    "tool_calls",
    "observation",
    "metrics",
    "is_copied_context",
    "llm_call_count",
    "extra",
}
ATIF_TOOL_CALL_FIELDS = {"tool_call_id", "function_name", "arguments", "extra"}
ATIF_OBSERVATION_FIELDS = {"results"}
ATIF_RESULT_FIELDS = {"source_call_id", "content", "extra"}
ATIF_FINAL_METRIC_FIELDS = {
    "total_prompt_tokens",
    "total_completion_tokens",
    "total_cached_tokens",
    "total_cost_usd",
    "total_steps",
    "extra",
}
RESULT_SUCCESS_FIELDS = {
    "schema_version",
    "task_id",
    "success",
    "accepted",
    "completion_ok",
    "verification_gated",
    "verifier",
    "agent",
    "taxonomy_version",
    "enabled_features",
    "disabled_features",
    "final_text",
    "event_count",
    "runtime_summary",
    "files",
}
RESULT_ERROR_FIELDS = {
    "schema_version",
    "task_id",
    "success",
    "error_type",
    "error",
    "error_schema_version",
    "error_code",
    "failure_scope",
    "retryable",
    "cause_type",
}
ERROR_METADATA_FIELDS = {
    "error_schema_version",
    "error_code",
    "failure_scope",
    "retryable",
    "cause_type",
}
BRIDGE_AGENT_FIELDS = {"name", "version", "runtime", "model_name"}
VERIFIER_FIELDS = {"ok", "missing_artifacts", "empty_artifacts", "failed_tests"}
PROVENANCE_ROOT_FIELDS = {
    "schema_version",
    "created_at",
    "task_id",
    "run_id",
    "agent",
    "input_hashes",
    "feature_config",
    "workspace_state_hashes",
    "required_artifacts",
    "outputs",
    "result",
}
PROVENANCE_INPUT_HASH_FIELDS = {
    "instruction_sha256",
    "task_contract_sha256",
    "feature_config_sha256",
}
PROVENANCE_FEATURE_FIELDS = {
    "taxonomy_version",
    "enabled",
    "disabled",
    "ablation_targets",
    "runtime_timeout_seconds",
    "compaction_limit_chars",
    "max_iters",
}
PROVENANCE_RESULT_FIELDS = {
    "accepted",
    "completion_ok",
    "verifier_ok",
    "event_count",
}
FILE_RECEIPT_FIELDS = {"path", "size", "sha256"}
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_TRAJECTORY_BYTES = 64 * 1024 * 1024
MAX_PROVENANCE_BYTES = 16 * 1024 * 1024
ERROR_EXPECTATIONS = {
    "HC_CONFIG_INVALID_FEATURE": ("configuration", False),
    "HC_INPUT_CONTRACT_INVALID": ("configuration", False),
    "HC_PREFLIGHT_FAILED": ("harness_runtime", False),
    "HC_PROVIDER_MODEL_NOT_FOUND": ("external_provider", False),
    "HC_PROVIDER_AUTH": ("external_provider", False),
    "HC_PROVIDER_RATE_LIMIT": ("external_provider", True),
    "HC_PROVIDER_UNAVAILABLE": ("external_provider", True),
    "HC_RUNTIME_TIMEOUT": ("harness_runtime", True),
    "HC_RUNTIME_ERROR": ("harness_runtime", False),
}


def _object(value: Any, path: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{path}: expected object")
        return None
    return value


def _iso_timestamp(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value:
        errors.append(f"{path}: expected non-empty ISO 8601 string")
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path}: invalid ISO 8601 timestamp")


def _sha256(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        errors.append(f"{path}: expected lowercase SHA-256")


def _file_receipt_on_disk(path: Path, *, maximum_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    if path.is_symlink():
        raise ValueError(f"file must not be a symlink: {path}")
    flags = os.O_RDONLY | os.O_NONBLOCK | int(getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(os.open(path, flags), "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"file must be regular: {path}")
        if metadata.st_size > maximum_bytes:
            raise ValueError(f"file exceeds {maximum_bytes} bytes: {path}")
        observed_size = 0
        while chunk := handle.read(64 * 1024):
            observed_size += len(chunk)
            if observed_size > maximum_bytes:
                raise ValueError(f"file exceeds {maximum_bytes} bytes: {path}")
            digest.update(chunk)
    return digest.hexdigest(), observed_size


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _read_json_file(path: Path, *, maximum_bytes: int, label: str) -> Any:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if metadata.st_size > maximum_bytes:
        raise ValueError(f"{label} exceeds {maximum_bytes} bytes")
    return json.loads(
        read_text_no_follow(path, max_bytes=maximum_bytes),
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_nonfinite_json,
    )


def _reject_unknown_fields(
    value: Mapping[str, Any],
    allowed: set[str],
    path: str,
    errors: list[str],
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        errors.append(f"{path}: unsupported fields: {unknown}")


def _optional_object(value: Any, path: str, errors: list[str]) -> None:
    if value is not None and not isinstance(value, Mapping):
        errors.append(f"{path}: expected object or null")


def _content(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str):
        return
    if not isinstance(value, list):
        errors.append(f"{path}: expected string or content-part array")
        return
    for index, raw_part in enumerate(value):
        part_path = f"{path}[{index}]"
        part = _object(raw_part, part_path, errors)
        if part is None:
            continue
        _reject_unknown_fields(part, {"type", "text", "source"}, part_path, errors)
        part_type = part.get("type")
        if part_type == "text":
            if not isinstance(part.get("text"), str):
                errors.append(f"{part_path}.text: required string for text content")
            if part.get("source") is not None:
                errors.append(f"{part_path}.source: forbidden for text content")
        elif part_type == "image":
            source = _object(part.get("source"), f"{part_path}.source", errors)
            if part.get("text") is not None:
                errors.append(f"{part_path}.text: forbidden for image content")
            if source is not None:
                _reject_unknown_fields(
                    source, {"media_type", "path"}, f"{part_path}.source", errors
                )
                if source.get("media_type") not in {
                    "image/jpeg",
                    "image/png",
                    "image/gif",
                    "image/webp",
                }:
                    errors.append(f"{part_path}.source.media_type: unsupported image type")
                if not isinstance(source.get("path"), str) or not source["path"]:
                    errors.append(f"{part_path}.source.path: required string")
        else:
            errors.append(f"{part_path}.type: expected 'text' or 'image'")


def _bridge_agent(value: Any, path: str, errors: list[str]) -> Mapping[str, Any] | None:
    agent = _object(value, path, errors)
    if agent is None:
        return None
    _reject_unknown_fields(agent, BRIDGE_AGENT_FIELDS, path, errors)
    expected = {
        "name": "agentscope-lab",
        "runtime": "AgentScope",
    }
    for field, expected_value in expected.items():
        if agent.get(field) != expected_value:
            errors.append(f"{path}.{field}: expected {expected_value!r}")
    for field in ("version", "model_name"):
        if not isinstance(agent.get(field), str) or not agent[field]:
            errors.append(f"{path}.{field}: required string")
    return agent


def _safe_relative_artifact(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = PurePosixPath(value.replace("\\", "/"))
    return not path.is_absolute() and str(path) not in {"", "."} and ".." not in path.parts


def _safe_output_reference(value: Any, expected_name: str) -> bool:
    if not isinstance(value, str) or not value or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return False
    path = PurePosixPath(value.replace("\\", "/"))
    return path.name == expected_name and ".." not in path.parts


def validate_result_contract(payload: Any) -> list[str]:
    """Validate either a completed result or the documented runtime-error envelope."""

    errors: list[str] = []
    value = _object(payload, "result", errors)
    if value is None:
        return errors
    if value.get("schema_version") != RESULT_SCHEMA_VERSION:
        errors.append(
            f"result.schema_version: expected {RESULT_SCHEMA_VERSION!r}"
        )
    task_id = value.get("task_id")
    if not isinstance(task_id, str) or not SAFE_TASK_ID.fullmatch(task_id):
        errors.append("result.task_id: expected one safe path component")

    if value.get("success") is False:
        _reject_unknown_fields(value, RESULT_ERROR_FIELDS, "result", errors)
        for field in ("error_type", "error"):
            if not isinstance(value.get(field), str) or not value[field].strip():
                errors.append(f"result.{field}: required for runtime error")
        if "error_code" in value and value.get("error_code") not in ERROR_CODES:
            errors.append("result.error_code: unknown Harness-core error code")
        if "error_schema_version" in value and value.get("error_schema_version") != ERROR_SCHEMA_VERSION:
            errors.append("result.error_schema_version: unsupported version")
        if "retryable" in value and not isinstance(value.get("retryable"), bool):
            errors.append("result.retryable: expected boolean")
        present_metadata = ERROR_METADATA_FIELDS & set(value)
        if present_metadata and present_metadata != ERROR_METADATA_FIELDS:
            errors.append(
                "result.error_metadata: coded errors require the complete metadata set"
            )
        elif present_metadata == ERROR_METADATA_FIELDS:
            expectation = ERROR_EXPECTATIONS.get(str(value.get("error_code")))
            if expectation is not None:
                expected_scope, expected_retryable = expectation
                if value.get("failure_scope") != expected_scope:
                    errors.append(
                        f"result.failure_scope: expected {expected_scope!r} for error_code"
                    )
                if value.get("retryable") is not expected_retryable:
                    errors.append(
                        f"result.retryable: expected {expected_retryable!r} for error_code"
                    )
            if not isinstance(value.get("cause_type"), str) or not value["cause_type"].strip():
                errors.append("result.cause_type: required non-empty string")
        return errors
    if value.get("success") is not True:
        errors.append("result.success: expected boolean")
        return errors
    _reject_unknown_fields(value, RESULT_SUCCESS_FIELDS, "result", errors)

    try:
        parsed = HarborBridgeResult.model_validate(value)
    except ValidationError as exc:
        errors.extend(
            f"result.{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors()
        )
        return errors
    if parsed.taxonomy_version != TAXONOMY_VERSION:
        errors.append("result.taxonomy_version: does not match active taxonomy")
    for field in ("accepted", "completion_ok", "verification_gated"):
        if not isinstance(value.get(field), bool):
            errors.append(f"result.{field}: expected boolean")
    verifier = _object(value.get("verifier"), "result.verifier", errors)
    if verifier is not None:
        _reject_unknown_fields(verifier, VERIFIER_FIELDS, "result.verifier", errors)
        if not isinstance(verifier.get("ok"), bool):
            errors.append("result.verifier.ok: expected boolean")
        for field in ("missing_artifacts", "empty_artifacts", "failed_tests"):
            entries = verifier.get(field)
            if entries is not None and (
                not isinstance(entries, list)
                or not all(isinstance(entry, str) for entry in entries)
            ):
                errors.append(f"result.verifier.{field}: expected string array")
    _bridge_agent(value.get("agent"), "result.agent", errors)
    if "event_count" in value and (
        isinstance(value.get("event_count"), bool)
        or not isinstance(value.get("event_count"), int)
        or value["event_count"] < 0
    ):
        errors.append("result.event_count: expected nonnegative integer")
    if "runtime_summary" in value and not isinstance(value.get("runtime_summary"), Mapping):
        errors.append("result.runtime_summary: expected object")
    if "final_text" in value and not isinstance(value.get("final_text"), str):
        errors.append("result.final_text: expected string")
    for field in ("enabled_features", "disabled_features"):
        entries = value.get(field)
        if (
            not isinstance(entries, list)
            or any(not isinstance(entry, str) for entry in entries)
            or len(entries) != len(set(entries))
        ):
            errors.append(f"result.{field}: expected duplicate-free string array")
    enabled = set(parsed.enabled_features)
    disabled = set(parsed.disabled_features)
    if enabled & disabled:
        errors.append("result.features: enabled and disabled sets overlap")
    if enabled | disabled != set(FEATURE_IDS):
        errors.append("result.features: enabled plus disabled must cover FEATURE_IDS")
    missing_files = sorted(REQUIRED_SUCCESS_FILES - set(parsed.files))
    if missing_files:
        errors.append(f"result.files: missing {missing_files}")
    elif set(parsed.files) != REQUIRED_SUCCESS_FILES:
        errors.append("result.files: unsupported file keys")
    expected_file_names = {
        "result": "result.json",
        "trace": "harness-core-trace.jsonl",
        "trajectory": "trajectory.json",
        "provenance": "provenance.json",
    }
    for key, file_name in expected_file_names.items():
        path = parsed.files.get(key)
        if not _safe_output_reference(path, file_name):
            errors.append(
                f"result.files.{key}: expected safe reference ending in {file_name!r}"
            )
    verifier_ok = verifier.get("ok") if verifier is not None else None
    if isinstance(verifier_ok, bool):
        expected_accepted = bool(parsed.completion_ok) and (
            verifier_ok if parsed.verification_gated else True
        )
        if parsed.accepted != expected_accepted:
            errors.append("result.accepted: inconsistent completion/verification decision")
    return errors


def validate_atif_v17(payload: Any) -> list[str]:
    """Validate the structural ATIF-v1.7 subset emitted by this bridge."""

    errors: list[str] = []
    value = _object(payload, "trajectory", errors)
    if value is None:
        return errors
    _reject_unknown_fields(value, ATIF_ROOT_FIELDS, "trajectory", errors)
    if value.get("schema_version") != ATIF_SCHEMA_VERSION:
        errors.append(
            f"trajectory.schema_version: expected {ATIF_SCHEMA_VERSION!r}"
        )
    agent = _object(value.get("agent"), "trajectory.agent", errors)
    if agent is not None:
        _reject_unknown_fields(agent, ATIF_AGENT_FIELDS, "trajectory.agent", errors)
        for field in ("name", "version", "model_name"):
            if not isinstance(agent.get(field), str) or not agent[field]:
                errors.append(f"trajectory.agent.{field}: required string")
        if agent.get("name") != "agentscope-lab":
            errors.append("trajectory.agent.name: unexpected bridge producer")
        tool_definitions = agent.get("tool_definitions")
        if tool_definitions is not None and (
            not isinstance(tool_definitions, list)
            or not all(isinstance(item, Mapping) for item in tool_definitions)
        ):
            errors.append("trajectory.agent.tool_definitions: expected object array")
        _optional_object(agent.get("extra"), "trajectory.agent.extra", errors)

    session_id = value.get("session_id")
    if session_id is not None and (not isinstance(session_id, str) or not session_id):
        errors.append("trajectory.session_id: expected non-empty string or null")
    trajectory_id = value.get("trajectory_id")
    if trajectory_id is not None and (
        not isinstance(trajectory_id, str) or not trajectory_id
    ):
        errors.append("trajectory.trajectory_id: expected non-empty string")
    _optional_object(value.get("extra"), "trajectory.extra", errors)

    steps = value.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("trajectory.steps: expected non-empty array")
        return errors
    known_calls: set[str] = set()
    agent_only_fields = {
        "model_name",
        "reasoning_effort",
        "reasoning_content",
        "tool_calls",
        "observation",
        "metrics",
        "llm_call_count",
    }
    for index, raw_step in enumerate(steps, start=1):
        path = f"trajectory.steps[{index - 1}]"
        step = _object(raw_step, path, errors)
        if step is None:
            continue
        _reject_unknown_fields(step, ATIF_STEP_FIELDS, path, errors)
        if step.get("step_id") != index:
            errors.append(f"{path}.step_id: expected {index}")
        _iso_timestamp(step.get("timestamp"), f"{path}.timestamp", errors)
        source = step.get("source")
        if source not in {"system", "user", "agent"}:
            errors.append(f"{path}.source: unsupported source")
        _content(step.get("message"), f"{path}.message", errors)
        if source != "agent":
            unexpected = sorted(agent_only_fields & set(step))
            if unexpected:
                errors.append(f"{path}: agent-only fields on {source} step: {unexpected}")

        model_name = step.get("model_name")
        if model_name is not None and not isinstance(model_name, str):
            errors.append(f"{path}.model_name: expected string or null")
        reasoning_content = step.get("reasoning_content")
        if reasoning_content is not None and not isinstance(reasoning_content, str):
            errors.append(f"{path}.reasoning_content: expected string or null")
        reasoning_effort = step.get("reasoning_effort")
        if reasoning_effort is not None and (
            isinstance(reasoning_effort, bool)
            or not isinstance(reasoning_effort, (str, int, float))
        ):
            errors.append(f"{path}.reasoning_effort: expected string, number, or null")
        elif isinstance(reasoning_effort, (int, float)) and not math.isfinite(reasoning_effort):
            errors.append(f"{path}.reasoning_effort: number must be finite")
        llm_call_count = step.get("llm_call_count")
        if llm_call_count is not None and (
            isinstance(llm_call_count, bool)
            or not isinstance(llm_call_count, int)
            or llm_call_count < 0
        ):
            errors.append(f"{path}.llm_call_count: expected nonnegative integer")
        if source == "agent" and llm_call_count == 0:
            for field in ("metrics", "reasoning_content"):
                if step.get(field) is not None:
                    errors.append(
                        f"{path}.{field}: forbidden when llm_call_count is zero"
                    )
        _optional_object(step.get("metrics"), f"{path}.metrics", errors)
        _optional_object(step.get("extra"), f"{path}.extra", errors)
        is_copied_context = step.get("is_copied_context")
        if is_copied_context is not None and not isinstance(is_copied_context, bool):
            errors.append(f"{path}.is_copied_context: expected boolean or null")

        tool_calls = step.get("tool_calls", [])
        if tool_calls is None:
            tool_calls = []
        if not isinstance(tool_calls, list):
            errors.append(f"{path}.tool_calls: expected array")
            tool_calls = []
        for call_index, raw_call in enumerate(tool_calls):
            call_path = f"{path}.tool_calls[{call_index}]"
            call = _object(raw_call, call_path, errors)
            if call is None:
                continue
            _reject_unknown_fields(call, ATIF_TOOL_CALL_FIELDS, call_path, errors)
            call_id = call.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                errors.append(f"{call_path}.tool_call_id: required string")
            elif call_id in known_calls:
                errors.append(f"{call_path}.tool_call_id: duplicate {call_id!r}")
            else:
                known_calls.add(call_id)
            if not isinstance(call.get("function_name"), str) or not call["function_name"]:
                errors.append(f"{call_path}.function_name: required string")
            if not isinstance(call.get("arguments"), Mapping):
                errors.append(f"{call_path}.arguments: required object")
            _optional_object(call.get("extra"), f"{call_path}.extra", errors)

        observation = step.get("observation")
        if observation is not None:
            observation_value = _object(observation, f"{path}.observation", errors)
            if observation_value is not None:
                _reject_unknown_fields(
                    observation_value,
                    ATIF_OBSERVATION_FIELDS,
                    f"{path}.observation",
                    errors,
                )
            results = observation_value.get("results") if observation_value else None
            if not isinstance(results, list):
                errors.append(f"{path}.observation.results: expected array")
            else:
                for result_index, raw_result in enumerate(results):
                    result_path = f"{path}.observation.results[{result_index}]"
                    result = _object(raw_result, result_path, errors)
                    if result is None:
                        continue
                    _reject_unknown_fields(
                        result, ATIF_RESULT_FIELDS, result_path, errors
                    )
                    source_call_id = result.get("source_call_id")
                    if source_call_id is not None:
                        if not isinstance(source_call_id, str) or not source_call_id:
                            errors.append(f"{result_path}.source_call_id: invalid")
                        else:
                            step_call_ids = {
                                call.get("tool_call_id")
                                for call in tool_calls
                                if isinstance(call, Mapping)
                            }
                            if source_call_id not in step_call_ids:
                                errors.append(
                                    f"{result_path}.source_call_id: unknown "
                                    f"{source_call_id!r} in step {index}"
                                )
                    if "content" not in result:
                        errors.append(f"{result_path}.content: required")
                    elif result.get("content") is not None:
                        _content(result.get("content"), f"{result_path}.content", errors)
                    _optional_object(result.get("extra"), f"{result_path}.extra", errors)
    final_metrics = value.get("final_metrics")
    if isinstance(final_metrics, Mapping):
        _reject_unknown_fields(
            final_metrics,
            ATIF_FINAL_METRIC_FIELDS,
            "trajectory.final_metrics",
            errors,
        )
        total_steps = final_metrics.get("total_steps")
        if total_steps is not None:
            if (
                isinstance(total_steps, bool)
                or not isinstance(total_steps, int)
                or total_steps < 0
            ):
                errors.append(
                    "trajectory.final_metrics.total_steps: expected nonnegative integer"
                )
            elif total_steps != len(steps):
                errors.append("trajectory.final_metrics.total_steps: does not match steps")
        for field in ("total_prompt_tokens", "total_completion_tokens", "total_cached_tokens"):
            value = final_metrics.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                errors.append(f"trajectory.final_metrics.{field}: expected nonnegative integer")
        total_cost = final_metrics.get("total_cost_usd")
        if total_cost is not None and (
            isinstance(total_cost, bool)
            or not isinstance(total_cost, (int, float))
            or not math.isfinite(total_cost)
            or total_cost < 0
        ):
            errors.append("trajectory.final_metrics.total_cost_usd: expected finite nonnegative number")
        _optional_object(
            final_metrics.get("extra"), "trajectory.final_metrics.extra", errors
        )
    elif final_metrics is not None:
        errors.append("trajectory.final_metrics: expected object or null")
    return errors


def validate_provenance_contract(payload: Any) -> list[str]:
    """Validate the reproducibility receipt produced beside every completed run."""

    errors: list[str] = []
    value = _object(payload, "provenance", errors)
    if value is None:
        return errors
    _reject_unknown_fields(value, PROVENANCE_ROOT_FIELDS, "provenance", errors)
    if value.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        errors.append(
            f"provenance.schema_version: expected {PROVENANCE_SCHEMA_VERSION!r}"
        )
    _iso_timestamp(value.get("created_at"), "provenance.created_at", errors)
    task_id = value.get("task_id")
    if not isinstance(task_id, str) or not SAFE_TASK_ID.fullmatch(task_id):
        errors.append("provenance.task_id: expected one safe path component")
    if not isinstance(value.get("run_id"), str) or not value["run_id"]:
        errors.append("provenance.run_id: required string")
    _bridge_agent(value.get("agent"), "provenance.agent", errors)
    hashes = _object(value.get("input_hashes"), "provenance.input_hashes", errors)
    if hashes is not None:
        _reject_unknown_fields(
            hashes,
            PROVENANCE_INPUT_HASH_FIELDS,
            "provenance.input_hashes",
            errors,
        )
        for field in PROVENANCE_INPUT_HASH_FIELDS:
            _sha256(hashes.get(field), f"provenance.input_hashes.{field}", errors)
    config = _object(value.get("feature_config"), "provenance.feature_config", errors)
    if config is not None:
        _reject_unknown_fields(
            config,
            PROVENANCE_FEATURE_FIELDS,
            "provenance.feature_config",
            errors,
        )
        enabled = config.get("enabled")
        disabled = config.get("disabled")
        if not isinstance(enabled, list) or not isinstance(disabled, list):
            errors.append("provenance.feature_config: enabled/disabled must be arrays")
        elif (
            any(not isinstance(feature_id, str) for feature_id in enabled + disabled)
            or len(enabled) != len(set(enabled))
            or len(disabled) != len(set(disabled))
        ):
            errors.append(
                "provenance.feature_config: enabled/disabled must be duplicate-free string arrays"
            )
        elif set(enabled) | set(disabled) != set(FEATURE_IDS) or set(enabled) & set(disabled):
            errors.append("provenance.feature_config: invalid Feature partition")
        if config.get("taxonomy_version") != TAXONOMY_VERSION:
            errors.append("provenance.feature_config.taxonomy_version: mismatch")
        targets = config.get("ablation_targets")
        if not isinstance(targets, Mapping) or any(
            key not in FEATURE_IDS or not isinstance(target, str)
            for key, target in (targets.items() if isinstance(targets, Mapping) else ())
        ):
            errors.append("provenance.feature_config.ablation_targets: invalid mapping")
        elif isinstance(disabled, list) and any(key not in set(disabled) for key in targets):
            errors.append(
                "provenance.feature_config.ablation_targets: targets must be disabled"
            )
        timeout = config.get("runtime_timeout_seconds")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            errors.append("provenance.feature_config.runtime_timeout_seconds: invalid")
        compaction = config.get("compaction_limit_chars")
        if (
            isinstance(compaction, bool)
            or not isinstance(compaction, int)
            or compaction < 1_000
        ):
            errors.append("provenance.feature_config.compaction_limit_chars: invalid")
        max_iters = config.get("max_iters")
        if (
            isinstance(max_iters, bool)
            or not isinstance(max_iters, int)
            or max_iters < 1
        ):
            errors.append("provenance.feature_config.max_iters: invalid")

    state_hashes = value.get("workspace_state_hashes")
    if state_hashes is not None:
        state = _object(state_hashes, "provenance.workspace_state_hashes", errors)
        if state is not None:
            _reject_unknown_fields(
                state,
                {"before", "after"},
                "provenance.workspace_state_hashes",
                errors,
            )
            for field, digest in state.items():
                _sha256(digest, f"provenance.workspace_state_hashes.{field}", errors)

    artifacts = _object(
        value.get("required_artifacts"), "provenance.required_artifacts", errors
    )
    if artifacts is not None:
        for artifact_name, raw_receipt in artifacts.items():
            artifact_path = f"provenance.required_artifacts.{artifact_name}"
            if not _safe_relative_artifact(artifact_name):
                errors.append(f"{artifact_path}: unsafe artifact key")
            receipt = _object(raw_receipt, artifact_path, errors)
            if receipt is None:
                continue
            if receipt.get("path") != artifact_name:
                errors.append(f"{artifact_path}.path: must match artifact key")
            exists = receipt.get("exists")
            if exists is False:
                _reject_unknown_fields(
                    receipt, {"path", "exists"}, artifact_path, errors
                )
            else:
                _reject_unknown_fields(
                    receipt, FILE_RECEIPT_FIELDS, artifact_path, errors
                )
                if (
                    isinstance(receipt.get("size"), bool)
                    or not isinstance(receipt.get("size"), int)
                    or receipt["size"] < 0
                ):
                    errors.append(f"{artifact_path}.size: expected nonnegative integer")
                _sha256(receipt.get("sha256"), f"{artifact_path}.sha256", errors)
    outputs = _object(value.get("outputs"), "provenance.outputs", errors)
    if outputs is not None:
        _reject_unknown_fields(
            outputs, {"native_trace", "trajectory"}, "provenance.outputs", errors
        )
        for name in ("native_trace", "trajectory"):
            receipt = _object(outputs.get(name), f"provenance.outputs.{name}", errors)
            if receipt is None:
                continue
            _reject_unknown_fields(
                receipt,
                FILE_RECEIPT_FIELDS,
                f"provenance.outputs.{name}",
                errors,
            )
            expected_name = (
                "harness-core-trace.jsonl" if name == "native_trace" else "trajectory.json"
            )
            if not _safe_output_reference(receipt.get("path"), expected_name):
                errors.append(
                    f"provenance.outputs.{name}.path: expected safe reference ending in "
                    f"{expected_name!r}"
                )
            _sha256(receipt.get("sha256"), f"provenance.outputs.{name}.sha256", errors)
            if (
                isinstance(receipt.get("size"), bool)
                or not isinstance(receipt.get("size"), int)
                or receipt["size"] < 0
            ):
                errors.append(f"provenance.outputs.{name}.size: expected nonnegative integer")

    result = _object(value.get("result"), "provenance.result", errors)
    if result is not None:
        _reject_unknown_fields(
            result, PROVENANCE_RESULT_FIELDS, "provenance.result", errors
        )
        for field in ("accepted", "completion_ok", "verifier_ok"):
            if not isinstance(result.get(field), bool):
                errors.append(f"provenance.result.{field}: expected boolean")
        event_count = result.get("event_count")
        if (
            isinstance(event_count, bool)
            or not isinstance(event_count, int)
            or event_count < 0
        ):
            errors.append("provenance.result.event_count: expected nonnegative integer")
    return errors


def _validate_cross_file_bindings(
    result: Mapping[str, Any],
    trajectory: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    task_id = result.get("task_id")
    if provenance.get("task_id") != task_id:
        errors.append("cross_file.task_id: result and provenance differ")
    trajectory_extra = trajectory.get("extra")
    if not isinstance(trajectory_extra, Mapping):
        errors.append("cross_file.trajectory.extra: required bridge metadata")
    else:
        if trajectory_extra.get("task_id") != task_id:
            errors.append("cross_file.task_id: result and trajectory differ")
        if trajectory_extra.get("bridge_schema_version") != BRIDGE_SCHEMA_VERSION:
            errors.append("cross_file.bridge_schema_version: unsupported or missing")

    run_id = provenance.get("run_id")
    if trajectory.get("session_id") != run_id:
        errors.append("cross_file.run_id: provenance and trajectory session differ")
    if trajectory.get("trajectory_id") != f"agentscope-lab:{run_id}":
        errors.append("cross_file.trajectory_id: does not bind the provenance run")

    result_agent = result.get("agent")
    provenance_agent = provenance.get("agent")
    trajectory_agent = trajectory.get("agent")
    if all(
        isinstance(agent, Mapping)
        for agent in (result_agent, provenance_agent, trajectory_agent)
    ):
        for field in ("name", "version", "model_name"):
            values = {
                result_agent.get(field),
                provenance_agent.get(field),
                trajectory_agent.get(field),
            }
            if len(values) != 1:
                errors.append(f"cross_file.agent.{field}: values differ")
        trajectory_agent_extra = trajectory_agent.get("extra")
        if not isinstance(trajectory_agent_extra, Mapping):
            errors.append("cross_file.agent.extra: required bridge metadata")
        else:
            expected_extra = {
                "runtime": result_agent.get("runtime"),
                "taxonomy_version": result.get("taxonomy_version"),
                "enabled_features": result.get("enabled_features"),
            }
            for field, expected_value in expected_extra.items():
                if trajectory_agent_extra.get(field) != expected_value:
                    errors.append(
                        f"cross_file.agent.extra.{field}: result value differs"
                    )

    feature_config = provenance.get("feature_config")
    if isinstance(feature_config, Mapping):
        if list(result.get("enabled_features", [])) != list(feature_config.get("enabled", [])):
            errors.append("cross_file.features.enabled: result and provenance differ")
        if list(result.get("disabled_features", [])) != list(feature_config.get("disabled", [])):
            errors.append("cross_file.features.disabled: result and provenance differ")
        if result.get("taxonomy_version") != feature_config.get("taxonomy_version"):
            errors.append("cross_file.taxonomy_version: result and provenance differ")

    result_files = result.get("files")
    provenance_outputs = provenance.get("outputs")
    if isinstance(result_files, Mapping) and isinstance(provenance_outputs, Mapping):
        path_bindings = {
            "trace": "native_trace",
            "trajectory": "trajectory",
        }
        for result_name, provenance_name in path_bindings.items():
            receipt = provenance_outputs.get(provenance_name)
            if (
                isinstance(receipt, Mapping)
                and result_files.get(result_name) != receipt.get("path")
            ):
                errors.append(
                    f"cross_file.outputs.{result_name}: result and provenance paths differ"
                )

    provenance_result = provenance.get("result")
    if isinstance(provenance_result, Mapping):
        bindings = {
            "accepted": result.get("accepted"),
            "completion_ok": result.get("completion_ok"),
            "verifier_ok": (
                result.get("verifier", {}).get("ok")
                if isinstance(result.get("verifier"), Mapping)
                else None
            ),
            "event_count": result.get("event_count", 0),
        }
        for field, expected in bindings.items():
            if provenance_result.get(field) != expected:
                errors.append(f"cross_file.result.{field}: result and provenance differ")

    final_metrics = trajectory.get("final_metrics")
    if not isinstance(final_metrics, Mapping):
        errors.append("cross_file.trajectory.final_metrics: required bridge metrics")
    else:
        final_extra = final_metrics.get("extra")
        if not isinstance(final_extra, Mapping):
            errors.append("cross_file.trajectory.final_metrics.extra: required bridge metrics")
            return errors
        expected = {
            "accepted": result.get("accepted"),
            "verifier_ok": (
                result.get("verifier", {}).get("ok")
                if isinstance(result.get("verifier"), Mapping)
                else None
            ),
            "agentscope_event_count": result.get("event_count", 0),
        }
        for field, expected_value in expected.items():
            if final_extra.get(field) != expected_value:
                errors.append(f"cross_file.trajectory.{field}: result value differs")

    return errors


def _validate_native_trace_bindings(
    rows: list[dict[str, Any]],
    result: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> list[str]:
    """Bind the immutable native trace to the other three handoff files."""

    errors: list[str] = []
    if not rows:
        return ["cross_file.native_trace: expected non-empty trace"]
    expected_task_id = result.get("task_id")
    expected_run_id = provenance.get("run_id")
    previous_event_id: str | None = None
    seen_event_ids: set[str] = set()
    agentscope_event_count = 0
    for index, row in enumerate(rows, start=1):
        path = f"cross_file.native_trace[{index - 1}]"
        if row.get("task_id") != expected_task_id:
            errors.append(f"{path}.task_id: differs from result")
        if row.get("run_id") != expected_run_id:
            errors.append(f"{path}.run_id: differs from provenance")
        if row.get("event_index") != index:
            errors.append(f"{path}.event_index: expected {index}")
        event_id = row.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            errors.append(f"{path}.event_id: required string")
        elif event_id in seen_event_ids:
            errors.append(f"{path}.event_id: duplicate")
        else:
            seen_event_ids.add(event_id)
            if event_id != f"{expected_run_id}:{index}":
                errors.append(f"{path}.event_id: inconsistent with run and index")
        if row.get("parent_event_id") != previous_event_id:
            errors.append(f"{path}.parent_event_id: broken chain")
        previous_event_id = event_id if isinstance(event_id, str) and event_id else None
        if not isinstance(row.get("type"), str) or not row["type"]:
            errors.append(f"{path}.type: required string")
        if not isinstance(row.get("payload"), Mapping):
            errors.append(f"{path}.payload: required object")
        _iso_timestamp(row.get("timestamp"), f"{path}.timestamp", errors)
        if row.get("type") == "agentscope_event":
            agentscope_event_count += 1
    feature_config = provenance.get("feature_config")
    diagnostic_enabled = True
    if isinstance(feature_config, Mapping) and isinstance(feature_config.get("enabled"), list):
        diagnostic_enabled = "F4.1" in feature_config["enabled"]
    if diagnostic_enabled and result.get("event_count", 0) != agentscope_event_count:
        errors.append("cross_file.native_trace.event_count: differs from result")
    if not diagnostic_enabled and agentscope_event_count:
        errors.append(
            "cross_file.native_trace.event_count: F4.1 OFF trace contains diagnostic events"
        )
    return errors


def validate_contract_directory(logs_dir: str | Path) -> dict[str, Any]:
    """Validate a materialized bridge directory and verify provenance hashes."""

    requested_root = Path(logs_dir).expanduser()
    if requested_root.is_symlink():
        return {
            "ok": False,
            "errors": ["logs_dir: must not be a symlink"],
            "logs_dir": str(requested_root),
        }
    root = requested_root.resolve()
    errors: list[str] = []
    result_path = root / "result.json"
    if not result_path.is_file():
        return {"ok": False, "errors": ["result.json: missing"], "logs_dir": str(root)}
    try:
        result = _read_json_file(
            result_path,
            maximum_bytes=MAX_RESULT_BYTES,
            label="result.json",
        )
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "errors": [f"result.json: unreadable: {exc}"],
            "logs_dir": str(root),
        }
    errors.extend(validate_result_contract(result))
    if isinstance(result, Mapping) and result.get("success") is True:
        trajectory_path = root / "trajectory.json"
        provenance_path = root / "provenance.json"
        trace_path = root / "harness-core-trace.jsonl"
        for path in (trajectory_path, provenance_path, trace_path):
            if not path.is_file():
                errors.append(f"{path.name}: missing")
        trajectory: Any = None
        provenance: Any = None
        if trajectory_path.is_file():
            try:
                trajectory = _read_json_file(
                    trajectory_path,
                    maximum_bytes=MAX_TRAJECTORY_BYTES,
                    label="trajectory.json",
                )
                errors.extend(validate_atif_v17(trajectory))
            except (OSError, ValueError) as exc:
                errors.append(f"trajectory.json: unreadable: {exc}")
        if provenance_path.is_file():
            try:
                provenance = _read_json_file(
                    provenance_path,
                    maximum_bytes=MAX_PROVENANCE_BYTES,
                    label="provenance.json",
                )
                errors.extend(validate_provenance_contract(provenance))
            except (OSError, ValueError) as exc:
                errors.append(f"provenance.json: unreadable: {exc}")
        trace_rows: list[dict[str, Any]] | None = None
        if trace_path.is_file():
            try:
                trace_rows, _ = load_native_trace(trace_path)
            except (OSError, ValueError) as exc:
                errors.append(f"harness-core-trace.jsonl: unreadable: {exc}")
        if isinstance(provenance, Mapping):
            outputs = provenance.get("outputs")
            if isinstance(outputs, Mapping):
                for name, path in (("native_trace", trace_path), ("trajectory", trajectory_path)):
                    receipt = outputs.get(name)
                    if isinstance(receipt, Mapping) and path.is_file():
                        maximum_bytes = (
                            MAX_TRACE_BYTES if name == "native_trace" else MAX_TRAJECTORY_BYTES
                        )
                        try:
                            observed_hash, observed_size = _file_receipt_on_disk(
                                path,
                                maximum_bytes=maximum_bytes,
                            )
                        except (OSError, ValueError) as exc:
                            errors.append(f"provenance.outputs.{name}: unreadable: {exc}")
                            continue
                        if receipt.get("sha256") != observed_hash:
                            errors.append(f"provenance.outputs.{name}.sha256: file mismatch")
                        if receipt.get("size") != observed_size:
                            errors.append(f"provenance.outputs.{name}.size: file mismatch")
        if isinstance(trajectory, Mapping) and isinstance(provenance, Mapping):
            errors.extend(_validate_cross_file_bindings(result, trajectory, provenance))
        if trace_rows is not None and isinstance(provenance, Mapping):
            errors.extend(_validate_native_trace_bindings(trace_rows, result, provenance))
    return {
        "ok": not errors,
        "errors": errors,
        "logs_dir": str(root),
        "schema_versions": {
            "result": RESULT_SCHEMA_VERSION,
            "trajectory": ATIF_SCHEMA_VERSION,
            "provenance": PROVENANCE_SCHEMA_VERSION,
        },
    }


__all__ = [
    "validate_atif_v17",
    "validate_contract_directory",
    "validate_provenance_contract",
    "validate_result_contract",
]
