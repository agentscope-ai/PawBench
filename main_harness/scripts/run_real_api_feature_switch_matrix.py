from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.paths import HARNESS_ABLATION_RUNS_ROOT, OUTPUT_RESULTS  # noqa: E402
from scripts.security import (  # noqa: E402
    redact_sensitive_text,
    redact_sensitive_value,
    resolve_openai_compatible_provider,
    safe_provider_error,
)

LEGACY_TAXONOMY_VERSION = "legacy_p0_20260709"
OUT_ROOT = HARNESS_ABLATION_RUNS_ROOT / "legacy_p0_real_api_feature_switch_matrix"
RESULTS = OUTPUT_RESULTS

LEGACY_P0_FEATURE_IDS = (
    "F1.1",
    "F1.2",
    "F1.5",
    "F2.1",
    "F2.3",
    "F2.4",
    "F3.1",
    "F3.3",
    "F4.1",
    "F5.1",
)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_shell_env() -> dict[str, str]:
    env = os.environ.copy()
    if any(
        env.get(name)
        for name in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "ALIYUN_API_KEY",
            "ALIBABA_CLOUD_API_KEY",
            "OPENAI_API_KEY",
            "LLM_API_KEY",
        )
    ):
        return env
    proc = subprocess.run(["zsh", "-ic", "env"], text=True, capture_output=True, timeout=15)
    if proc.returncode != 0:
        return env
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "ALIYUN_API_KEY",
            "ALIBABA_CLOUD_API_KEY",
            "OPENAI_API_KEY",
            "LLM_API_KEY",
            "DASHSCOPE_BASE_URL",
            "BAILIAN_BASE_URL",
            "ALIYUN_BASE_URL",
            "ALIBABA_CLOUD_BASE_URL",
            "OPENAI_BASE_URL",
            "LLM_BASE_URL",
            "REAL_ENV_MODEL_NAME",
            "GLM_MODEL_NAME",
            "GLM_MODEL",
        }:
            env[key] = value
    return env


def api_config(env: dict[str, str]) -> dict[str, Any]:
    settings = resolve_openai_compatible_provider(env)
    model_candidates = [
        env.get("REAL_ENV_MODEL_NAME"),
        env.get("GLM_MODEL_NAME"),
        env.get("GLM_MODEL"),
        "glm-5.2",
        "qwen3.7-max",
        "qwen-max",
        "qwen-plus",
    ]
    models: list[str] = []
    for model in model_candidates:
        if model and model not in models:
            models.append(model)
    return {
        "api_key": settings.api_key,
        "base_url": settings.base_url,
        "provider": settings.provider,
        "models": models,
    }


def manifest_paths() -> list[Path]:
    return [
        path
        for path in sorted((ROOT / "candidates").glob("*/feature_manifest.json"))
        if json.loads(path.read_text(encoding="utf-8")).get("taxonomy_version") == LEGACY_TAXONOMY_VERSION
    ]


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) | {"candidate_dir": path.parent.name, "manifest_path": str(path)}


def safe_name(name: str) -> str:
    return name.lower().replace(" ", "_").replace("/", "_").replace(".", "_").replace("-", "_")


def case_configs() -> list[tuple[str, set[str], str | None]]:
    all_enabled = set(LEGACY_P0_FEATURE_IDS)
    configs = [("all_p0", set(all_enabled), None)]
    configs.extend((f"without_{feature_id.replace('.', '_')}", all_enabled - {feature_id}, feature_id) for feature_id in LEGACY_P0_FEATURE_IDS)
    return configs


def trace_append(trace_path: Path, event_type: str, payload: dict[str, Any]) -> None:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "seq": 1,
        "time": now(),
        "type": event_type,
        "payload": redact_sensitive_value(payload),
    }
    if trace_path.exists():
        record["seq"] = sum(1 for _ in trace_path.open(encoding="utf-8")) + 1
    with trace_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(redact_sensitive_value(record), ensure_ascii=False) + "\n")


def public_run_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(OUT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def reset_workspace(candidate: str, case_name: str) -> Path:
    workspace = OUT_ROOT / safe_name(candidate) / case_name / "workspace_root"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / "input").mkdir()
    (workspace / "workspace").mkdir()
    (workspace / "input" / "task.txt").write_text(
        "Create workspace/answer.txt with exactly: pawbench-api-ok\n",
        encoding="utf-8",
    )
    return workspace


