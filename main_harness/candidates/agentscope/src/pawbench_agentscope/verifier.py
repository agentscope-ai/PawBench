from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from scripts.security import redact_sensitive_text, safe_subprocess_env

from pawbench_agentscope.features import safe_join
from pawbench_agentscope.models import TaskSpec, VerifierResult


def verify_artifacts(task: TaskSpec, workspace_root: Path, *, run_semantic: bool = True) -> VerifierResult:
    missing: list[str] = []
    empty: list[str] = []
    failed_tests: list[str] = []
    expected_text = task.hidden_contract.get("artifact_text", {})
    for rel_path in task.required_artifacts:
        path = safe_join(workspace_root, rel_path)
        if not path.exists():
            missing.append(rel_path)
        elif path.is_file() and path.stat().st_size == 0:
            empty.append(rel_path)
        elif rel_path in expected_text and path.read_text(encoding="utf-8", errors="replace").strip() != str(expected_text[rel_path]).strip():
            failed_tests.append(f"{rel_path}: content mismatch")

    validator_command = task.test_command or task.hidden_contract.get("validator_command")
    if run_semantic and validator_command and not missing and not empty and not failed_tests:
        env = safe_subprocess_env(
            workspace_root,
            extra={"PAWBENCH_WORKSPACE_ROOT": str(workspace_root.resolve())},
        )
        timeout = float(task.hidden_contract.get("validator_timeout_sec", 120))
        try:
            command = shlex.split(str(validator_command))
            if not command:
                raise ValueError("semantic validator command is empty")
            proc = subprocess.run(
                command,
                cwd=workspace_root,
                shell=False,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
            if proc.returncode != 0:
                output = redact_sensitive_text(proc.stdout + proc.stderr).strip()
                failed_tests.append(f"semantic_validator exit={proc.returncode}: {output[-4000:]}")
        except Exception as exc:
            failed_tests.append(
                f"semantic_validator {type(exc).__name__}: "
                f"{redact_sensitive_text(str(exc))}"
            )

    return VerifierResult(ok=not missing and not empty and not failed_tests, missing_artifacts=missing, empty_artifacts=empty, failed_tests=failed_tests)
