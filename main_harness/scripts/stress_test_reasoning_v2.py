from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.feature_taxonomy import (  # noqa: E402
    CODE_TABLE,
    FEATURE_NAMES,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
    select_features_for_evidence,
)
from scripts.paths import HARNESS_WORK_ROOT  # noqa: E402
from scripts.security import (  # noqa: E402
    redact_sensitive_text,
    redact_sensitive_value,
    resolve_openai_compatible_provider,
    safe_provider_error,
)


DEFAULT_MODELS = ("deepseek-v4-pro", "qwen3.7-max")
DEFAULT_OUT = HARNESS_WORK_ROOT / "reasoning_v2_api_stress_20260710"


CASES: tuple[dict[str, Any], ...] = (
    {"id": "workspace_binding", "evidence": "The workspace mount was bound to the wrong CWD, so the artifact path resolved under the wrong root.", "codes": ["H1"], "features": ["F1.1"]},
    {"id": "readiness_reset", "evidence": "Preflight was skipped; a required dependency was missing and the fixture remained stale after reset.", "codes": ["H1"], "features": ["F1.2"]},
    {"id": "isolation_permissions", "evidence": "The harness permission policy denied a valid write inside the sandbox even though the task allowed it.", "codes": ["H1"], "features": ["F1.3"]},
    {"id": "action_contract", "evidence": "The registered tool schema rejected a valid argument that matched the declared action contract.", "codes": ["H2"], "features": ["F2.1"]},
    {"id": "tool_availability", "evidence": "The required write_file tool was unavailable and hidden from the tool registry.", "codes": ["H2"], "features": ["F2.2"]},
    {"id": "result_error_feedback", "evidence": "The tool result claimed false success, omitted exit status, and returned malformed error feedback.", "codes": ["H2"], "features": ["F2.3"]},
    {"id": "completion_termination", "evidence": "The run reported completion and then stopped prematurely before the required artifact; the stop condition was wrong.", "codes": ["H3"], "features": ["F3.1"]},
    {"id": "budget_guards", "evidence": "The run hit max iteration, token budget, and timeout cutoff before completion.", "codes": ["H3"], "features": ["F3.2"]},
    {"id": "recovery_resume", "evidence": "The tool failure was recoverable, but the harness made no retry, repair attempt, or resume.", "codes": ["H3"], "features": ["F3.3"]},
    {"id": "diagnostic_trace", "evidence": "The exception was swallowed and the transcript omitted the diagnostic event needed to explain the failure.", "codes": ["H4"], "features": ["F4.1"]},
    {"id": "state_artifact_deltas", "evidence": "The artifact file changed, but there was no before/after state delta or artifact manifest.", "codes": ["H4"], "features": ["F4.2"]},
    {"id": "verification", "evidence": "The verifier failed, yet the acceptance gate reported false success and accepted the run.", "codes": ["H4"], "features": ["F4.3"]},
    {"id": "context_assembly", "evidence": "A required SKILL instruction was not injected into context assembly or the active prompt.", "codes": ["H5"], "features": ["F5.1"]},
    {"id": "persistent_memory", "evidence": "The record existed in the persistent memory store, but retrieval returned no record for the next run.", "codes": ["H5"], "features": ["F5.2"]},
    {"id": "compaction", "evidence": "The compaction summary omitted a required fact, and reconstructed context lost that fact.", "codes": ["H5"], "features": ["F5.3"]},
    {"id": "external_only", "evidence": "A healthy harness stopped because the provider returned persistent 503 service unavailable responses.", "codes": ["Ex-3"], "features": []},
    {"id": "external_plus_recovery", "evidence": "The provider returned persistent 503 responses; the harness classified them as recoverable but skipped the required retry.", "codes": ["Ex-3", "H3"], "features": ["F3.3"]},
    {"id": "no_clear_error", "evidence": "The run completed, produced the artifact, passed verification, and contains no failure evidence.", "codes": [], "features": []},
    {"id": "availability_plus_feedback", "evidence": "One required tool was unavailable; a second tool returned malformed error feedback with no exit status.", "codes": ["H2"], "features": ["F2.2", "F2.3"]},
    {"id": "trace_plus_verification", "evidence": "The transcript omitted the verifier event, and the acceptance gate still reported false success.", "codes": ["H4"], "features": ["F4.1", "F4.3"]},
)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_shell_env() -> dict[str, str]:
    env = dict(os.environ)
    if any(env.get(name) for name in ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", "ALIYUN_API_KEY")):
        return env
    proc = subprocess.run(
        ["zsh", "-ic", "env"],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        return env
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key and value:
                env.setdefault(key, value)
    return env


def api_settings() -> tuple[str, str]:
    settings = resolve_openai_compatible_provider(
        load_shell_env(),
        allowed_providers=("dashscope",),
    )
    return settings.api_key, settings.base_url


def taxonomy_prompt(cases: list[dict[str, Any]]) -> str:
    cases = redact_sensitive_value(cases)
    code_rows = [
        f"- {code}: {CODE_TABLE[code].short_name}. {CODE_TABLE[code].assign_when}"
        for code in ("Ex-3", "H1", "H2", "H3", "H4", "H5")
    ]
    feature_rows = [
        f"- {h_code}: " + "; ".join(f"{feature_id} {FEATURE_NAMES[feature_id]}" for feature_id in feature_ids)
        for h_code, feature_ids in H_TO_FEATURES.items()
    ]
    input_cases = [{"id": case["id"], "evidence": case["evidence"]} for case in cases]
    prompt = f"""Classify each received PawBench result under taxonomy {TAXONOMY_VERSION}.

Error codes:
{chr(10).join(code_rows)}

H-to-F candidate map:
{chr(10).join(feature_rows)}

Rules:
- Use only direct evidence. Never infer from score.
- Select zero or one F-code normally. Select two only when two distinct evidence clauses support them.
- Every F-code must belong to an assigned H-code and include an exact evidence quote.
- Ex-3 is external and never maps automatically to an F-code.
- A result may use Ex-3 plus an H/F only when the evidence separately shows bad harness handling.
- Never emit H6 or a legacy feature meaning.
- Return an empty code and feature list when there is no clear error.

Return one JSON object exactly in this form:
{{
  "taxonomy_version": "{TAXONOMY_VERSION}",
  "results": [
    {{
      "id": "case id",
      "codes": ["H2"],
      "features": [{{"feature_id": "F2.3", "h_code": "H2", "evidence_quote": "exact substring"}}],
      "reason": "short"
    }}
  ]
}}

Cases:
{json.dumps(input_cases, ensure_ascii=False, indent=2)}
"""
    return redact_sensitive_text(prompt)


def extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def build_request_payload(model: str, prompt: str) -> dict[str, Any]:
    prompt = redact_sensitive_text(prompt)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a strict harness attribution auditor. Return JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "stream": False,
    }
    if model == "kimi-k2.7-code":
        # DashScope exposes this model as thinking-only without structured output.
        payload["max_completion_tokens"] = 32_768
    else:
        payload.update(
            {
                "max_tokens": 8_000,
                "thinking": {"type": "disabled"},
                "response_format": {"type": "json_object"},
            }
        )
    return payload


def call_model(model: str, prompt: str, *, timeout: int, retries: int) -> dict[str, Any]:
    api_key, base_url = api_settings()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = build_request_payload(model, prompt)
    last_error = ""
    started = time.monotonic()
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            if response.status_code >= 400 and attempt == 0 and (
                "response_format" in payload or "thinking" in payload
            ):
                payload.pop("response_format", None)
                payload.pop("thinking", None)
                last_error = safe_provider_error(
                    status_code=response.status_code,
                    headers=response.headers,
                )
                continue
            if response.status_code >= 400:
                last_error = safe_provider_error(
                    status_code=response.status_code,
                    headers=response.headers,
                )
                if attempt < retries:
                    time.sleep(1.0 + attempt)
                    continue
                break
            response.raise_for_status()
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            raw_content = message.get("content") or ""
            if not raw_content.strip():
                reasoning = message.get("reasoning_content") or ""
                raise ValueError(
                    "empty response content "
                    f"(finish_reason={choice.get('finish_reason')!r}, "
                    f"reasoning_length={len(reasoning)})"
                )
            content = redact_sensitive_text(raw_content)
            try:
                parsed = extract_json(content)
            except json.JSONDecodeError:
                parsed = redact_sensitive_value(extract_json(raw_content))
                content = json.dumps(parsed, ensure_ascii=False)
            result = {
                "ok": True,
                "model": model,
                "response_id": body.get("id"),
                "provider_model": body.get("model"),
                "latency_seconds": round(time.monotonic() - started, 3),
                "usage": body.get("usage", {}),
                "raw_content": content,
                "parsed": redact_sensitive_value(parsed),
            }
            return redact_sensitive_value(result)
        except Exception as exc:
            last_error = safe_provider_error(exc=exc)
            if attempt < retries:
                time.sleep(1.0 + attempt)
    return {
        "ok": False,
        "model": model,
        "latency_seconds": round(time.monotonic() - started, 3),
        "error": last_error,
    }


def validate_response(response: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not response.get("ok"):
        return {"ok": False, "errors": [response.get("error", "API call failed")], "cases": []}
    parsed = response["parsed"]
    errors: list[str] = []
    if parsed.get("taxonomy_version") != TAXONOMY_VERSION:
        errors.append("wrong taxonomy_version")
    result_rows = parsed.get("results")
    if not isinstance(result_rows, list):
        return {"ok": False, "errors": errors + ["results is not a list"], "cases": []}
    expected_by_id = {case["id"]: case for case in cases}
    actual_by_id = {row.get("id"): row for row in result_rows if isinstance(row, dict)}
    if set(actual_by_id) != set(expected_by_id):
        errors.append("missing or extra case ids")

    case_results: list[dict[str, Any]] = []
    allowed_codes = {"Ex-3", "H1", "H2", "H3", "H4", "H5"}
    for case_id, expected in expected_by_id.items():
        actual = actual_by_id.get(case_id, {})
        row_errors: list[str] = []
        codes = actual.get("codes") if isinstance(actual.get("codes"), list) else []
        code_set = {str(code) for code in codes}
        if not code_set <= allowed_codes or "H6" in code_set:
            row_errors.append("unknown or legacy code")
        if code_set != set(expected["codes"]):
            row_errors.append(f"codes expected={expected['codes']} actual={sorted(code_set)}")
        features = actual.get("features") if isinstance(actual.get("features"), list) else []
        feature_ids = [str(item.get("feature_id")) for item in features if isinstance(item, dict)]
        if set(feature_ids) != set(expected["features"]):
            row_errors.append(f"features expected={expected['features']} actual={feature_ids}")
        by_h: Counter[str] = Counter()
        for item in features:
            if not isinstance(item, dict):
                row_errors.append("feature item is not an object")
                continue
            h_code = str(item.get("h_code"))
            feature_id = str(item.get("feature_id"))
            quote = str(item.get("evidence_quote") or "")
            by_h[h_code] += 1
            if h_code not in code_set or feature_id not in H_TO_FEATURES.get(h_code, ()):
                row_errors.append(f"invalid pair {h_code}+{feature_id}")
            if quote.lower() not in expected["evidence"].lower():
                row_errors.append(f"non-verbatim evidence quote for {feature_id}")
        if any(count > 2 for count in by_h.values()):
            row_errors.append("more than two features for one H-code")
        if "Ex-3" in code_set and not any(code.startswith("H") for code in code_set) and feature_ids:
            row_errors.append("Ex-3 received an automatic feature")

        local_candidates = {
            item["feature_id"]
            for h_code in code_set
            if h_code in H_TO_FEATURES
            for item in select_features_for_evidence(h_code, expected["evidence"])
        }
        if not set(feature_ids) <= local_candidates:
            row_errors.append(f"feature lacks local evidence match: {sorted(set(feature_ids) - local_candidates)}")
        case_results.append(
            {
                "id": case_id,
                "ok": not row_errors,
                "errors": row_errors,
                "codes": sorted(code_set),
                "feature_ids": feature_ids,
            }
        )
    return {
        "ok": not errors and all(row["ok"] for row in case_results),
        "errors": errors,
        "cases": case_results,
        "passed_cases": sum(row["ok"] for row in case_results),
        "case_count": len(case_results),
    }


def signature(validation: dict[str, Any]) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        row["id"]: (tuple(row["codes"]), tuple(sorted(row["feature_ids"])))
        for row in validation.get("cases", [])
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stress test taxonomy-v2 reasoning with DeepSeek and Qwen.")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=2701)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.rounds <= 0:
        raise SystemExit("--rounds must be positive")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    signatures: dict[str, list[dict[str, tuple[tuple[str, ...], tuple[str, ...]]]]] = defaultdict(list)
    for round_no in range(1, args.rounds + 1):
        cases = [dict(case) for case in CASES]
        random.Random(args.seed + round_no).shuffle(cases)
        prompt = taxonomy_prompt(cases)
        for model in args.models:
            print(f"[api-stress] round={round_no}/{args.rounds} model={model} cases={len(cases)}", flush=True)
            response = call_model(model, prompt, timeout=args.timeout, retries=args.retries)
            validation = validate_response(response, cases)
            signatures[model].append(signature(validation))
            run = {
                "round": round_no,
                "model": model,
                "response": response,
                "validation": validation,
            }
            runs.append(run)
            model_dir = args.out_dir / f"round_{round_no:02d}" / model.replace("/", "_")
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "prompt.txt").write_text(
                redact_sensitive_text(prompt), encoding="utf-8"
            )
            (model_dir / "result.json").write_text(
                json.dumps(redact_sensitive_value(run), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    stability = {
        model: {
            "stable": all(item == model_signatures[0] for item in model_signatures[1:]),
            "round_count": len(model_signatures),
        }
        for model, model_signatures in signatures.items()
    }
    model_first = {model: model_signatures[0] for model, model_signatures in signatures.items() if model_signatures}
    cross_model_agreement = len(set(json.dumps(value, sort_keys=True) for value in model_first.values())) <= 1
    summary = {
        "generated_at": now(),
        "taxonomy_version": TAXONOMY_VERSION,
        "models": args.models,
        "rounds": args.rounds,
        "cases_per_run": len(CASES),
        "api_call_count": len(runs),
        "passed_api_calls": sum(run["response"].get("ok", False) for run in runs),
        "passed_validations": sum(run["validation"].get("ok", False) for run in runs),
        "stability": stability,
        "cross_model_agreement": cross_model_agreement,
        "failures": [
            {
                "round": run["round"],
                "model": run["model"],
                "api_error": run["response"].get("error"),
                "validation_errors": run["validation"].get("errors"),
                "failed_cases": [case for case in run["validation"].get("cases", []) if not case["ok"]],
            }
            for run in runs
            if not run["validation"].get("ok", False)
        ],
    }
    summary["ok"] = (
        summary["passed_api_calls"] == len(runs)
        and summary["passed_validations"] == len(runs)
        and all(item["stable"] for item in stability.values())
        and cross_model_agreement
    )
    (args.out_dir / "summary.json").write_text(
        json.dumps(redact_sensitive_value(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: summary[key] for key in ("api_call_count", "passed_api_calls", "passed_validations", "stability", "cross_model_agreement", "ok")}, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
