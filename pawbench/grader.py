# -*- coding: utf-8 -*-
"""Grading engine for pawbench tasks.

Supports three grading types defined in each task's YAML front-matter:

* ``automated``  — runs the ``grade()`` function embedded in the task Markdown.
* ``llm_judge``  — calls an LLM via the OpenAI-compatible API.
* ``hybrid``     — combines both, with penalty when automated score is too low.

Judge credentials are resolved from environment variables:
  JUDGE_BASE_URL, JUDGE_API_KEY

If those are not set, the judge API call will raise a RuntimeError.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from math import comb
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .task_loader import Task


logger = logging.getLogger(__name__)

DEFAULT_JUDGE_MODEL = "claude-opus-4-5-20251101"
DEFAULT_JUDGE_TIMEOUT_SECONDS = 1800
JUDGE_API_MAX_RETRIES = 100
JUDGE_API_RETRY_BASE_SECONDS = 5
MAX_JUDGE_SUMMARY_CHARS = 120_000
MAX_TEXT_EVENT_CHARS = 20_000

AUTO_PENALTY_THRESHOLD = 0.75


@dataclass
class GradeResult:
    task_id: str
    score: float
    max_score: float
    grading_type: str
    breakdown: Dict[str, float]
    notes: str
    score_simple: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "score": self.score,
            "score_simple": self.score_simple if self.score_simple is not None else self.score,
            "max_score": self.max_score,
            "grading_type": self.grading_type,
            "breakdown": self.breakdown,
            "notes": self.notes,
        }


def grade_task(
    *,
    task: Task,
    execution_result: Dict[str, Any],
    judge_model: str = DEFAULT_JUDGE_MODEL,
    judge_timeout_seconds: float = DEFAULT_JUDGE_TIMEOUT_SECONDS,
    verbose: bool = False,
) -> GradeResult:
    grading_type = task.grading_type

    if grading_type == "automated":
        return _grade_automated(task, execution_result, verbose=verbose)

    if grading_type == "llm_judge":
        return _grade_llm_judge(
            task=task,
            execution_result=execution_result,
            judge_model=judge_model,
            judge_timeout_seconds=judge_timeout_seconds,
            verbose=verbose,
        )

    if grading_type == "hybrid":
        auto_result = _grade_automated(task, execution_result, verbose=verbose)
        llm_result = _grade_llm_judge(
            task=task,
            execution_result=execution_result,
            judge_model=judge_model,
            judge_timeout_seconds=judge_timeout_seconds,
            verbose=verbose,
        )
        return _combine_grades(task, auto_result, llm_result, execution_result)

    raise ValueError(f"Unknown grading type: {grading_type!r}")


# ── automated grading ─────────────────────────────────────────────────────────

def _grade_automated(
    task: Task, execution_result: Dict[str, Any], verbose: bool = False
) -> GradeResult:
    grading_code = _extract_grading_code(task)
    if not grading_code:
        return GradeResult(
            task_id=task.task_id, score=0.0, max_score=1.0,
            grading_type="automated", breakdown={}, notes="No automated grading code found",
        )

    # Diagnostic: print workspace state and pytest availability BEFORE calling grade_func.
    import subprocess as _subprocess_mod
    ws_path = execution_result.get("workspace", "")
    ws_dir = Path(ws_path) if ws_path else None
    ws_exists = ws_dir.is_dir() if ws_dir else False
    ws_files = sorted(p.name for p in ws_dir.iterdir()) if ws_exists else []
    print(
        f"[grader] pre-grade  task={task.task_id}\n"
        f"  workspace_path={ws_path!r}  exists={ws_exists}  files={ws_files}",
        flush=True,
    )
    # Check pytest importability
    try:
        import pytest as _pytest  # noqa: F401
        print(f"[grader] pytest importable v{_pytest.__version__} via {_pytest.__file__}", flush=True)
    except ImportError as _pie:
        print(f"[grader] pytest NOT importable: {_pie}", flush=True)
        # Try running sys.executable -m pytest to see what happens
        import sys as _sys
        _pr = _subprocess_mod.run(
            [_sys.executable, "-m", "pytest", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        print(f"[grader] pytest -m check: rc={_pr.returncode} out={_pr.stdout!r} err={_pr.stderr!r}", flush=True)

    namespace: Dict[str, Any] = {}
    exec(grading_code, namespace)  # noqa: S102
    grade_func = namespace.get("grade")
    if not callable(grade_func):
        return GradeResult(
            task_id=task.task_id, score=0.0, max_score=1.0,
            grading_type="automated", breakdown={}, notes="Automated grading function missing",
        )

    # Patch sys.modules["subprocess"] temporarily so that `import subprocess`
    # inside grade_func picks up our logging wrapper.
    import sys as _sys_mod
    import types as _types_mod
    _orig_subproc = _sys_mod.modules.get("subprocess")
    _logging_mod = _types_mod.ModuleType("subprocess")
    _logging_mod.__dict__.update(vars(_subprocess_mod))

    def _logged_run(*args, **kwargs):  # noqa: D401
        result = _subprocess_mod.run(*args, **kwargs)
        cmd_str = " ".join(str(a) for a in (args[0] if args else []))
        print(
            f"[grader.subprocess.run] cmd={cmd_str!r}\n"
            f"  returncode={result.returncode}\n"
            f"  stdout={getattr(result, 'stdout', b'')!r}\n"
            f"  stderr={getattr(result, 'stderr', b'')!r}",
            flush=True,
        )
        return result

    _logging_mod.run = _logged_run  # type: ignore[attr-defined]
    _sys_mod.modules["subprocess"] = _logging_mod  # type: ignore[assignment]

    try:
        scores = grade_func(
            execution_result.get("transcript", []),
            execution_result.get("workspace", ""),
        )
    finally:
        if _orig_subproc is not None:
            _sys_mod.modules["subprocess"] = _orig_subproc
        else:
            _sys_mod.modules.pop("subprocess", None)

    if not isinstance(scores, dict):
        scores = {}

    print(f"[grader] automated scores for {task.task_id}: {scores}", flush=True)

    if verbose:
        logger.info("Automated grading scores: %s", scores)

    total = _average_scores(scores)
    return GradeResult(
        task_id=task.task_id, score=total, max_score=1.0,
        grading_type="automated", breakdown=_normalize_score_dict(scores), notes="",
    )


# ── LLM judge grading ─────────────────────────────────────────────────────────

def _grade_llm_judge(
    *,
    task: Task,
    execution_result: Dict[str, Any],
    judge_model: str,
    judge_timeout_seconds: float,
    verbose: bool = False,
) -> GradeResult:
    transcript_summary = _summarize_transcript(execution_result.get("transcript", []))
    rubric = task.llm_judge_rubric or _format_grading_criteria(task)
    prompt = _build_judge_prompt(task, transcript_summary, rubric)

    base_url = os.environ.get("JUDGE_BASE_URL")
    api_key = os.environ.get("JUDGE_API_KEY")

    if not base_url or not api_key:
        raise RuntimeError(
            "LLM judge requires JUDGE_BASE_URL and JUDGE_API_KEY environment variables."
        )

    api_model = judge_model.split("/", 1)[-1] if "/" in judge_model else judge_model
    last_exc: Optional[Exception] = None
    for attempt in range(JUDGE_API_MAX_RETRIES):
        if attempt > 0:
            delay = JUDGE_API_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.info("LLM judge: retry %d/%d in %.0fs", attempt + 1, JUDGE_API_MAX_RETRIES, delay)
            time.sleep(delay)
        try:
            response_text = _call_llm_judge_api(
                prompt=prompt, model=api_model,
                base_url=base_url, api_key=api_key,
                timeout_seconds=judge_timeout_seconds,
            )
            if verbose:
                logger.info("Judge raw response (first 2000 chars):\n%s", response_text[:2000])

            raw_parsed = _parse_judge_text_response(response_text)
            parsed = _normalize_judge_response(raw_parsed)
            breakdown = parsed.get("scores", {})
            total = parsed.get("total")
            notes = parsed.get("notes", "")
            return GradeResult(
                task_id=task.task_id,
                score=float(total) if total is not None else 0.0,
                max_score=1.0,
                grading_type="llm_judge",
                breakdown=_normalize_score_dict(breakdown),
                notes=str(notes) if notes is not None else "",
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "LLM judge attempt %d/%d failed: %s", attempt + 1, JUDGE_API_MAX_RETRIES, exc
            )

    raise RuntimeError(
        f"LLM judge API failed after {JUDGE_API_MAX_RETRIES} attempts"
    ) from last_exc


def _is_native_anthropic_url(base_url: str) -> bool:
    """Return True when *base_url* points to the official Anthropic Messages API."""
    return "api.anthropic.com" in base_url


def _call_llm_judge_api(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout_seconds: float = DEFAULT_JUDGE_TIMEOUT_SECONDS,
) -> str:
    if _is_native_anthropic_url(base_url):
        return _call_anthropic_messages_api(
            prompt=prompt, model=model, base_url=base_url,
            api_key=api_key, timeout_seconds=timeout_seconds,
        )
    return _call_openai_compatible_api(
        prompt=prompt, model=model, base_url=base_url,
        api_key=api_key, timeout_seconds=timeout_seconds,
    )


def _call_openai_compatible_api(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout_seconds: float = DEFAULT_JUDGE_TIMEOUT_SECONDS,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    # DashScope's OpenAI-compatible endpoint caps max_tokens at 8192 for most
    # models (e.g. qwen-max); larger values return a 400 InvalidParameter error.
    max_tokens = 8192 if "dashscope.aliyuncs.com" in base_url else 20480
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "curl/8.14.1",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"LLM judge API returned {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM judge API request failed: {exc}") from exc

    # OpenAI-compatible format: {"choices": [{"message": {"content": "..."}}]}
    choices = body.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")

    # Anthropic Messages API format (some proxies forward it as-is)
    anthropic_content = body.get("content", [])
    if anthropic_content:
        text_blocks = [b.get("text", "") for b in anthropic_content if b.get("type") == "text"]
        if text_blocks:
            return "\n".join(text_blocks)

    raise RuntimeError(f"LLM judge API returned unrecognized response format: {body}")


def _call_anthropic_messages_api(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout_seconds: float = DEFAULT_JUDGE_TIMEOUT_SECONDS,
) -> str:
    """Call the native Anthropic Messages API (POST /v1/messages)."""
    base = base_url.rstrip("/")
    # Avoid double /v1 if the caller already included it
    if base.endswith("/v1"):
        url = base + "/messages"
    else:
        url = base + "/v1/messages"

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 20480,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": "curl/8.14.1",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"LLM judge Anthropic API returned {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM judge Anthropic API request failed: {exc}") from exc

    # Anthropic Messages API format: {"content": [{"type": "text", "text": "..."}]}
    anthropic_content = body.get("content", [])
    if anthropic_content:
        text_blocks = [b.get("text", "") for b in anthropic_content if b.get("type") == "text"]
        if text_blocks:
            return "\n".join(text_blocks)

    raise RuntimeError(f"LLM judge Anthropic API returned unrecognized response format: {body}")


# ── hybrid grading ────────────────────────────────────────────────────────────

def _combine_grades(
    task: Task,
    auto_result: GradeResult,
    llm_result: GradeResult,
    execution_result: Optional[Dict[str, Any]] = None,
) -> GradeResult:
    """Combine automated and LLM-judge scores into a hybrid grade.

    Normally the LLM judge contribution is zeroed out when auto_score <
    AUTO_PENALTY_THRESHOLD (to prevent the judge from rescuing a completely
    failed run).  However, if anomaly detection indicates the low auto score
    was caused by a true API communication failure (rate limit, server error,
    terminal API failure), the penalty is skipped so the LLM judge score is
    preserved.

    Note: EMPTY_TRANSCRIPT and ZERO_TOKEN_RESPONSE are intentionally excluded
    from the penalty-skip set.  When the agent produced no output at all, the
    LLM judge has nothing to evaluate and tends to return 1.0 by default,
    which would create an artificial "consolation score" (~0.5) despite a
    complete execution failure.  Only genuine mid-run API interruptions
    (TERMINAL_API_FAILURE, API_RATE_LIMIT, API_SERVER_ERROR) warrant skipping
    the penalty.
    """
    from .utils.anomalies import detect_anomalies, API_FAILURE_IDS

    # Exclude empty-output anomalies: the LLM judge cannot meaningfully score
    # an empty transcript, so preserving its score would be misleading.
    INFRA_FAILURE_IDS = API_FAILURE_IDS - {"EMPTY_TRANSCRIPT", "ZERO_TOKEN_RESPONSE"}

    weights = task.grading_weights or {"automated": 0.5, "llm_judge": 0.5}
    auto_weight = float(weights.get("automated", 0.5))
    llm_weight = float(weights.get("llm_judge", 0.5))
    total_weight = auto_weight + llm_weight or 1.0

    score_simple = (auto_result.score * auto_weight + llm_result.score * llm_weight) / total_weight

    # Detect whether a low automated score is due to a mid-run API failure.
    # If so, use the simple weighted average instead of penalizing the LLM judge.
    skip_penalty = False
    penalty_reason = ""
    if auto_result.score < AUTO_PENALTY_THRESHOLD and execution_result is not None:
        anomaly = detect_anomalies(execution_result, auto_result.notes)
        triggered_ids = {item["id"] for item in anomaly.get("items", [])}
        if triggered_ids & INFRA_FAILURE_IDS:
            skip_penalty = True
            matched = triggered_ids & INFRA_FAILURE_IDS
            penalty_reason = f"[penalty skipped: API failure detected — {', '.join(sorted(matched))}]"

    if skip_penalty:
        score = score_simple
        llm_adj = llm_result.score
    else:
        llm_adj = 0.0 if auto_result.score < AUTO_PENALTY_THRESHOLD else llm_result.score
        score = (auto_result.score * auto_weight + llm_adj * llm_weight) / total_weight

    breakdown = {
        **{f"automated.{k}": v for k, v in auto_result.breakdown.items()},
        **{f"llm_judge.{k}": v for k, v in llm_result.breakdown.items()},
    }
    notes_parts = [p for p in [auto_result.notes, llm_result.notes, penalty_reason] if p]
    notes = " | ".join(notes_parts)
    return GradeResult(
        task_id=task.task_id, score=score, score_simple=score_simple,
        max_score=1.0, grading_type="hybrid", breakdown=breakdown, notes=notes,
    )


# ── statistics helpers ────────────────────────────────────────────────────────

def strict_accuracy_stats(means: List[float]) -> Tuple[float, int]:
    """Return (rate, count) for tasks with a perfect score (== 1.0)."""
    n = len(means)
    if n == 0:
        return 0.0, 0
    perfect = sum(1 for m in means if float(m) >= 1.0 - 1e-9)
    return perfect / n, perfect


def pass_k_stats(grades_by_task_id: Dict[str, Any], runs_per_task: int) -> Dict[str, Any]:
    """Return pass@k and pass^k statistics for k = 1 … *runs_per_task*."""
    result: Dict[str, Any] = {}
    for k in range(1, runs_per_task + 1):
        at_vals: List[float] = []
        pow_vals: List[float] = []
        for g in grades_by_task_id.values():
            runs = g.get("runs", [])
            n = len(runs)
            c = sum(1 for r in runs if r.get("score", 0) >= 1.0 - 1e-9)
            at_vals.append(_pass_at_k(n, c, k))
            pow_vals.append(_pass_pow_k(n, c, k))
        result[f"pass@{k}"] = round(sum(at_vals) / len(at_vals), 4) if at_vals else 0.0
        result[f"pass^{k}"] = round(sum(pow_vals) / len(pow_vals), 4) if pow_vals else 0.0
        result[f"pass@{k}_count"] = sum(1 for v in at_vals if v >= 1.0 - 1e-9)
        result[f"pass^{k}_count"] = sum(1 for v in pow_vals if v >= 1.0 - 1e-9)
    return result


def _pass_at_k(n: int, c: int, k: int) -> float:
    if n < k:
        return 0.0
    if c >= n:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def _pass_pow_k(n: int, c: int, k: int) -> float:
    if n < k:
        return 0.0
    return comb(c, k) / comb(n, k)


# ── internal helpers ──────────────────────────────────────────────────────────

def _extract_grading_code(task: Task) -> str:
    if not task.automated_checks:
        return ""
    m = re.search(r"```python\s*\n(.*?)\n\s*```", task.automated_checks, re.DOTALL)
    return m.group(1) if m else ""


def _average_scores(scores: Dict[str, Any]) -> float:
    values = [float(v) for v in scores.values() if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else 0.0


def _normalize_score_dict(scores: Dict[str, Any]) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for key, value in scores.items():
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            pass
    return result


def _format_grading_criteria(task: Task) -> str:
    return "\n".join(f"- {c}" for c in task.grading_criteria) if task.grading_criteria else ""


def _summarize_transcript(transcript: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for event in transcript:
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        role = msg.get("role")
        if role == "assistant":
            for item in msg.get("content", []):
                if item.get("type") == "toolCall":
                    parts.append(
                        f"Tool: {item.get('name')}({json.dumps(item.get('arguments', {}))})"
                    )
                elif item.get("type") == "text":
                    text = item.get("text", "").strip()
                    if text:
                        parts.append(f"Output:\n{_truncate_middle(text, MAX_TEXT_EVENT_CHARS)}")
        elif role == "toolResult":
            content = msg.get("content", [])
            if content:
                parts.append(f"Result: {str(content[0])[:200]}")
        elif role == "user":
            content = msg.get("content", [])
            if content:
                parts.append(f"User: {content[0]}")

    summary = "\n".join(parts)
    if len(summary) > MAX_JUDGE_SUMMARY_CHARS:
        summary = _truncate_middle(summary, MAX_JUDGE_SUMMARY_CHARS)
    return summary


def _truncate_middle(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep_head = max_chars // 2
    keep_tail = max_chars - keep_head
    omitted = len(text) - (keep_head + keep_tail)
    marker = f"\n\n...[truncated {omitted} chars]...\n\n"
    keep_tail = max(0, keep_tail - len(marker))
    return text[:keep_head] + marker + text[-keep_tail:]


def _build_judge_prompt(task: Task, transcript_summary: str, rubric: str) -> str:
    return (
        "You are a grading function. Your ONLY job is to output a single JSON object.\n\n"
        "CRITICAL RULES:\n"
        "- Do NOT use any tools\n"
        "- Do NOT write any prose or commentary outside the JSON\n"
        "- Respond with ONLY a JSON object — nothing else\n\n"
        "Be a strict evaluator. Reserve 1.0 for genuinely excellent performance.\n\n"
        f"## Task\n{task.prompt}\n\n"
        f"## Expected Behavior\n{task.expected_behavior}\n\n"
        f"## Agent Transcript (summarized)\n{transcript_summary}\n\n"
        f"## Grading Rubric\n{rubric}\n\n"
        "Score each criterion from 0.0 to 1.0.\n\n"
        "Respond with ONLY this JSON (no markdown, no code fences):\n"
        '{"scores": {"criterion_name": 0.0}, "total": 0.0, "notes": "brief justification"}'
    )


def _parse_judge_text_response(raw_text: str) -> Dict[str, Any]:
    raw_text = raw_text.strip()
    if not raw_text:
        return {}

    code_block = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text, re.DOTALL)
    if code_block:
        try:
            parsed = json.loads(code_block.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    json_candidates: List[str] = []
    brace_depth = 0
    current: List[str] = []
    for char in raw_text:
        if char == "{":
            if brace_depth == 0:
                current = []
            brace_depth += 1
        if brace_depth > 0:
            current.append(char)
        if char == "}":
            brace_depth -= 1
            if brace_depth == 0 and current:
                json_candidates.append("".join(current))

    for candidate in reversed(json_candidates):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "scores" in parsed:
                return parsed
        except json.JSONDecodeError:
            pass

    for candidate in reversed(json_candidates):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    score_match = re.search(
        r"(?:total|overall|final)\s*(?:score)?[:\s]*(0\.\d+|1\.0+)", raw_text, re.IGNORECASE
    )
    if score_match:
        try:
            total = float(score_match.group(1))
            if 0.0 <= total <= 1.0:
                logger.warning("Fell back to regex score extraction (total=%.2f)", total)
                return {"scores": {}, "total": total, "notes": "Score extracted from prose"}
        except ValueError:
            pass

    logger.warning("Failed to parse judge response as JSON")
    return {}


def _normalize_judge_response(parsed: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"scores": {}, "total": None, "notes": ""}

    if "scores" in parsed:
        scores_data = parsed["scores"]
        if isinstance(scores_data, dict):
            for key, value in scores_data.items():
                if isinstance(value, dict) and "score" in value:
                    result["scores"][key] = float(value["score"])
                elif isinstance(value, (int, float)):
                    result["scores"][key] = value
    elif "criteria_scores" in parsed:
        criteria = parsed["criteria_scores"]
        if isinstance(criteria, dict):
            for key, value in criteria.items():
                if isinstance(value, dict) and "score" in value:
                    result["scores"][key] = value["score"]
                elif isinstance(value, (int, float)):
                    result["scores"][key] = value

    if "total" in parsed and parsed["total"] is not None:
        result["total"] = float(parsed["total"])
    elif "score" in parsed and isinstance(parsed["score"], (int, float)):
        result["total"] = float(parsed["score"])
    elif result["scores"]:
        values = [v for v in result["scores"].values() if isinstance(v, (int, float))]
        if values:
            result["total"] = sum(values) / len(values)

    for key in ("notes", "justification", "reasoning"):
        if key in parsed:
            result["notes"] = str(parsed[key])
            break

    return result
