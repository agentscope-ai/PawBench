#!/usr/bin/env python3
"""Run OpenJudge's harness-based AgenticGrader and emit Harbor rewards."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import types
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from input_contract import validate_atif
from openjudge.graders.agentic_grader import AgenticGrader
from openjudge.graders.schema import Checkpoint, GraderError, Rubric
from openjudge.harness import ClaudeCodeHarness, CodexHarness, CursorAgentHarness

TESTS_DIR = Path("/tests")
QUALITY_DIR = TESTS_DIR / "quality"
RESULT_DIR = TESTS_DIR / "result"
WS_RESULT_DIMENSION_PATH = Path("/logs/verifier/ws_result_dimension.json")
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
PROVENANCE_SOURCE_PATH = QUALITY_DIR / "pawbench-provenance.json"
PROVENANCE_PATH = Path("/logs/verifier/openjudge-provenance.json")
INPUT_READINESS_PATH = Path("/logs/verifier/openjudge-input-readiness.json")

MAX_ATTEMPTS = 3

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

    def __init__(self, delegate: Any, attempt: int = 1):
        self.delegate = delegate
        self.last_result: Any = None
        self.invocation_count = 0
        self.attempt = attempt

    def run(
        self,
        sandbox_dir: Path,
        prompt: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> Any:
        self.invocation_count += 1
        self.last_result = self.delegate.run(sandbox_dir, prompt, schema, model)
        _persist_harness_logs(
            sandbox_dir=sandbox_dir,
            harness_result=self.last_result,
            invocation=self.invocation_count,
            model=model,
            binary=str(getattr(self.delegate, "binary", "")),
            prompt=prompt,
            schema=schema,
            attempt=self.attempt,
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
    attempt: int = 1,
) -> None:
    """Persist the judge CLI's full file protocol and native process streams.

    ``ProcessSandbox`` deletes its temporary directory after the grader call.
    Copying these files while ``RecordingHarness.run`` is still executing is
    therefore the only reliable way to retain the exact judge inputs, output,
    tool-call stream, stderr, and process status. Naming includes the retry
    ``attempt`` number so that logs from earlier failed attempts are never
    overwritten by later retries.
    """
    run_name = f"attempt-{attempt:02d}-run-{invocation:03d}"
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
        "attempt": attempt,
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


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(_read_text(path))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _record_input_readiness(
    *,
    ready: bool,
    error: str | None = None,
    trajectory: dict[str, Any] | None = None,
) -> None:
    provenance = _load_json_object(PROVENANCE_SOURCE_PATH)
    if provenance is not None:
        _atomic_json(PROVENANCE_PATH, provenance)
    payload = {
        "ready": ready,
        "error": error,
        "trajectory_path": str(TRAJECTORY_PATH),
        "trajectory_sha256": (
            hashlib.sha256(TRAJECTORY_PATH.read_bytes()).hexdigest()
            if TRAJECTORY_PATH.is_file()
            else None
        ),
        "schema_version": trajectory.get("schema_version") if trajectory else None,
        "step_count": len(trajectory.get("steps", [])) if trajectory else 0,
        "provenance_present": provenance is not None,
    }
    _atomic_json(INPUT_READINESS_PATH, payload)


def _load_trajectory() -> list[dict[str, Any]]:
    if not TRAJECTORY_PATH.is_file():
        message = f"ATIF trajectory is missing: {TRAJECTORY_PATH}"
        _record_input_readiness(ready=False, error=message)
        raise FileNotFoundError(message)
    try:
        payload = validate_atif(json.loads(_read_text(TRAJECTORY_PATH)))
    except (json.JSONDecodeError, ValueError) as exc:
        _record_input_readiness(ready=False, error=str(exc))
        raise ValueError(f"OpenJudge input trajectory is not valid ATIF: {exc}") from exc
    _record_input_readiness(ready=True, trajectory=payload)

    steps = payload.get("steps")

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


def _ensure_harness_cli(harness_name: str) -> str:
    """Make sure the judge CLI exists in the shared task environment.

    OpenJudge agent-judge tasks run the verifier *inside* the task container
    (``environment_mode=shared``). If the required CLI is missing, this fails
    immediately with a precise error rather than attempting to install it;
    the task image must pre-install the judge CLI it declares.
    """
    if harness_name in {"claude", "claude-code"}:
        binary = "claude"
        local_bin = Path.home() / ".local" / "bin"
        _ensure_path_contains(local_bin)
        existing = _which_cli(binary)
        if existing:
            return existing
        raise RuntimeError(
            "OpenJudge judge requires the `claude` CLI, but it is missing "
            "from this task environment. Pre-install Claude Code in the "
            "task image, or evaluate with `harbor:claude-code` so Harbor "
            "installs it."
        )

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


def _resolve_harness(
    config: dict[str, Any], attempt: int = 1
) -> tuple[Any, str, str | None, float]:
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
    harness = RecordingHarness(
        harness_type(binary=cli_path, timeout_s=timeout), attempt=attempt
    )
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


def _stub_rewardkit() -> None:
    """``tests/result/*.py`` imports ``from rewardkit import criterion``.

    The openjudge branch of ``test.sh`` runs under ``uv run --with py-openjudge
    --with tomli`` (no ``harbor-rewardkit`` install), so provide a no-op
    ``@criterion`` decorator instead of pulling in the real package.
    """
    if "rewardkit" in sys.modules:
        return
    fake = types.ModuleType("rewardkit")
    fake.criterion = lambda f: f
    sys.modules["rewardkit"] = fake


def _compute_result_dimension() -> tuple[float, dict[str, Any]] | None:
    """Score ``tests/result/`` (programmatic fact_tokens/source/shortcut check).

    ``test.sh`` dispatches openjudge tasks to this script, which historically
    only ever scored ``tests/quality/`` — ``tests/result/`` (present on ws-*
    tasks alongside agent_judge.toml) was silently skipped, a bug fixed here
    so the "result" dimension is folded back into "score"/"reward" the same
    way ``reward.toml``'s weighted_mean/threshold semantics declare it.
    Returns ``None`` (falling back to quality-only scoring) if there is no
    ``tests/result/score.py``, or if the programmatic grader itself errors.
    """
    score_py = RESULT_DIR / "score.py"
    if not score_py.is_file():
        return None
    try:
        _stub_rewardkit()
        sys.modules.pop("ua_task_score", None)
        spec = importlib.util.spec_from_file_location(
            "_pawbench_ws_result_score", score_py
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result_score = float(module.ua_contract_score(WORKSPACE_PATH))
    except Exception as exc:  # noqa: BLE001
        print(
            f"OpenJudge verifier: tests/result/score.py failed, "
            f"falling back to quality-only score: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None

    debug_path = Path("/logs/verifier/ua_task_score.json")
    detail = _load_json_object(debug_path) or {}
    if debug_path.is_file():
        try:
            shutil.move(str(debug_path), str(WS_RESULT_DIMENSION_PATH))
        except OSError:
            pass
    return round(result_score, 4), detail


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

    # ws-* tasks additionally ship a programmatic tests/result/ grader
    # (fact_tokens / required_source / no_shortcut) alongside the agentic
    # tests/quality/ judge. Fold it in as score = mean(quality, result) —
    # see _compute_result_dimension for why this was previously skipped.
    result_dimension = _compute_result_dimension()
    if result_dimension is not None:
        result_score, result_detail = result_dimension
        combined_score = round((quality_score + result_score) / 2.0, 4)
    else:
        result_score = None
        result_detail = None
        combined_score = quality_score

    reward_config = _read_toml(TESTS_DIR / "reward.toml")
    reward_entries = reward_config.get("reward", [])
    # reward.toml declares multiple [[reward]] tables collapsing the single
    # "quality" dimension in different ways (e.g. a continuous "score" via
    # weighted_mean, and a binary pass/fail "reward" via threshold). Each
    # entry must be matched by its own `name`, not by position: a previous
    # version took reward_entries[0] as *the* pass/fail rule, which happened
    # to be the "score" (weighted_mean, no threshold) table and silently let
    # the raw continuous quality score stand in for "reward", unthresholded.
    extra_scores: dict[str, float] = {}
    reward_score = combined_score
    for entry in reward_entries:
        entry_name = str(entry.get("name") or "").strip()
        if not entry_name:
            continue
        aggregation = str(entry.get("aggregation", "weighted_mean"))
        threshold = float(entry.get("threshold", 0.8))
        value = _aggregate(
            combined_score,
            [combined_score > 0.0],
            aggregation,
            threshold,
        )
        if entry_name == "reward":
            reward_score = value
        else:
            extra_scores[entry_name] = value

    details = {
        "quality": {
            "score": quality_score,
            "raw_score": raw_score,
            "valid": True,
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
                "input_readiness": _load_json_object(INPUT_READINESS_PATH),
                "provenance": _load_json_object(PROVENANCE_PATH),
            },
            "scoring": {
                "aggregation": scoring_aggregation,
                "threshold": scoring_threshold,
            },
            "judge_output": json.dumps(judge_output, ensure_ascii=False),
            "openjudge_metadata": metadata,
        }
    }
    if result_dimension is not None:
        details["result"] = {
            "score": result_score,
            "raw_score": result_score,
            "valid": True,
            "kind": "programmatic",
            "framework": "ua_task_score.py (tests/result/)",
            "detail": result_detail,
        }
    _atomic_json(DETAILS_PATH, details)
    _atomic_json(
        REWARD_PATH,
        {
            "quality": quality_score,
            **({"result": result_score} if result_dimension is not None else {}),
            **extra_scores,
            "reward": reward_score,
            "valid": True,
        },
    )


def _write_invalid_result(attempt_errors: list[str]) -> None:
    """Persist the terminal failure state once every retry has been exhausted.

    This is the only place a failed run still produces a score: Harbor
    requires ``reward.json`` to exist, so we mark the task ``valid: false``
    instead of hiding the failure behind a silently-recovered score.
    """
    details = {
        "quality": {
            "score": 0.0,
            "kind": "agent",
            "framework": "openjudge-agentic-grader",
            "valid": False,
            "attempts": attempt_errors,
            "error": attempt_errors[-1] if attempt_errors else "unknown error",
            "input_readiness": _load_json_object(INPUT_READINESS_PATH),
            "provenance": _load_json_object(PROVENANCE_PATH),
        }
    }
    _atomic_json(DETAILS_PATH, details)
    _atomic_json(REWARD_PATH, {"quality": 0.0, "reward": 0.0, "valid": False})


async def _run(attempt: int = 1) -> int:
    config = _read_toml(QUALITY_DIR / "agent_judge.toml")
    rubrics = _build_rubrics(config)
    context = _load_context(config)
    trajectory = _load_trajectory()
    harness, harness_name, model, timeout = _resolve_harness(config, attempt)

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
    INPUT_READINESS_PATH.unlink(missing_ok=True)
    PROVENANCE_PATH.unlink(missing_ok=True)
    _reset_judge_logs()

    attempt_errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return asyncio.run(_run(attempt))
        except Exception as exc:
            message = f"attempt {attempt}/{MAX_ATTEMPTS}: {type(exc).__name__}: {exc}"
            print(f"OpenJudge verifier {message}", file=sys.stderr)
            attempt_errors.append(message)

    # Every retry failed: mark the task invalid instead of silently scoring 0.
    _write_invalid_result(attempt_errors)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