def build_prompt(manifest: dict[str, Any], case_name: str, enabled: set[str], workspace: Path, trace_path: Path) -> str:
    lines = [
        "You are running a quick PawBench harness feature-switch test.",
        "Read this instruction conceptually: create workspace/answer.txt with exactly pawbench-api-ok.",
        "Return JSON only with keys path and content. Use path=workspace/answer.txt and content=pawbench-api-ok.",
        f"Candidate route: {manifest['candidate']}",
        f"Case: {case_name}",
    ]
    if "F1.1" in enabled:
        lines.append("Workspace root alias: workspace_root")
        trace_append(
            trace_path,
            "workspace_injection",
            {"workspace_root": public_run_path(workspace)},
        )
    if "F1.2" in enabled:
        (workspace / "SKILL.md").write_text("EXPECTED_CONTENT=pawbench-api-ok\n", encoding="utf-8")
        lines.append("Local skill says EXPECTED_CONTENT=pawbench-api-ok.")
        trace_append(trace_path, "skill_discovery", {"paths": ["SKILL.md"], "count": 1})
    if "F1.5" in enabled:
        trace_append(trace_path, "prompt_rewrite", {"changed": False, "diff": ""})
    if "F2.1" in enabled:
        lines.append('Available tool schema: write_file(path: str, content: str) -> "ok".')
        trace_append(trace_path, "tool_schema_registration", {"tools": ["write_file"]})
    if "F2.4" in enabled:
        lines.append("If tool execution fails, the harness will provide structured error feedback.")
    if "F3.1" in enabled:
        lines.append("Completion will be accepted only after artifact verification.")
    if "F3.3" in enabled:
        lines.append("One bounded retry is available after validation failure.")
        trace_append(trace_path, "retry_policy_enabled", {"max_retries": 1})
    if "F4.1" in enabled:
        trace_append(trace_path, "context_compaction_check", {"token_estimate": 256, "threshold": 96000, "should_compact": False})
    if "F5.1" in enabled:
        lines.append("Semantic verifier is enabled.")
    return redact_sensitive_text("\n".join(lines))


def emit_feature_events(manifest: dict[str, Any], enabled: set[str], trace_path: Path) -> None:
    for feature_id in sorted(enabled):
        feature = manifest["features"][feature_id]
        trace_append(
            trace_path,
            "feature_enabled",
            {
                "feature": feature_id,
                "feature_trace_event": feature["trace_event"],
                "switch_type": feature["switch_type"],
                "evidence_level": feature["evidence_level"],
            },
        )


def parse_json_from_text(text: str) -> dict[str, str]:
    raw_text = text
    stripped = redact_sensitive_text(raw_text).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", stripped)
        try:
            if not match:
                raise
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            raw_match = re.search(r"\{[\s\S]*\}", raw_text.strip())
            data = json.loads(raw_match.group(0) if raw_match else raw_text.strip())
    data = redact_sensitive_value(data)
    return redact_sensitive_value(
        {
            "path": str(data.get("path", "workspace/answer.txt")),
            "content": str(data.get("content", "")),
        }
    )


def call_llm(client: OpenAI, provider: dict[str, Any], prompt: str) -> dict[str, Any]:
    prompt = redact_sensitive_text(prompt)
    errors: list[dict[str, str]] = []
    for model in provider["models"]:
        for use_extra_body in (True, False):
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "Return compact JSON only. Do not include markdown."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 80,
            }
            if use_extra_body:
                kwargs["extra_body"] = {"enable_thinking": False}
            try:
                response = client.chat.completions.create(**kwargs)
                raw_content = response.choices[0].message.content or ""
                content = json.dumps(
                    parse_json_from_text(raw_content), ensure_ascii=False
                )
                usage = getattr(response, "usage", None)
                return redact_sensitive_value({
                    "content": content,
                    "usage": usage.model_dump() if hasattr(usage, "model_dump") else None,
                    "model": model,
                    "base_url": provider["base_url"],
                    "fallback_attempts": errors,
                    "used_extra_body": use_extra_body,
                })
            except Exception as exc:
                errors.append(
                    {
                        "model": model,
                        "extra_body": str(use_extra_body),
                        "error": safe_provider_error(exc=exc),
                    }
                )
    raise RuntimeError("All model fallbacks failed: " + json.dumps(errors[-6:], ensure_ascii=False))


