"""Reference-only Harbor wrapper owned by Boyin's integration layer.

This file is not imported by Harness-core and intentionally depends on Harbor.
Copy/adapt it inside Harbor after pinning a Harbor revision. The execution and
Feature semantics remain in the portable ``agentscope-lab`` CLI.
"""

from __future__ import annotations

import json
import re
import shlex
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


class HarnessCoreAgentScope(BaseAgent):
    SUPPORTS_ATIF = True
    SUPPORTS_RESUME = False
    SUPPORTS_WINDOWS = False
    RESULT_SCHEMA_VERSION = "harness-core-harbor-result/v1"
    MAX_RESULT_BYTES = 8 * 1024 * 1024
    MAX_INSTRUCTION_BYTES = 512 * 1024
    TAXONOMY_VERSION = "harness_core_v2_20260710"
    RESULT_FILES = {"result", "trace", "trajectory", "provenance"}
    RESULT_FILE_NAMES = {
        "result": "result.json",
        "trace": "harness-core-trace.jsonl",
        "trajectory": "trajectory.json",
        "provenance": "provenance.json",
    }
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
    ERROR_METADATA_FIELDS = {
        "error_schema_version",
        "error_code",
        "failure_scope",
        "retryable",
        "cause_type",
    }

    @staticmethod
    @override
    def name() -> str:
        return "agentscope-lab"

    @override
    def version(self) -> str | None:
        return "0.2.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        result = await environment.exec("agentscope-lab --version")
        if result.return_code != 0:
            raise RuntimeError(
                "agentscope-lab is absent; install the pinned wheel "
                "in the task image or in this Harbor-owned setup method"
            )
        expected = f"{self.name()} {self.version()}"
        if (result.stdout or "").strip() != expected:
            raise RuntimeError(
                f"agentscope-lab version mismatch; expected {expected}"
            )

    @staticmethod
    def _safe_task_id(session_id: str | None) -> str:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id or "harbor-task")
        normalized = normalized.strip("-._") or "harbor-task"
        return normalized[:128]

    @classmethod
    def _validate_result_payload(
        cls,
        payload: dict,
        *,
        runtime_succeeded: bool,
        expected_task_id: str,
    ) -> None:
        if payload.get("schema_version") != cls.RESULT_SCHEMA_VERSION:
            raise RuntimeError("Harness-core result schema version is missing or unsupported")
        if payload.get("task_id") != expected_task_id:
            raise RuntimeError("Harness-core result task_id does not match the current trial")
        if payload.get("success") is not runtime_succeeded:
            raise RuntimeError("Harness-core result success flag disagrees with process exit")
        if runtime_succeeded:
            verifier = payload.get("verifier")
            if not isinstance(payload.get("accepted"), bool):
                raise RuntimeError("Harness-core success result accepted flag is missing or invalid")
            if not isinstance(verifier, dict) or not isinstance(verifier.get("ok"), bool):
                raise RuntimeError("Harness-core success result verifier is missing or invalid")
            if payload.get("taxonomy_version") != cls.TAXONOMY_VERSION:
                raise RuntimeError("Harness-core success result taxonomy version is unsupported")
            completion_ok = payload.get("completion_ok")
            verification_gated = payload.get("verification_gated")
            if not isinstance(completion_ok, bool) or not isinstance(verification_gated, bool):
                raise RuntimeError("Harness-core completion/verification flags are invalid")
            expected_accepted = completion_ok and (
                verifier["ok"] if verification_gated else True
            )
            if payload["accepted"] is not expected_accepted:
                raise RuntimeError("Harness-core accepted flag is internally inconsistent")
            files = payload.get("files")
            if not isinstance(files, dict) or set(files) != cls.RESULT_FILES or not all(
                isinstance(path, str) and path for path in files.values()
            ):
                raise RuntimeError("Harness-core success result file manifest is invalid")
            for key, expected_name in cls.RESULT_FILE_NAMES.items():
                reference = files[key]
                path = PurePosixPath(reference.replace("\\", "/"))
                if (
                    path.name != expected_name
                    or ".." in path.parts
                    or any(
                        ord(character) < 32 or ord(character) == 127
                        for character in reference
                    )
                ):
                    raise RuntimeError(
                        "Harness-core success result file manifest contains an unsafe reference"
                    )
        else:
            if not isinstance(payload.get("error_type"), str) or not payload["error_type"].strip():
                raise RuntimeError("Harness-core error result error_type is missing or invalid")
            if not isinstance(payload.get("error"), str) or not payload["error"].strip():
                raise RuntimeError("Harness-core error result message is missing or invalid")
            if "retryable" in payload and not isinstance(payload.get("retryable"), bool):
                raise RuntimeError("Harness-core error result retryable flag is invalid")
            present_metadata = cls.ERROR_METADATA_FIELDS & set(payload)
            if present_metadata and present_metadata != cls.ERROR_METADATA_FIELDS:
                raise RuntimeError("Harness-core coded error metadata is incomplete")
            if present_metadata == cls.ERROR_METADATA_FIELDS:
                error_code = payload.get("error_code")
                expectation = (
                    cls.ERROR_EXPECTATIONS.get(error_code)
                    if isinstance(error_code, str)
                    else None
                )
                if expectation is None:
                    raise RuntimeError("Harness-core error code is unsupported")
                expected_scope, expected_retryable = expectation
                if (
                    payload.get("failure_scope") != expected_scope
                    or payload.get("retryable") is not expected_retryable
                    or not isinstance(payload.get("cause_type"), str)
                    or not payload["cause_type"].strip()
                ):
                    raise RuntimeError("Harness-core coded error metadata is inconsistent")

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not isinstance(instruction, str) or not instruction.strip():
            raise RuntimeError("Harbor instruction must be a non-empty string")
        if len(instruction.encode("utf-8")) > self.MAX_INSTRUCTION_BYTES:
            raise RuntimeError("Harbor instruction exceeds the Harness-core input limit")
        workdir_result = await environment.exec("pwd -P")
        if workdir_result.return_code != 0 or not workdir_result.stdout:
            raise RuntimeError("could not resolve Harbor task workspace")
        workspace = workdir_result.stdout.strip()
        if (
            not workspace.startswith("/")
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in workspace
            )
        ):
            raise RuntimeError("Harbor task workspace is not one safe absolute path")
        model = (self.model_name or "qwen3.7-max").split("/", 1)[-1]
        if (
            not model
            or len(model.encode("utf-8")) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in model)
        ):
            raise RuntimeError("Harbor model name is invalid")
        task_id = self._safe_task_id(self.session_id)
        logs_dir = EnvironmentPaths.agent_dir.as_posix()
        remote_instruction = f"/tmp/harness-core-instruction-{uuid.uuid4().hex}.md"
        with tempfile.TemporaryDirectory(prefix="harness-core-harbor-") as temp_dir:
            local_instruction = Path(temp_dir) / "instruction.md"
            local_instruction.write_text(instruction, encoding="utf-8")
            await environment.upload_file(local_instruction, remote_instruction)

        command = " ".join(
            [
                "agentscope-lab",
                "--task-id",
                shlex.quote(task_id),
                "--instruction-file",
                shlex.quote(remote_instruction),
                "--workspace-root",
                shlex.quote(workspace),
                "--logs-dir",
                shlex.quote(logs_dir),
                "--model",
                shlex.quote(model),
            ]
        )
        try:
            result = await environment.exec(command, cwd=workspace)
        finally:
            await environment.exec(f"rm -f -- {shlex.quote(remote_instruction)}")

        result_path = f"{logs_dir}/result.json"
        result_check = await environment.exec(
            " ".join(
                [
                    "test",
                    "-f",
                    shlex.quote(result_path),
                    "&&",
                    "test",
                    "!",
                    "-L",
                    shlex.quote(result_path),
                    "&&",
                    "wc",
                    "-c",
                    "<",
                    shlex.quote(result_path),
                ]
            )
        )
        try:
            result_size = int((result_check.stdout or "").strip())
        except ValueError:
            result_size = -1
        if (
            result_check.return_code != 0
            or result_size < 0
            or result_size > self.MAX_RESULT_BYTES
        ):
            raise RuntimeError(f"Harness-core result is missing, unsafe, or oversized at {result_path}")
        result_json = await environment.exec(f"cat {shlex.quote(result_path)}")
        if result_json.return_code != 0 or not result_json.stdout:
            raise RuntimeError(f"Harness-core result is missing at {result_path}")
        if len(result_json.stdout.encode("utf-8")) > self.MAX_RESULT_BYTES:
            raise RuntimeError(f"Harness-core result changed or became oversized at {result_path}")
        try:
            parsed = json.loads(
                result_json.stdout,
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_nonfinite_json,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"Harness-core emitted malformed JSON at {result_path}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(
                f"Harness-core result must be a JSON object at {result_path}"
            )
        payload: dict = parsed
        self._validate_result_payload(
            payload,
            runtime_succeeded=result.return_code == 0,
            expected_task_id=task_id,
        )
        verifier = payload.get("verifier")
        context.metadata = {
            **(context.metadata or {}),
            "harness_core": {
                "schema_version": payload.get("schema_version"),
                "task_id": payload.get("task_id", task_id),
                "success": payload.get("success"),
                "accepted": payload.get("accepted"),
                "verifier_ok": verifier.get("ok") if isinstance(verifier, dict) else None,
                "taxonomy_version": payload.get("taxonomy_version"),
                "error_code": payload.get("error_code"),
                "retryable": payload.get("retryable"),
            }
        }
        if result.return_code != 0:
            code = payload.get("error_code") or "unclassified"
            raise RuntimeError(
                f"Harness-core runtime failed ({code}); inspect {result_path}"
            )
