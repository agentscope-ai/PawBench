from __future__ import annotations

import os
import math
import selectors
import signal
import shlex
import stat
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

from pawbench_agentscope._portable_security import redact_sensitive_text, safe_subprocess_env

from pawbench_agentscope.features import safe_join
from pawbench_agentscope.models import TaskSpec, VerifierResult


MAX_VALIDATOR_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_EXACT_TEXT_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_VALIDATOR_COMMAND_CHARS = 64 * 1024
MAX_VALIDATOR_TIMEOUT_SECONDS = 900.0


def _inspect_artifact(path: Path, *, read_text: bool) -> tuple[str, int, str | None]:
    """Inspect one stable fd without following a last-moment symlink swap."""

    flags = os.O_RDONLY | os.O_NONBLOCK | int(getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return "missing", 0, None
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}", 0, None
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            return "non_regular", metadata.st_size, None
        if not read_text:
            return "regular", metadata.st_size, None
        if metadata.st_size > MAX_EXACT_TEXT_ARTIFACT_BYTES:
            return "too_large", metadata.st_size, None
        chunks = bytearray()
        while len(chunks) <= MAX_EXACT_TEXT_ARTIFACT_BYTES:
            chunk = os.read(fd, min(64 * 1024, MAX_EXACT_TEXT_ARTIFACT_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > MAX_EXACT_TEXT_ARTIFACT_BYTES:
            return "too_large", len(chunks), None
        return "regular", metadata.st_size, bytes(chunks).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def _run_validator_process(
    command: list[str],
    *,
    workspace_root: Path,
    env: dict[str, str],
    timeout: float,
) -> tuple[int, str, str, bool, bool]:
    """Run a validator with bounded output and process-group cleanup."""

    process = subprocess.Popen(
        command,
        cwd=workspace_root,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    captured_total = 0
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()

    def kill_process_group() -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError, PermissionError):
            if process.poll() is None:
                process.kill()

    timed_out = False
    output_exceeded = False
    selector = selectors.DefaultSelector()
    for stream, destination in (
        (process.stdout, stdout_buffer),
        (process.stderr, stderr_buffer),
    ):
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream.fileno(), selectors.EVENT_READ, destination)

    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                timed_out = True
                kill_process_group()
                break
            events = selector.select(timeout=min(0.1, remaining_time))
            for key, _ in events:
                try:
                    chunk = os.read(key.fd, 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                remaining_capture = max(
                    0,
                    MAX_VALIDATOR_CAPTURE_BYTES - captured_total,
                )
                if remaining_capture:
                    kept = chunk[:remaining_capture]
                    key.data.extend(kept)
                    captured_total += len(kept)
                if len(chunk) > remaining_capture:
                    output_exceeded = True
                    kill_process_group()
                    break
            if output_exceeded:
                break
            if process.poll() is not None and not selector.get_map():
                break
        if process.poll() is None:
            try:
                return_code = process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                kill_process_group()
                return_code = process.wait(timeout=2.0)
        else:
            return_code = process.returncode
    finally:
        # Also remove background descendants that inherited validator pipes.
        kill_process_group()
        selector.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    stdout = bytes(stdout_buffer).decode("utf-8", errors="replace")
    stderr = bytes(stderr_buffer).decode("utf-8", errors="replace")
    return return_code, stdout, stderr, timed_out, output_exceeded


def verify_artifacts(task: TaskSpec, workspace_root: Path, *, run_semantic: bool = True) -> VerifierResult:
    missing: list[str] = []
    empty: list[str] = []
    failed_tests: list[str] = []
    raw_expected_text = task.hidden_contract.get("artifact_text", {})
    if not isinstance(raw_expected_text, Mapping) or any(
        not isinstance(key, str) for key in raw_expected_text
    ):
        failed_tests.append("hidden_contract.artifact_text must be an object with string keys")
        expected_text: Mapping[str, object] = {}
    else:
        expected_text = raw_expected_text
    for rel_path in task.required_artifacts:
        try:
            path = safe_join(workspace_root, rel_path)
        except ValueError:
            failed_tests.append(f"{rel_path}: artifact path escapes workspace")
            continue
        status, size, observed_text = _inspect_artifact(
            path,
            read_text=rel_path in expected_text,
        )
        if status == "missing":
            missing.append(rel_path)
        elif status == "non_regular" or status.startswith("unreadable:"):
            failed_tests.append(f"{rel_path}: artifact is not a regular file")
        elif size == 0:
            empty.append(rel_path)
        elif status == "too_large":
            failed_tests.append(
                f"{rel_path}: exact-text artifact exceeds {MAX_EXACT_TEXT_ARTIFACT_BYTES} bytes"
            )
        elif rel_path in expected_text and str(observed_text).strip() != str(expected_text[rel_path]).strip():
            failed_tests.append(f"{rel_path}: content mismatch")

    validator_command = task.test_command or task.hidden_contract.get("validator_command")
    if run_semantic and validator_command and not missing and not empty and not failed_tests:
        env = safe_subprocess_env(
            workspace_root,
            extra={"PAWBENCH_WORKSPACE_ROOT": str(workspace_root.resolve())},
        )
        raw_timeout = task.hidden_contract.get("validator_timeout_sec", 120)
        try:
            if isinstance(raw_timeout, bool):
                raise ValueError("validator timeout must be a finite positive number")
            timeout = float(raw_timeout)
            if (
                not math.isfinite(timeout)
                or timeout <= 0
                or timeout > MAX_VALIDATOR_TIMEOUT_SECONDS
            ):
                raise ValueError(
                    f"validator timeout must be finite and in (0, {MAX_VALIDATOR_TIMEOUT_SECONDS:g}]"
                )
            command_text = str(validator_command)
            if len(command_text) > MAX_VALIDATOR_COMMAND_CHARS or "\x00" in command_text:
                raise ValueError("semantic validator command is too long or contains NUL")
            command = shlex.split(command_text)
            if not command:
                raise ValueError("semantic validator command is empty")
            return_code, stdout, stderr, timed_out, output_exceeded = _run_validator_process(
                command,
                workspace_root=workspace_root,
                timeout=timeout,
                env=env,
            )
            output = redact_sensitive_text(stdout + stderr).strip()
            if timed_out:
                failed_tests.append(f"semantic_validator TimeoutExpired: exceeded {timeout:g}s")
            elif output_exceeded:
                failed_tests.append(
                    f"semantic_validator output limit exceeded ({MAX_VALIDATOR_CAPTURE_BYTES} bytes): "
                    f"{output[-4000:]}"
                )
            elif return_code != 0:
                failed_tests.append(f"semantic_validator exit={return_code}: {output[-4000:]}")
        except Exception as exc:
            failed_tests.append(
                f"semantic_validator {type(exc).__name__}: "
                f"{redact_sensitive_text(str(exc))}"
            )

    return VerifierResult(ok=not missing and not empty and not failed_tests, missing_artifacts=missing, empty_artifacts=empty, failed_tests=failed_tests)