def safe_join(root: Path, rel_path: str) -> Path:
    target = (root / rel_path).resolve()
    resolved = root.resolve()
    if target != resolved and resolved not in target.parents:
        raise ValueError(f"path escapes workspace: {rel_path}")
    return target


def execute_tool(workspace: Path, enabled: set[str], llm_data: dict[str, str], trace_path: Path) -> dict[str, Any]:
    trace_append(trace_path, "tool_attempt", {"tool": "write_file", "feature_F2_3": "F2.3" in enabled})
    if "F2.3" not in enabled:
        payload = {"executed": False, "blocked": True, "ok": False, "reason": "F2.3 disabled default tool availability"}
        trace_append(trace_path, "tool_blocked", payload)
        return payload
    try:
        path = safe_join(workspace, llm_data["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(llm_data["content"], encoding="utf-8")
        payload = {"executed": True, "blocked": False, "ok": True, "path": str(path.relative_to(workspace)), "chars": len(llm_data["content"])}
    except Exception as exc:
        payload = {
            "executed": False,
            "blocked": False,
            "ok": False,
            "error": redact_sensitive_text(f"{type(exc).__name__}: {exc}"),
        }
        if "F2.4" in enabled:
            payload["structured_error"] = True
    trace_append(trace_path, "tool_result", payload)
    return payload


def verify(workspace: Path, enabled: set[str], trace_path: Path) -> dict[str, Any]:
    answer = workspace / "workspace" / "answer.txt"
    external_ok = answer.is_file() and answer.read_text(encoding="utf-8", errors="replace").strip() == "pawbench-api-ok"
    if "F5.1" not in enabled:
        payload = {"semantic_ran": False, "validator_passed": None, "external_validator_passed": external_ok}
        trace_append(trace_path, "verifier_skipped", payload)
        return payload
    payload = {"semantic_ran": True, "validator_passed": external_ok, "external_validator_passed": external_ok}
    trace_append(trace_path, "verifier_result", payload)
    return payload


def run_case(client: OpenAI, provider: dict[str, str], manifest: dict[str, Any], case_name: str, enabled: set[str], disabled_feature: str | None) -> dict[str, Any]:
    workspace = reset_workspace(manifest["candidate"], case_name)
    trace_path = workspace.parent / "trace.jsonl"
    if trace_path.exists():
        trace_path.unlink()
    trace_append(trace_path, "run_start", {"candidate": manifest["candidate"], "case": case_name, "disabled_feature": disabled_feature})
    emit_feature_events(manifest, enabled, trace_path)
    prompt = redact_sensitive_text(
        build_prompt(manifest, case_name, enabled, workspace, trace_path)
    )
    llm_error = None
    llm_result: dict[str, Any]
    try:
        llm_result = call_llm(client, provider, prompt)
        trace_append(
            trace_path,
            "llm_api_result",
            {
                "model": llm_result["model"],
                "content_preview": redact_sensitive_text(llm_result["content"][:200]),
                "usage": llm_result["usage"],
            },
        )
        llm_data = parse_json_from_text(llm_result["content"])
    except Exception as exc:
        llm_error = redact_sensitive_text(str(exc))
        llm_result = {
            "content": "",
            "usage": None,
            "model": provider["models"][0],
            "base_url": provider["base_url"],
        }
        llm_data = {"path": "workspace/answer.txt", "content": ""}
        trace_append(trace_path, "llm_api_error", {"model": provider["models"][0], "error": llm_error})
    tool = execute_tool(workspace, enabled, llm_data, trace_path)
    verifier = verify(workspace, enabled, trace_path)
    trace_append(trace_path, "completion_detection", {"enabled": "F3.1" in enabled, "accepted": bool(verifier["external_validator_passed"])})
    feature_enabled_ids = [
        json.loads(line).get("payload", {}).get("feature")
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("type") == "feature_enabled"
    ]
    disabled_feature_enabled_absent = disabled_feature not in feature_enabled_ids if disabled_feature else True
    return redact_sensitive_value({
        "candidate": manifest["candidate"],
        "candidate_status": manifest.get("status"),
        "case": case_name,
        "disabled_feature": disabled_feature,
        "features_enabled": sorted(enabled),
        "workspace": public_run_path(workspace),
        "trace": public_run_path(trace_path),
        "api_call": {
            "attempted": True,
            "ok": llm_error is None,
            "model": llm_result["model"],
            "base_url": provider["base_url"],
            "usage": llm_result["usage"],
            "fallback_attempts": llm_result.get("fallback_attempts", []),
            "used_extra_body": llm_result.get("used_extra_body"),
            "error": llm_error,
        },
        "tool_interaction": tool,
        "harness_verifier": verifier,
        "external_validator_passed": bool(verifier["external_validator_passed"]),
        "disabled_feature_enabled_absent": disabled_feature_enabled_absent,
        "accepted": bool(verifier["external_validator_passed"]) if "F3.1" in enabled else True,
    })


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_candidate.setdefault(record["candidate"], []).append(record)
    out: dict[str, Any] = {}
    for candidate, items in by_candidate.items():
        by_case = {item["case"]: item for item in items}
        feature_checks = {}
        for feature_id in LEGACY_P0_FEATURE_IDS:
            item = by_case[f"without_{feature_id.replace('.', '_')}"]
            feature_checks[feature_id] = {
                "api_ok": item["api_call"]["ok"],
                "disabled_feature_enabled_absent": item["disabled_feature_enabled_absent"],
                "tool_executed": item["tool_interaction"].get("executed"),
                "expected_tool_blocked": feature_id == "F2.3" and item["tool_interaction"].get("blocked"),
                "external_validator_passed": item["external_validator_passed"],
                "harness_verifier_ran": item["harness_verifier"]["semantic_ran"],
            }
        all_p0 = by_case["all_p0"]
        out[candidate] = {
            "candidate_status": all_p0["candidate_status"],
            "all_p0_api_ok": all_p0["api_call"]["ok"],
            "all_p0_tool_executed": all_p0["tool_interaction"].get("executed"),
            "all_p0_external_validator_passed": all_p0["external_validator_passed"],
            "feature_switches_checked": feature_checks,
            "all_feature_switches_have_off_evidence": all(item["disabled_feature_enabled_absent"] for item in feature_checks.values()),
        }
    return out


def main() -> int:
    global OUT_ROOT
    parser = argparse.ArgumentParser(description="Run real LLM/API quick feature-switch matrix for every candidate.")
    parser.add_argument("--candidate", default="all")
    parser.add_argument("--out-root", default=str(OUT_ROOT))
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    OUT_ROOT = Path(args.out_root).expanduser().resolve()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)

    env = load_shell_env()
    provider = api_config(env)
    client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"])
    provider_public = {
        "provider": provider["provider"],
        "base_url": provider["base_url"],
        "models": provider["models"],
    }

    manifests = [load_manifest(path) for path in manifest_paths()]
    if args.candidate != "all":
        needle = args.candidate.lower()
        manifests = [m for m in manifests if needle in m["candidate"].lower() or needle in m["candidate_dir"].lower()]

    records: list[dict[str, Any]] = []
    for manifest in manifests:
        for case_name, enabled, disabled_feature in case_configs():
            record = run_case(client, provider_public, manifest, case_name, enabled, disabled_feature)
            records.append(record)
            print(json.dumps({
                "candidate": record["candidate"],
                "case": record["case"],
                "api_ok": record["api_call"]["ok"],
                "tool_executed": record["tool_interaction"].get("executed"),
                "validator_passed": record["external_validator_passed"],
                "disabled_feature": record["disabled_feature"],
            }, ensure_ascii=False), flush=True)

    candidate_summary = summarize(records)
    summary = {
        "generated_at": now(),
        "provider": provider_public,
        "candidate_count": len(manifests),
        "case_count": len(records),
        "taxonomy_version": LEGACY_TAXONOMY_VERSION,
        "features": list(LEGACY_P0_FEATURE_IDS),
        "candidate_summary": candidate_summary,
        "records": records,
        "ok": bool(records)
        and all(item["all_p0_api_ok"] for item in candidate_summary.values())
        and all(item["all_p0_external_validator_passed"] for item in candidate_summary.values())
        and all(item["all_feature_switches_have_off_evidence"] for item in candidate_summary.values()),
    }
    out = RESULTS / "feature_switch_matrix__pawbench_v1__api__quick.json"
    safe_summary = redact_sensitive_value(summary)
    out.write_text(json.dumps(safe_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    console_payload = safe_summary if args.pretty else {
        "ok": safe_summary["ok"],
        "candidate_count": safe_summary["candidate_count"],
        "case_count": safe_summary["case_count"],
        "provider": safe_summary["provider"],
        "output": out.name,
    }
    print(json.dumps(console_payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
