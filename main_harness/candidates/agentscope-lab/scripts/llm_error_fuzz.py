#!/usr/bin/env python3
"""Generate diverse boundary-error samples with an LLM and replay local routing."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CANDIDATE_ROOT.parents[2]
for value in (PROJECT_ROOT, PROJECT_ROOT / "main_harness", CANDIDATE_ROOT / "src"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from pawbench_agentscope._atomic_io import atomic_write_text, prepare_marked_output, read_text_no_follow
from pawbench_agentscope._portable_security import redact_sensitive_text, redact_sensitive_value
from pawbench_agentscope.error_codes import ERROR_CODES, classify_bridge_error


SCHEMA_VERSION = "harness-core-llm-error-fuzz/v1"
REVIEW_SCHEMA_VERSION = "harness-core-llm-error-fuzz-review/v1"
MARKER = ".harness-core-llm-error-fuzz"
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_CASES_PER_REQUEST = 4
MAX_GENERATION_REQUESTS = 12
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
CODE_DEFINITIONS = {
    "HC_CONFIG_INVALID_FEATURE": "unknown or invalid Harness Feature ID/switch",
    "HC_INPUT_CONTRACT_INVALID": "bad local input path, file, type, or task contract",
    "HC_PREFLIGHT_FAILED": "workspace/dependency/readiness preflight failed",
    "HC_PROVIDER_MODEL_NOT_FOUND": "requested external model is absent or inaccessible",
    "HC_PROVIDER_AUTH": "external provider authentication or authorization rejected",
    "HC_PROVIDER_RATE_LIMIT": "external provider rate or quota limit",
    "HC_PROVIDER_UNAVAILABLE": "external transport, DNS, TLS, overload, or 5xx outage",
    "HC_RUNTIME_TIMEOUT": "Harness runtime budget or task execution timed out",
    "HC_RUNTIME_ERROR": "other internal Harness runtime failure",
}


ModelCaller = Callable[..., str]


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _strict_json_loads(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite_json,
    )


def build_prompt(cases_per_code: int) -> str:
    definitions = json.dumps(CODE_DEFINITIONS, sort_keys=True)
    return (
        "Generate a realistic fuzz corpus for a deterministic agent-to-Harbor error router. "
        f"Return exactly one JSON object {{\"cases\": [...]}} with exactly {cases_per_code} "
        "cases for each definition below. Each case must contain case_id, expected_code, "
        "error_type, error, and runtime_context. runtime_context must be an object containing "
        "only optional error_type and error strings. Use diverse SDK class names and message "
        "forms such as HTTP/status representations, but never put HC_* names in error_type, "
        "error, or runtime_context. Do not include credentials, personal data, URLs with query "
        "parameters, or ambiguous multi-failure cases. Every case must have one unambiguous "
        "expected_code. case_id must be unique lowercase [a-z0-9._-] text of at most "
        "80 characters. Keep each error under 240 characters.\n\nDefinitions:\n" + definitions
    )


def parse_cases(text: str, *, cases_per_code: int) -> list[dict[str, Any]]:
    try:
        payload = _strict_json_loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("generator response is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"cases"}:
        raise ValueError("generator response must contain exactly one cases field")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("cases must be an array")
    expected_total = len(ERROR_CODES) * cases_per_code
    if len(raw_cases) != expected_total:
        raise ValueError(f"expected {expected_total} generated cases, got {len(raw_cases)}")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    for index, raw in enumerate(raw_cases):
        if not isinstance(raw, Mapping):
            raise ValueError(f"case {index} is not an object")
        if set(raw) != {"case_id", "expected_code", "error_type", "error", "runtime_context"}:
            raise ValueError(f"case {index} has unexpected fields")
        case_id = raw.get("case_id")
        expected_code = raw.get("expected_code")
        error_type = raw.get("error_type")
        error = raw.get("error")
        context = raw.get("runtime_context")
        if not isinstance(case_id, str):
            raise ValueError(f"case {index} has a non-string case_id")
        if not SAFE_ID.fullmatch(case_id) or case_id in seen:
            # The identifier is bookkeeping only. Replace unsafe or repeated
            # generator labels deterministically; never relax semantic fields.
            case_id = f"generated-{index + 1:04d}"
            while case_id in seen:
                case_id = f"generated-{index + 1:04d}-{len(seen)}"
        if expected_code not in ERROR_CODES:
            raise ValueError(f"case {case_id} has an unsupported expected_code")
        if not isinstance(error_type, str) or not 1 <= len(error_type) <= 120:
            raise ValueError(f"case {case_id} has an invalid error_type")
        if not isinstance(error, str) or not 1 <= len(error) <= 500:
            raise ValueError(f"case {case_id} has an invalid error")
        if not isinstance(context, Mapping) or set(context) - {"error_type", "error"}:
            raise ValueError(f"case {case_id} has an invalid runtime_context")
        if any(not isinstance(value, str) or len(value) > 500 for value in context.values()):
            raise ValueError(f"case {case_id} has invalid runtime_context values")
        untrusted_text = json.dumps(
            {"error_type": error_type, "error": error, "runtime_context": dict(context)},
            sort_keys=True,
        )
        if "hc_" in untrusted_text.casefold():
            raise ValueError(f"case {case_id} leaks the target code into classifier input")
        seen.add(case_id)
        counts[str(expected_code)] += 1
        cases.append(
            redact_sensitive_value(
                {
                    "case_id": case_id,
                    "expected_code": expected_code,
                    "error_type": error_type,
                    "error": error,
                    "runtime_context": dict(context),
                }
            )
        )
    if counts != Counter({code: cases_per_code for code in ERROR_CODES}):
        raise ValueError("generated corpus does not balance every stable error code")
    return cases


def classify_cases(cases: Sequence[Mapping[str, Any]], *, model: str) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case in cases:
        value = classify_bridge_error(
            error_type=str(case["error_type"]),
            error=str(case["error"]),
            runtime_context=case.get("runtime_context"),
        )
        expected = str(case["expected_code"])
        records.append(
            {
                **dict(case),
                "observed_code": value.error_code,
                "failure_scope": value.failure_scope,
                "retryable": value.retryable,
                "cause_type": value.cause_type,
                "matched": value.error_code == expected,
            }
        )
    matched = sum(bool(item["matched"]) for item in records)
    return redact_sensitive_value(
        {
            "schema_version": SCHEMA_VERSION,
            "mode": "synthetic_shadow_non_authoritative",
            "model": model,
            "runtime_design_modified": False,
            "case_count": len(records),
            "matched_count": matched,
            "accuracy": matched / len(records) if records else 0.0,
            "all_matched": matched == len(records),
            "expected_code_counts": dict(Counter(item["expected_code"] for item in records)),
            "observed_code_counts": dict(Counter(item["observed_code"] for item in records)),
            "records": records,
        }
    )


def apply_human_review(
    summary: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    if set(review) != {"schema_version", "exclusions"}:
        raise ValueError("review must contain exactly schema_version and exclusions")
    if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise ValueError("review has an unsupported schema_version")
    exclusions = review.get("exclusions")
    if not isinstance(exclusions, list):
        raise ValueError("review exclusions must be an array")
    by_id = {item["case_id"]: item for item in summary["records"]}
    excluded: dict[str, str] = {}
    for index, item in enumerate(exclusions):
        if not isinstance(item, Mapping) or set(item) != {"case_id", "reason"}:
            raise ValueError(f"review exclusion {index} must contain case_id and reason")
        case_id = item.get("case_id")
        reason = item.get("reason")
        if not isinstance(case_id, str) or case_id not in by_id or case_id in excluded:
            raise ValueError(f"review exclusion {index} has an unknown or duplicate case_id")
        if by_id[case_id]["matched"]:
            raise ValueError(f"review cannot exclude an already matched case: {case_id}")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError(f"review exclusion {case_id} needs a concise reason")
        excluded[case_id] = redact_sensitive_text(reason.strip())
    included = [item for item in summary["records"] if item["case_id"] not in excluded]
    matched = sum(bool(item["matched"]) for item in included)
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_type": "human_generator-label_adjudication",
        "excluded_count": len(excluded),
        "excluded": [
            {"case_id": case_id, "reason": reason}
            for case_id, reason in excluded.items()
        ],
        "adjudicated_case_count": len(included),
        "adjudicated_matched_count": matched,
        "adjudicated_accuracy": matched / len(included) if included else 0.0,
        "all_adjudicated_matched": bool(included) and matched == len(included),
    }


def generate_cases(
    caller: ModelCaller,
    *,
    cases_per_code: int,
) -> list[dict[str, Any]]:
    pending_sizes: list[int] = []
    remaining = cases_per_code
    while remaining:
        size = min(remaining, MAX_CASES_PER_REQUEST)
        pending_sizes.append(size)
        remaining -= size

    cases: list[dict[str, Any]] = []
    iteration = 0
    while pending_sizes:
        size = pending_sizes.pop(0)
        iteration += 1
        if iteration > MAX_GENERATION_REQUESTS:
            raise ValueError(
                f"generator exceeded {MAX_GENERATION_REQUESTS} bounded requests"
            )
        raw = caller(build_prompt(size), stage="fuzz", iteration=iteration)
        try:
            batch = parse_cases(raw, cases_per_code=size)
        except ValueError as original_error:
            batch = []
            accepted_size = 0
            # Some models return a smaller but otherwise complete balanced
            # corpus. Preserve that validated evidence and request only the
            # missing remainder instead of discarding a usable response.
            for candidate_size in range(size - 1, 0, -1):
                try:
                    batch = parse_cases(raw, cases_per_code=candidate_size)
                except ValueError:
                    continue
                accepted_size = candidate_size
                break
            if accepted_size:
                pending_sizes.insert(0, size - accepted_size)
            elif size > 1:
                left = size // 2
                pending_sizes[0:0] = [left, size - left]
                continue
            else:
                raise original_error

        for index, case in enumerate(batch, start=1):
            # Independent or adaptive batches commonly reuse short IDs.
            # Prefixing with deterministic coordinates preserves a safe,
            # auditable key without trusting generator uniqueness globally.
            original = str(case["case_id"])
            prefix = f"batch-{iteration}-{index}-"
            case["case_id"] = (prefix + original)[:80]
        cases.extend(batch)
    counts = Counter(str(case["expected_code"]) for case in cases)
    if counts != Counter({code: cases_per_code for code in ERROR_CODES}):
        raise ValueError("adaptive generator did not balance every stable error code")
    return cases


def _prepare_output(root: Path) -> None:
    prepare_marked_output(
        root,
        marker_name=MARKER,
        marker_text=SCHEMA_VERSION + "\n",
        replace=False,
    )


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(redact_sensitive_value(payload), ensure_ascii=False, indent=2) + "\n",
    )


def _write_report(root: Path, summary: Mapping[str, Any]) -> None:
    mismatches = [item for item in summary["records"] if not item["matched"]]
    rows = "\n".join(
        f"| `{item['case_id']}` | `{item['expected_code']}` | `{item['observed_code']}` | "
        f"`{item['error_type']}` |"
        for item in mismatches
    ) or "| — | — | — | — |"
    review = summary.get("human_review") if isinstance(summary.get("human_review"), Mapping) else None
    review_line = (
        f"- adjudicated: {review['adjudicated_matched_count']}/{review['adjudicated_case_count']} "
        f"after {review['excluded_count']} documented generator-label exclusions\n"
        if review
        else ""
    )
    report = (
        "# LLM-Generated Harbor Boundary Error Fuzz\n\n"
        f"- model: `{summary['model']}`\n"
        f"- matched: {summary['matched_count']}/{summary['case_count']}\n"
        f"{review_line}"
        "- status: synthetic shadow evidence; no runtime design mutation\n\n"
        "| Case | Expected | Observed | Cause |\n"
        "| --- | --- | --- | --- |\n"
        f"{rows}\n"
    )
    atomic_write_text(root / "REPORT_EN.md", report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument("--cases-per-code", type=int, default=4)
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY")
    parser.add_argument("--base-url")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 1 <= args.cases_per_code <= 6:
        parser.error("--cases-per-code must be in [1, 6]")
    # Do not resolve away a final-component symlink before the ownership guard.
    root = args.output.expanduser().absolute()
    try:
        _prepare_output(root)
        root = root.resolve()
        if args.input:
            raw = read_text_no_follow(args.input.expanduser(), max_bytes=MAX_INPUT_BYTES)
            cases = parse_cases(raw, cases_per_code=args.cases_per_code)
        else:
            try:
                from main_reasoning.simple_v2.client import (
                    OpenAIJSONCaller,
                    resolve_api_key,
                    resolve_base_url,
                )
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "LLM fuzz generation requires the optional main_reasoning package"
                ) from exc
            caller = OpenAIJSONCaller(
                model=args.model,
                api_key=resolve_api_key(args.api_key_env),
                base_url=resolve_base_url(args.base_url),
                max_retries=1,
                stage_max_tokens={"fuzz": 8_000},
            )
            cases = generate_cases(caller, cases_per_code=args.cases_per_code)
        generated = {"cases": cases}
        _write_json(root / "generated_cases.json", generated)
        summary = classify_cases(cases, model=args.model)
        if args.review:
            review_payload = _strict_json_loads(
                read_text_no_follow(args.review.expanduser(), max_bytes=MAX_INPUT_BYTES)
            )
            if not isinstance(review_payload, Mapping):
                raise ValueError("review file must contain one JSON object")
            summary["human_review"] = apply_human_review(summary, review_payload)
        _write_json(root / "summary.json", summary)
        _write_report(root, summary)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(redact_sensitive_text(str(exc)))
    compact = {key: summary[key] for key in ("case_count", "matched_count", "accuracy", "all_matched")}
    if "human_review" in summary:
        compact["human_review"] = summary["human_review"]
    print(json.dumps(compact, sort_keys=True))
    accepted = (
        summary["human_review"]["all_adjudicated_matched"]
        if "human_review" in summary
        else summary["all_matched"]
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
