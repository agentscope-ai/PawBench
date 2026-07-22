#!/usr/bin/env python3
"""Run OpenJudge's harness-based AgenticGrader and emit Harbor rewards."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from openjudge.graders.agentic_grader import AgenticGrader
from openjudge.graders.schema import Checkpoint, GraderError, Rubric
from openjudge.harness import ClaudeCodeHarness, CodexHarness, CursorAgentHarness


TESTS_DIR = Path("/tests")
QUALITY_DIR = TESTS_DIR / "quality"
WORKSPACE_PATH = Path(os.environ.get("OPENJUDGE_WORKSPACE", "/home/node/workspace"))
TRAJECTORY_PATH = Path(
    os.environ.get("OPENJUDGE_TRAJECTORY", "/logs/agent/trajectory.json")
)
REWARD_PATH = Path("/logs/verifier/reward.json")
DETAILS_PATH = Path("/logs/verifier/reward-details.json")
JUDGE_LOG_DIR = Path(
    os.environ.get("OPENJUDGE_JUDGE_LOG_DIR", "/logs/verifier/openjudge-judge")
)
JUDGE_STREAM_PATH = Path("/logs/verifier/openjudge-judge-stream.jsonl")
JUDGE_STDERR_PATH = Path("/logs/verifier/openjudge-judge-stderr.log")
JUDGE_SPEC_PATH = Path("/logs/verifier/openjudge-judge-spec.json")
JUDGE_RESULT_PATH = Path("/logs/verifier/openjudge-judge-result.json")
JUDGE_HARNESS_PATH = Path("/logs/verifier/openjudge-harness.json")

HARNESS_TYPES = {
    "claude": ClaudeCodeHarness,
    "claude-code": ClaudeCodeHarness,
    "codex": CodexHarness,
    "cursor": CursorAgentHarness,
    "cursor-agent": CursorAgentHarness,
}

_KNOWN_MODEL_PREFIXES = {
    "openai",
    "dashscope",
    "anthropic",
    "google",
    "gemini",
    "custom",
    "deepseek",
    "azure",
    "qwen",
}


class RecordingHarness:
    """Keep harness diagnostics that AgenticGrader v1 otherwise discards."""

    def __init__(self, delegate: Any):
        self.delegate = delegate
        self.last_result: Any = None
        self.invocation_count = 0

    def run(
        self,
        sandbox_dir: Path,
        prompt: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> Any:
        self.invocation_count += 1
        self.last_result = self.delegate.run(sandbox_dir, prompt, schema, model)
        if not getattr(self.last_result, "available", False):
            recovered = _recover_harness_result(sandbox_dir, self.last_result, schema)
            if recovered is not None:
                self.last_result = recovered
        try:
            _persist_harness_logs(
                sandbox_dir=sandbox_dir,
                harness_result=self.last_result,
                invocation=self.invocation_count,
                model=model,
                binary=str(getattr(self.delegate, "binary", "")),
                prompt=prompt,
                schema=schema,
            )
        except Exception as exc:
            # Log persistence must never change the judge verdict.
            print(
                f"Warning: failed to persist OpenJudge harness logs: {exc}",
                file=sys.stderr,
            )
        return self.last_result


def _try_load_judge_result(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) and payload else None


def _extract_result_from_stream_json(raw: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort recovery when Claude prints stream-json but skips the result file.

    Claude Code occasionally finishes the judging turn in chat / tool payloads
    without leaving ``_judge_result.json`` at the sandbox root. Reconstruct a
    flat checkpoint map from stream-json events when possible.
    """
    if not raw.strip():
        return None
    checkpoint_ids = set(schema.keys()) if schema else set()
    candidates: list[dict[str, Any]] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        # Tool writes of the result file.
        content = event.get("message", {}).get("content") if isinstance(event.get("message"), dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and str(block.get("name", "")).lower() in {
                    "write",
                    "create_file",
                    "writefile",
                }:
                    tool_input = block.get("input") or {}
                    path = str(tool_input.get("path") or tool_input.get("file_path") or "")
                    if path.endswith("_judge_result.json"):
                        body = tool_input.get("contents") or tool_input.get("content") or tool_input.get("file_text")
                        if isinstance(body, str):
                            try:
                                parsed = json.loads(body)
                            except json.JSONDecodeError:
                                parsed = None
                            if isinstance(parsed, dict) and parsed:
                                candidates.append(parsed)
                if block.get("type") == "tool_use" and str(block.get("name", "")).lower() in {
                    "bash",
                    "shell",
                }:
                    tool_input = block.get("input") or {}
                    cmd = str(tool_input.get("command") or "")
                    if "_judge_result.json" in cmd and "{" in cmd:
                        # naive: find last JSON object in the command
                        start = cmd.rfind("{")
                        end = cmd.rfind("}")
                        if start != -1 and end > start:
                            try:
                                parsed = json.loads(cmd[start : end + 1])
                            except json.JSONDecodeError:
                                parsed = None
                            if isinstance(parsed, dict) and parsed:
                                candidates.append(parsed)

        # Some models echo the full verdict JSON in assistant text.
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = str(block.get("text") or "")
                    start = text.find("{")
                    end = text.rfind("}")
                    if start != -1 and end > start:
                        try:
                            parsed = json.loads(text[start : end + 1])
                        except json.JSONDecodeError:
                            parsed = None
                        if isinstance(parsed, dict) and parsed:
                            candidates.append(parsed)

    for payload in reversed(candidates):
        if checkpoint_ids and checkpoint_ids.issubset(payload.keys()):
            return payload
        # Accept payloads that look like checkpoint maps even if ids differ slightly.
        values = list(payload.values())
        if values and all(isinstance(v, dict) and "passed" in v for v in values):
            return payload
    return None


def _recover_harness_result(
    sandbox_dir: Path,
    harness_result: Any,
    schema: dict[str, Any],
) -> Any | None:
    """Recover a usable HarnessResult when the CLI finished but the file protocol missed."""
    from openjudge.harness.base import HarnessResult

    # 1) Search the sandbox for a misplaced result file.
    for path in [
        sandbox_dir / "_judge_result.json",
        sandbox_dir / "workspace" / "_judge_result.json",
        *sandbox_dir.rglob("_judge_result.json"),
    ]:
        payload = _try_load_judge_result(path)
        if payload is not None:
            return HarnessResult(
                available=True,
                result=payload,
                raw_stdout=getattr(harness_result, "raw_stdout", "") or "",
                raw_stderr=getattr(harness_result, "raw_stderr", "") or "",
                exit_code=getattr(harness_result, "exit_code", 0) or 0,
                timed_out=bool(getattr(harness_result, "timed_out", False)),
                duration=float(getattr(harness_result, "duration", 0.0) or 0.0),
            )

    # 2) Reconstruct from Claude stream-json stdout/stderr.
    raw = "\n".join(
        [
            getattr(harness_result, "raw_stdout", "") or "",
            getattr(harness_result, "raw_stderr", "") or "",
        ]
    )
    payload = _extract_result_from_stream_json(raw, schema)
    if payload is None:
        return None
    return HarnessResult(
        available=True,
        result=payload,
        raw_stdout=getattr(harness_result, "raw_stdout", "") or "",
        raw_stderr=getattr(harness_result, "raw_stderr", "") or "",
        exit_code=getattr(harness_result, "exit_code", 0) or 0,
        timed_out=bool(getattr(harness_result, "timed_out", False)),
        duration=float(getattr(harness_result, "duration", 0.0) or 0.0),
    )


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _persist_harness_logs(
    *,
    sandbox_dir: Path,
    harness_result: Any,
    invocation: int,
    model: str | None,
    binary: str,
    prompt: str,
    schema: dict[str, Any],
) -> None:
    """Persist the judge CLI's full file protocol and native process streams.

    ``ProcessSandbox`` deletes its temporary directory after the grader call.
    Copying these files while ``RecordingHarness.run`` is still executing is
    therefore the only reliable way to retain the exact judge inputs, output,
    tool-call stream, stderr, and process status.
    """
    run_name = f"run-{invocation:03d}"
    run_dir = JUDGE_LOG_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    stdout = str(getattr(harness_result, "raw_stdout", "") or "")
    stderr = str(getattr(harness_result, "raw_stderr", "") or "")
    _atomic_text(run_dir / "stdout.jsonl", stdout)
    _atomic_text(run_dir / "stderr.log", stderr)
    _atomic_text(JUDGE_STREAM_PATH, stdout)
    _atomic_text(JUDGE_STDERR_PATH, stderr)

    spec_payload: dict[str, Any] = {
        "instructions": prompt,
        "output_schema": schema,
    }
    sandbox_spec = sandbox_dir / "_judge_spec.json"
    if sandbox_spec.is_file():
        try:
            loaded_spec = json.loads(
                sandbox_spec.read_text(encoding="utf-8", errors="replace")
            )
            if isinstance(loaded_spec, dict):
                spec_payload = loaded_spec
        except (json.JSONDecodeError, OSError):
            pass
    _atomic_json(run_dir / "spec.json", spec_payload)
    _atomic_json(JUDGE_SPEC_PATH, spec_payload)

    sandbox_result_paths = [
        sandbox_dir / "_judge_result.json",
        sandbox_dir / "workspace" / "_judge_result.json",
    ]
    result_payload: dict[str, Any] = {}
    result_source: str | None = None
    for path in sandbox_result_paths:
        loaded_result = _try_load_judge_result(path)
        if loaded_result is not None:
            result_payload = loaded_result
            result_source = str(path.relative_to(sandbox_dir))
            break
    if not result_payload:
        recovered_result = getattr(harness_result, "result", None)
        if isinstance(recovered_result, dict):
            result_payload = recovered_result
            if result_payload:
                result_source = "recovered-from-harness-stream"
    _atomic_json(run_dir / "result.json", result_payload)
    _atomic_json(JUDGE_RESULT_PATH, result_payload)

    metadata = {
        "schema_version": "1.0",
        "invocation": invocation,
        "available": bool(getattr(harness_result, "available", False)),
        "exit_code": int(getattr(harness_result, "exit_code", -1)),
        "timed_out": bool(getattr(harness_result, "timed_out", False)),
        "duration_seconds": float(getattr(harness_result, "duration", 0.0) or 0.0),
        "model": model,
        "binary": binary,
        "result_source": result_source,
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "files": {
            "stdout": f"{run_name}/stdout.jsonl",
            "stderr": f"{run_name}/stderr.log",
            "spec": f"{run_name}/spec.json",
            "result": f"{run_name}/result.json",
        },
    }
    _atomic_json(run_dir / "harness.json", metadata)
    _atomic_json(JUDGE_HARNESS_PATH, metadata)

    manifest_path = JUDGE_LOG_DIR / "manifest.json"
    manifest: dict[str, Any] = {"schema_version": "1.0", "invocations": []}
    if manifest_path.is_file():
        try:
            existing = json.loads(
                manifest_path.read_text(encoding="utf-8", errors="replace")
            )
            if isinstance(existing, dict) and isinstance(
                existing.get("invocations"), list
            ):
                manifest = existing
        except (json.JSONDecodeError, OSError):
            pass
    manifest["invocations"].append(metadata)
    _atomic_json(manifest_path, manifest)


def _reset_judge_logs() -> None:
    shutil.rmtree(JUDGE_LOG_DIR, ignore_errors=True)
    for path in (
        JUDGE_STREAM_PATH,
        JUDGE_STDERR_PATH,
        JUDGE_SPEC_PATH,
        JUDGE_RESULT_PATH,
        JUDGE_HARNESS_PATH,
    ):
        path.unlink(missing_ok=True)


def _persist_runner_error(exc: Exception) -> None:
    metadata: dict[str, Any] = {
        "schema_version": "1.0",
        "available": False,
        "runner_error": f"{type(exc).__name__}: {exc}",
    }
    if JUDGE_HARNESS_PATH.is_file():
        try:
            existing = json.loads(
                JUDGE_HARNESS_PATH.read_text(encoding="utf-8", errors="replace")
            )
            if isinstance(existing, dict):
                metadata = existing
                metadata["runner_error"] = f"{type(exc).__name__}: {exc}"
        except (json.JSONDecodeError, OSError):
            pass
    _atomic_json(JUDGE_HARNESS_PATH, metadata)


def _load_context(config: dict[str, Any]) -> str:
    judge = config.get("judge", {})
    prompt_path = QUALITY_DIR / str(
        judge.get("prompt_template", "agent_judge_prompt.md")
    )
    reference_path = QUALITY_DIR / str(
        judge.get("reference", "expected_behavior.txt")
    )

    prompt = _read_text(prompt_path) if prompt_path.is_file() else ""
    # The old RewardKit template contains its own criteria and stdout JSON
    # protocol. OpenJudge injects both, so retain only the task-specific prefix.
    prompt = prompt.partition("## Criteria")[0].rstrip()
    reference = _read_text(reference_path) if reference_path.is_file() else ""

    parts = [
        prompt,
        (
            "## Checkpoint evaluation policy\n"
            "- Evaluate every checkpoint independently. A failure in one checkpoint "
            "must not cause another checkpoint to fail.\n"
            "- Apply only the requirements explicitly stated in that checkpoint; "
            "do not invent additional file, format, or content requirements.\n"
            "- Distinguish requirements for different deliverables. Do not require "
            "a CSV to contain information that the task assigns to the report.\n"
            "- Cite the candidate artifact and source evidence used for the verdict. "
            "When commands are run, put the exact command and relevant output in "
            "execution_log rather than a reconstructed summary."
        ),
    ]
    if reference:
        parts.extend(
            [
                "## Ground-truth reference",
                "Use this reference as expected behavior, but verify the candidate "
                "against concrete files in the copied workspace.",
                reference.rstrip(),
            ]
        )
    return "\n\n".join(part for part in parts if part)


def _build_rubrics(config: dict[str, Any]) -> list[Rubric]:
    rubrics: list[Rubric] = []
    seen_ids: set[str] = set()
    for index, criterion in enumerate(config.get("criterion", [])):
        criterion_id = str(criterion.get("id") or f"criterion_{index}")
        if criterion_id in seen_ids:
            raise ValueError(f"duplicate criterion id: {criterion_id}")
        seen_ids.add(criterion_id)

        name = str(criterion.get("name") or criterion_id)
        description = str(criterion.get("description") or "")
        weight = float(criterion.get("weight", 1.0))
        rubrics.append(
            Rubric(
                name=name,
                description=description,
                weight=weight,
                checkpoints=[
                    Checkpoint(
                        id=criterion_id,
                        description=description,
                        weight=1.0,
                    )
                ],
            )
        )
    if not rubrics:
        raise ValueError("agent_judge.toml contains no [[criterion]] entries")
    return rubrics


def _load_trajectory() -> list[dict[str, Any]] | None:
    if not TRAJECTORY_PATH.is_file():
        return None
    payload = json.loads(_read_text(TRAJECTORY_PATH))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return [{"type": "raw_trajectory", "value": payload}]

    steps = payload.get("steps")
    if not isinstance(steps, list):
        return [payload]

    metadata = {
        "type": "atif_metadata",
        "schema_version": payload.get("schema_version"),
        "session_id": payload.get("session_id"),
        "agent": payload.get("agent"),
    }
    return [metadata, *(step for step in steps if isinstance(step, dict))]


def _usable_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("${"):
        return None
    return value


def _strip_model_prefix(model: str | None) -> str | None:
    """Strip pawbench ``provider/model`` prefixes for CLI ``--model`` args."""
    if not model:
        return None
    model = model.strip()
    if "/" not in model:
        return model
    prefix, rest = model.split("/", 1)
    if prefix.lower() in _KNOWN_MODEL_PREFIXES and rest:
        return rest
    return model


def _which_cli(binary: str) -> str | None:
    path = shutil.which(binary)
    if path:
        return path
    # Common install locations used by Claude Code / Codex bootstraps.
    for candidate in (
        Path.home() / ".local" / "bin" / binary,
        Path("/root/.local/bin") / binary,
        Path("/usr/local/bin") / binary,
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _ensure_path_contains(directory: Path) -> None:
    path = os.environ.get("PATH", "")
    entries = path.split(":") if path else []
    text = str(directory)
    if text not in entries:
        os.environ["PATH"] = f"{text}:{path}" if path else text


def _run_shell(command: str, *, timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-lc", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _ensure_harness_cli(harness_name: str) -> str:
    """Make sure the judge CLI exists in the shared task environment.

    OpenJudge agent-judge tasks run the verifier *inside* the task container
    (``environment_mode=shared``). That image often has Codex but not Claude
    Code. When the agent-under-test is not ``harbor:claude-code``, Harbor never
    installs ``claude``, so ClaudeCodeHarness fails with exit_code=-1 / empty
    output ("harness CLI is unavailable"). Install the missing CLI here.
    """
    if harness_name in {"claude", "claude-code"}:
        binary = "claude"
        local_bin = Path.home() / ".local" / "bin"
        _ensure_path_contains(local_bin)
        existing = _which_cli(binary)
        if existing:
            return existing

        install_cmds = [
            # Official Claude Code bootstrap (same path Harbor uses).
            "curl -fsSL https://downloads.claude.ai/claude-code-releases/bootstrap.sh | bash",
            # Fallback via npm when bootstrap is blocked.
            "npm install -g @anthropic-ai/claude-code",
        ]
        errors: list[str] = []
        for cmd in install_cmds:
            try:
                result = _run_shell(cmd, timeout=300.0)
            except subprocess.TimeoutExpired as exc:
                errors.append(f"{cmd!r} timed out: {exc}")
                continue
            if result.returncode == 0 and _which_cli(binary):
                break
            errors.append(
                f"{cmd!r} -> rc={result.returncode}; "
                f"stdout={(result.stdout or '')[-500]!r}; "
                f"stderr={(result.stderr or '')[-500]!r}"
            )
        existing = _which_cli(binary)
        if not existing:
            raise RuntimeError(
                "OpenJudge judge requires the `claude` CLI, but it is missing "
                "from this task environment and auto-install failed. "
                "Install Claude Code in the task image, or evaluate with "
                f"`harbor:claude-code` so Harbor installs it. details={errors}"
            )
        return existing

    if harness_name == "codex":
        binary = "codex"
        existing = _which_cli(binary)
        if existing:
            return existing
        raise RuntimeError(
            "OpenJudge judge requires the `codex` CLI, but it is missing from "
            "this task environment (PATH="
            f"{os.environ.get('PATH', '')!r})."
        )

    if harness_name in {"cursor", "cursor-agent"}:
        for binary in ("cursor-agent", "cursor"):
            existing = _which_cli(binary)
            if existing:
                return existing
        raise RuntimeError(
            "OpenJudge judge requires the Cursor CLI, but it is missing from "
            "this task environment."
        )
    return harness_name


def _configure_cli_environment(harness_name: str, model: str | None) -> None:
    """Translate PawBench verifier credentials to the selected CLI contract."""
    if harness_name in {"claude", "claude-code"}:
        # Harbor runs the verifier as root inside its own Docker boundary.
        # Claude Code only permits bypassPermissions for root when the runtime
        # explicitly identifies itself as a sandbox.
        os.environ.setdefault("IS_SANDBOX", "1")
        api_key = (
            _usable_env("ANTHROPIC_API_KEY")
            or _usable_env("LLM_API_KEY")
            or _usable_env("JUDGE_API_KEY")
        )
        base_url = (
            _usable_env("ANTHROPIC_BASE_URL")
            or _usable_env("LLM_BASE_URL")
            or _usable_env("JUDGE_BASE_URL")
        )
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if base_url:
            # Claude Code appends the Anthropic API route itself. PawBench's
            # OpenAI-compatible judge URL commonly ends in /v1.
            os.environ["ANTHROPIC_BASE_URL"] = base_url.removesuffix("/v1")
        if model:
            os.environ["ANTHROPIC_MODEL"] = model
            for alias in (
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "CLAUDE_CODE_SUBAGENT_MODEL",
            ):
                os.environ[alias] = model
    elif harness_name == "codex":
        api_key = (
            _usable_env("OPENAI_API_KEY")
            or _usable_env("LLM_API_KEY")
            or _usable_env("JUDGE_API_KEY")
        )
        base_url = (
            _usable_env("OPENAI_BASE_URL")
            or _usable_env("LLM_BASE_URL")
            or _usable_env("JUDGE_BASE_URL")
        )
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
        if base_url:
            os.environ["OPENAI_BASE_URL"] = base_url
            os.environ["OPENAI_API_BASE"] = base_url
    elif harness_name in {"cursor", "cursor-agent"}:
        api_key = _usable_env("CURSOR_API_KEY")
        if api_key:
            os.environ["CURSOR_API_KEY"] = api_key


def _resolve_harness(config: dict[str, Any]) -> tuple[Any, str, str | None, float]:
    judge = config.get("judge", {})
    harness_name = (
        os.environ.get("OPENJUDGE_HARNESS")
        or str(judge.get("judge", "claude-code"))
        or os.environ.get("REWARDKIT_JUDGE")
    ).strip()
    harness_type = HARNESS_TYPES.get(harness_name)
    if harness_type is None:
        supported = ", ".join(sorted(HARNESS_TYPES))
        raise ValueError(
            f"unsupported OpenJudge harness {harness_name!r}; choose one of {supported}"
        )

    model = (
        os.environ.get("OPENJUDGE_MODEL")
        or os.environ.get("JUDGE_MODEL")
        or os.environ.get("MODEL")
        or judge.get("model")
        or os.environ.get("REWARDKIT_MODEL")
    )
    model = _strip_model_prefix(str(model).strip() if model else None)
    timeout = float(
        os.environ.get("OPENJUDGE_TIMEOUT") or judge.get("timeout", 900)
    )
    _configure_cli_environment(harness_name, model)
    cli_path = _ensure_harness_cli(harness_name)
    harness = RecordingHarness(harness_type(binary=cli_path, timeout_s=timeout))
    return harness, harness_name, model, timeout


def _aggregate(
    raw_score: float,
    passed_values: list[bool],
    aggregation: str,
    threshold: float,
) -> float:
    if aggregation == "threshold":
        return 1.0 if raw_score >= threshold else 0.0
    if aggregation == "all_pass":
        return 1.0 if passed_values and all(passed_values) else 0.0
    if aggregation == "any_pass":
        return 1.0 if any(passed_values) else 0.0
    return raw_score


def _checkpoint_results(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for rubric in metadata.get("rubric_results", []):
        if not isinstance(rubric, dict):
            continue
        for checkpoint in rubric.get("checkpoint_results", []):
            if not isinstance(checkpoint, dict):
                continue
            checkpoint_id = checkpoint.get("checkpoint_id")
            if checkpoint_id:
                results[str(checkpoint_id)] = checkpoint
    return results


def _write_success(
    config: dict[str, Any],
    result: Any,
    harness_name: str,
    model: str | None,
    timeout: float,
    trajectory_loaded: bool,
) -> None:
    metadata = dict(result.metadata or {})
    by_id = _checkpoint_results(metadata)
    criteria_details: list[dict[str, Any]] = []
    passed_values: list[bool] = []
    judge_output: dict[str, dict[str, Any]] = {}

    for criterion in config.get("criterion", []):
        criterion_id = str(criterion.get("id"))
        checkpoint = by_id.get(criterion_id, {})
        passed = bool(checkpoint.get("passed", False))
        reason = str(checkpoint.get("reason", "missing checkpoint result"))
        execution_log = checkpoint.get("execution_log")
        passed_values.append(passed)
        criteria_details.append(
            {
                "id": criterion_id,
                "name": str(criterion.get("name") or criterion_id),
                "value": 1.0 if passed else 0.0,
                "raw": "yes" if passed else "no",
                "weight": float(criterion.get("weight", 1.0)),
                "description": str(criterion.get("description") or ""),
                "reasoning": reason,
                "execution_log": execution_log,
            }
        )
        judge_output[criterion_id] = {
            "passed": passed,
            "reason": reason,
            "execution_log": execution_log,
        }

    raw_score = float(result.score)
    scoring = config.get("scoring", {})
    scoring_aggregation = str(scoring.get("aggregation", "weighted_mean"))
    scoring_threshold = float(scoring.get("threshold", 0.8))
    quality_score = _aggregate(
        raw_score,
        passed_values,
        scoring_aggregation,
        scoring_threshold,
    )

    reward_config = _read_toml(TESTS_DIR / "reward.toml")
    reward_entries = reward_config.get("reward", [])
    reward_rule = reward_entries[0] if reward_entries else {}
    reward_aggregation = str(reward_rule.get("aggregation", "weighted_mean"))
    reward_threshold = float(reward_rule.get("threshold", 0.8))
    reward_score = _aggregate(
        quality_score,
        [quality_score > 0.0],
        reward_aggregation,
        reward_threshold,
    )

    details = {
        "quality": {
            "score": quality_score,
            "raw_score": raw_score,
            "criteria": criteria_details,
            "kind": "agent",
            "framework": "openjudge-agentic-grader",
            "judge": {
                "agent": harness_name,
                "model": model,
                "timeout": timeout,
                "sandbox": "physical-copy",
                "atif_trajectory": str(TRAJECTORY_PATH),
                "trajectory_loaded": trajectory_loaded,
            },
            "scoring": {
                "aggregation": scoring_aggregation,
                "threshold": scoring_threshold,
            },
            "judge_output": json.dumps(judge_output, ensure_ascii=False),
            "openjudge_metadata": metadata,
        }
    }
    _atomic_json(DETAILS_PATH, details)
    _atomic_json(
        REWARD_PATH,
        {"quality": quality_score, "reward": reward_score},
    )


async def _run() -> int:
    config = _read_toml(QUALITY_DIR / "agent_judge.toml")
    rubrics = _build_rubrics(config)
    context = _load_context(config)
    trajectory = _load_trajectory()
    harness, harness_name, model, timeout = _resolve_harness(config)

    grader = AgenticGrader(
        harness=harness,
        rubrics=rubrics,
        model=model,
    )
    result = await grader.aevaluate(
        query=context,
        response=(
            "The candidate's submitted artifacts and all task source files are "
            "available under ./workspace in the judge sandbox."
        ),
        workspace_path=str(WORKSPACE_PATH),
        transcript=trajectory,
    )
    if isinstance(result, GraderError):
        harness_result = harness.last_result
        diagnostic = ""
        if harness_result is not None:
            output = (harness_result.raw_stderr or harness_result.raw_stdout or "")[-4000:]
            diagnostic = (
                f"; exit_code={harness_result.exit_code}"
                f"; timed_out={harness_result.timed_out}"
                f"; output={output!r}"
            )
        raise RuntimeError(f"{result.error}: {result.reason}{diagnostic}")

    _write_success(
        config,
        result,
        harness_name,
        model,
        timeout,
        trajectory is not None,
    )
    return 0


def main() -> int:
    REWARD_PATH.unlink(missing_ok=True)
    DETAILS_PATH.unlink(missing_ok=True)
    _reset_judge_logs()
    try:
        return asyncio.run(_run())
    except Exception as exc:
        # Always emit reward.json so Harbor does not escalate a grader failure
        # into RewardFileNotFoundError (which hides the real diagnostic).
        _persist_runner_error(exc)
        details = {
            "quality": {
                "score": 0.0,
                "kind": "agent",
                "framework": "openjudge-agentic-grader",
                "error": f"{type(exc).__name__}: {exc}",
            }
        }
        _atomic_json(DETAILS_PATH, details)
        _atomic_json(REWARD_PATH, {"quality": 0.0, "reward": 0.0})
        print(f"OpenJudge verifier failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
