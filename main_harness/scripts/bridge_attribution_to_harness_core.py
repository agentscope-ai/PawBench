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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.feature_taxonomy import (
    FEATURE_NAMES,
    H_CODE_MEANING,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
    select_features_for_evidence,
)

from scripts.paths import ATTRIBUTION_ROOT, HARNESS_WORK_ROOT, RUN_RECORDS_ROOT
from scripts.security import (
    redact_sensitive_text,
    redact_sensitive_value,
    resolve_openai_compatible_provider,
    safe_provider_error,
)

DEFAULT_INPUT = (
    RUN_RECORDS_ROOT
    / "PawBenchV1-deepseek-v4-pro-20260709"
    / "runs"
    / "runs.jsonl"
)
DEFAULT_RUN_GROUP = "pawbench-4models-opusjudge-20260529"
DEFAULT_MODEL = "deepseek-v4-pro"

H_CODES = tuple(H_TO_FEATURES)
ATTRIBUTION_H_TO_FEATURES = H_TO_FEATURES


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = redact_sensitive_value(payload)
    path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(redact_sensitive_value(row), ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid jsonl row: {exc}") from exc
    return rows


def feature_switch_key(feature_id: str) -> str:
    return feature_id.replace(".", "_")


def compact_text(value: Any, *, limit: int = 260) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = redact_sensitive_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def row_key(row: dict[str, Any]) -> str:
    fields = [
        row.get("run_group"),
        row.get("harness"),
        row.get("model"),
        row.get("task_id"),
        row.get("path"),
    ]
    return redact_sensitive_text("::".join(str(item or "") for item in fields))


def extract_h_codes(row: dict[str, Any]) -> list[str]:
    codes = row.get("codes") or []
    if not isinstance(codes, list):
        return []
    return [code for code in H_CODES if code in codes]


def load_manifests(candidate: str, *, include_legacy: bool = False) -> dict[str, dict[str, Any]]:
    paths = sorted((ROOT / "candidates").glob("*/feature_manifest.json"))
    manifests: dict[str, dict[str, Any]] = {}
    for path in paths:
        if candidate != "all" and candidate not in {path.parent.name, path.stem}:
            continue
        manifest = load_json(path)
        if not include_legacy and manifest.get("taxonomy_version") != TAXONOMY_VERSION:
            continue
        manifest["_manifest_path"] = str(path)
        manifest["_candidate_dir"] = path.parent.name
        manifests[path.parent.name] = manifest
    if not manifests:
        raise ValueError(f"No feature manifests found for candidate={candidate!r}")
    return manifests


def switchable_features(manifest: dict[str, Any]) -> set[str]:
    features = manifest.get("features")
    if not isinstance(features, dict):
        return set()
    return set(features)


def manifest_h_features(manifest: dict[str, Any], h_code: str) -> set[str]:
    declared = manifest.get("h_code_mapping", {}).get(h_code, [])
    return {item for item in declared if isinstance(item, str)}


def feature_name(manifest: dict[str, Any], feature_id: str) -> str:
    item = manifest.get("features", {}).get(feature_id, {})
    return item.get("name") or FEATURE_NAMES.get(feature_id) or feature_id


def map_h_code_to_switches(
    h_code: str,
    manifest: dict[str, Any],
    *,
    evidence: Any = None,
) -> dict[str, Any]:
    evidence = redact_sensitive_value(evidence)
    switchable = switchable_features(manifest)
    attribution_features = list(ATTRIBUTION_H_TO_FEATURES.get(h_code, ()))
    manifest_features = manifest_h_features(manifest, h_code)
    recommendation_basis = (manifest_features or switchable) & switchable
    selections = select_features_for_evidence(h_code, evidence, max_features=2)
    selected = [
        item for item in selections if item["feature_id"] in recommendation_basis
    ]
    return {
        "h_code": h_code,
        "h_meaning": H_CODE_MEANING.get(h_code, h_code),
        "candidate_features": [
            feature_id for feature_id in attribution_features if feature_id in recommendation_basis
        ],
        "switchable_features": [
            {
                "feature_id": item["feature_id"],
                "switch_key": feature_switch_key(item["feature_id"]),
                "name": feature_name(manifest, item["feature_id"]),
                "source": "evidence+taxonomy+manifest",
                "evidence_matches": item["evidence_matches"],
                "evidence_score": item["score"],
            }
            for item in selected
        ],
        "unsupported_attribution_features": [
            feature_id for feature_id in attribution_features if feature_id not in switchable
        ],
        "switchable_but_not_candidate_mapped": [feature_id for feature_id in attribution_features if feature_id in switchable and feature_id not in recommendation_basis],
        "manifest_only_features": sorted((manifest_features & switchable) - set(attribution_features)),
        "selection_rule": "zero_or_one_normally; at_most_two_with_distinct_evidence",
    }


def bridge_row(row: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    manifest_version = manifest.get("taxonomy_version")
    if manifest_version not in (None, TAXONOMY_VERSION):
        raise ValueError(
            f"Manifest {manifest.get('candidate')} uses {manifest_version}; expected {TAXONOMY_VERSION}"
        )
    h_codes = extract_h_codes(row)
    mappings = [
        map_h_code_to_switches(h_code, manifest, evidence=row.get("evidence"))
        for h_code in h_codes
    ]
    feature_ids: list[str] = []
    for mapping in mappings:
        for feature in mapping["switchable_features"]:
            feature_ids.append(feature["feature_id"])
    feature_ids = list(dict.fromkeys(feature_ids))
    features_payload = row.get("features") if isinstance(row.get("features"), dict) else {}
    payload = {
        "row_key": row_key(row),
        "candidate": manifest.get("candidate") or manifest.get("_candidate_dir"),
        "candidate_dir": manifest.get("_candidate_dir"),
        "run_group": row.get("run_group"),
        "harness": row.get("harness"),
        "model": row.get("model"),
        "task_id": row.get("task_id"),
        "score": row.get("score"),
        "status": row.get("status"),
        "all_codes": row.get("codes", []),
        "external_codes": [code for code in row.get("codes", []) if code == "Ex-3"],
        "h_codes": h_codes,
        "recommended_feature_ids": feature_ids,
        "recommended_switch_keys": [feature_switch_key(feature_id) for feature_id in feature_ids],
        "h_to_features": mappings,
        "evidence": compact_text(row.get("evidence"), limit=360),
        "feature_hits": features_payload.get("hits", {}),
        "anomaly": features_payload.get("anomaly"),
        "judge": row.get("judge"),
        "path": row.get("path"),
    }
    return redact_sensitive_value(payload)


def aggregate_bridged(rows: list[dict[str, Any]]) -> dict[str, Any]:
    h_counts: Counter[str] = Counter()
    feature_counts: Counter[str] = Counter()
    pair_counts: Counter[str] = Counter()
    unsupported_counts: Counter[str] = Counter()
    examples_by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    examples_by_h: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        for h_code in row["h_codes"]:
            h_counts[h_code] += 1
            if len(examples_by_h[h_code]) < 5:
                examples_by_h[h_code].append(example_row(row))
        for feature_id in row["recommended_feature_ids"]:
            feature_counts[feature_id] += 1
            if len(examples_by_feature[feature_id]) < 5:
                examples_by_feature[feature_id].append(example_row(row))
        for mapping in row["h_to_features"]:
            h_code = mapping["h_code"]
            for feature in mapping["switchable_features"]:
                pair_counts[f"{h_code}+{feature['feature_id']}"] += 1
            for feature_id in mapping["unsupported_attribution_features"]:
                unsupported_counts[f"{h_code}+{feature_id}"] += 1
            for feature_id in mapping.get("switchable_but_not_candidate_mapped", []):
                unsupported_counts[f"{h_code}+{feature_id}:not_candidate_mapped"] += 1

    candidate = rows[0]["candidate"] if rows else None
    recommendations = []
    for feature_id, count in feature_counts.most_common():
        supporting_h = {
            pair_key.split("+", 1)[0]: pair_count
            for pair_key, pair_count in pair_counts.items()
            if pair_key.endswith("+" + feature_id)
        }
        recommendations.append(
            {
                "feature_id": feature_id,
                "switch_key": feature_switch_key(feature_id),
                "count": count,
                "supporting_h_codes": dict(sorted(supporting_h.items())),
                "example_rows": examples_by_feature.get(feature_id, []),
            }
        )

    return {
        "candidate": candidate,
        "row_count": len(rows),
        "h_code_counts": dict(h_counts.most_common()),
        "feature_counts": dict(feature_counts.most_common()),
        "h_feature_pair_counts": dict(pair_counts.most_common()),
        "unsupported_feature_counts": dict(unsupported_counts.most_common()),
        "examples_by_h_code": {key: value for key, value in sorted(examples_by_h.items())},
        "recommendations": recommendations,
        "next_ablation_profile": {
            "candidate": candidate,
            "enable_feature_ids": [item["feature_id"] for item in recommendations],
            "switches": {item["switch_key"]: True for item in recommendations},
        },
    }


def example_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_key": row["row_key"],
        "harness": row.get("harness"),
        "model": row.get("model"),
        "task_id": row.get("task_id"),
        "score": row.get("score"),
        "h_codes": row.get("h_codes"),
        "evidence": compact_text(row.get("evidence"), limit=180),
    }


def filter_rows(rows: list[dict[str, Any]], run_group: str, require_h_code: bool) -> list[dict[str, Any]]:
    filtered = []
    for row in rows:
        if run_group and row.get("run_group") != run_group:
            continue
        if require_h_code and not extract_h_codes(row):
            continue
        filtered.append(row)
    return filtered


def sample_rounds(
    rows: list[dict[str, Any]],
    *,
    rounds: int,
    sample_size: int,
    seed: int,
) -> list[list[dict[str, Any]]]:
    rng = random.Random(seed)
    keyed_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        keyed_rows.setdefault(row_key(row), row)
    pool = list(keyed_rows.values())
    if not pool:
        raise ValueError("No rows available for sampling")
    rng.shuffle(pool)

    samples: list[list[dict[str, Any]]] = []
    cursor = 0
    for _ in range(rounds):
        if len(pool) >= sample_size:
            if cursor + sample_size > len(pool):
                rng.shuffle(pool)
                cursor = 0
            sample = pool[cursor : cursor + sample_size]
            cursor += sample_size
        else:
            sample = [rng.choice(pool) for _ in range(sample_size)]
        samples.append(sample)
    return samples


def build_llm_prompt(round_payload: dict[str, Any]) -> str:
    round_payload = redact_sensitive_value(round_payload)
    compact_examples = []
    for row in round_payload["bridge_rows"][:16]:
        compact_examples.append(
            {
                "harness": row.get("harness"),
                "model": row.get("model"),
                "task_id": row.get("task_id"),
                "h_codes": row.get("h_codes"),
                "recommended_feature_ids": row.get("recommended_feature_ids"),
                "evidence": compact_text(row.get("evidence"), limit=180),
            }
        )

    audit_view = {
        "round": round_payload["round"],
        "sample_size": round_payload["sample_size"],
        "candidate_summaries": {
            candidate: {
                "h_code_counts": summary["h_code_counts"],
                "feature_counts": summary["feature_counts"],
                "unsupported_feature_counts": summary["unsupported_feature_counts"],
                "next_ablation_profile": summary["next_ablation_profile"],
            }
            for candidate, summary in round_payload["candidate_summaries"].items()
        },
        "examples": compact_examples,
    }
    prompt = (
        "You audit a PawBench harness-attribution bridge. The bridge maps H1-H5 "
        "harness error codes into switchable Harness-core features for PydanticAI "
        "and AgentScope. Return JSON only.\n\n"
        "Judge whether the proposed H-code to feature-switch attribution is too broad, "
        "missing a concrete switch, candidate-specific, or unsupported by evidence. "
        "Focus on actionable implementation issues that can be fixed in Harness-core.\n\n"
        "Return this schema:\n"
        "{\n"
        '  "round_assessment": "pass|warn|fail",\n'
        '  "issues": [\n'
        '    {"severity":"low|medium|high","type":"mapping|switch|evidence|candidate_gap|data_quality",'
        '"h_code":"H1-H5 or null","feature_id":"Fx.y or null","reason":"short",'
        '"suggested_fix":"short"}\n'
        "  ],\n"
        '  "keep_rules": ["short rule that should remain unchanged"]\n'
        "}\n\n"
        f"Audit data:\n{json.dumps(audit_view, ensure_ascii=False, indent=2)}"
    )
    return redact_sensitive_text(prompt)


def shell_env() -> dict[str, str]:
    env = dict(os.environ)
    if any(env.get(key) for key in ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY")):
        return env
    try:
        proc = subprocess.run(
            ["zsh", "-ic", "env"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=12,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return env
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and value and key not in env:
            env[key] = value
    return env


def api_settings() -> tuple[str, str]:
    settings = resolve_openai_compatible_provider(shell_env())
    return settings.api_key, settings.base_url


def extract_json_object(text: str) -> dict[str, Any]:
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


def call_qwen_audit(
    prompt: str,
    *,
    model: str,
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    prompt = redact_sensitive_text(prompt)
    api_key, base_url = api_settings()
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base_payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict benchmark harness engineer. Return JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 1600,
        "response_format": {"type": "json_object"},
    }
    last_error: str | None = None
    for attempt in range(1, retries + 2):
        for provider_extras in ({"enable_thinking": False}, {}):
            payload = dict(base_payload)
            payload.update(provider_extras)
            started = time.time()
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                elapsed = time.time() - started
                if response.status_code >= 400:
                    last_error = safe_provider_error(
                        status_code=response.status_code,
                        headers=response.headers,
                    )
                    continue
                data = response.json()
                raw_content = data["choices"][0]["message"]["content"]
                content = redact_sensitive_text(raw_content)
                try:
                    parsed = extract_json_object(content)
                except json.JSONDecodeError:
                    parsed = redact_sensitive_value(extract_json_object(raw_content))
                parsed["_api"] = {
                    "model": model,
                    "base_url": base_url,
                    "elapsed_seconds": round(elapsed, 3),
                    "usage": data.get("usage"),
                    "attempt": attempt,
                    "provider_extras": provider_extras,
                }
                return redact_sensitive_value(parsed)
            except Exception as exc:  # noqa: BLE001 - retry provider failures
                last_error = safe_provider_error(exc=exc)
        if attempt <= retries:
            time.sleep(min(8, 2**attempt))
    raise RuntimeError(last_error or "LLM audit failed")


def run_round(
    *,
    round_no: int,
    sample: list[dict[str, Any]],
    manifests: dict[str, dict[str, Any]],
    out_dir: Path,
    audit_with_llm: bool,
    model: str,
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    round_dir = out_dir / f"round_{round_no:02d}"
    sample_rows = [dict(row, _sample_row_key=row_key(row)) for row in sample]
    write_jsonl(round_dir / "sample.jsonl", sample_rows)

    bridge_rows_by_candidate: dict[str, list[dict[str, Any]]] = {}
    candidate_summaries: dict[str, dict[str, Any]] = {}
    for candidate_dir, manifest in manifests.items():
        bridged = [bridge_row(row, manifest) for row in sample]
        bridge_rows_by_candidate[candidate_dir] = bridged
        write_jsonl(round_dir / f"{candidate_dir}_bridge_rows.jsonl", bridged)
        candidate_summaries[candidate_dir] = aggregate_bridged(bridged)
        write_json(round_dir / f"{candidate_dir}_summary.json", candidate_summaries[candidate_dir])

    # The bridge rows are identical across candidates for source evidence; use the
    # first candidate to build compact audit examples.
    first_candidate = next(iter(bridge_rows_by_candidate))
    payload = {
        "round": round_no,
        "sample_size": len(sample),
        "candidate_summaries": candidate_summaries,
        "bridge_rows": bridge_rows_by_candidate[first_candidate],
    }
    write_json(round_dir / "round_payload.json", payload)

    audit: dict[str, Any] | None = None
    audit_error: str | None = None
    if audit_with_llm:
        try:
            prompt = redact_sensitive_text(build_llm_prompt(payload))
            (round_dir / "qwen_audit_prompt.txt").write_text(prompt, encoding="utf-8")
            audit = call_qwen_audit(prompt, model=model, timeout=timeout, retries=retries)
            write_json(round_dir / "qwen_audit.json", audit)
        except Exception as exc:  # noqa: BLE001 - keep loop running and report failed round
            audit_error = redact_sensitive_text(str(exc))
            write_json(round_dir / "qwen_audit_error.json", {"error": audit_error, "model": model})

    return {
        "round": round_no,
        "sample_size": len(sample),
        "sample_unique_keys": len({row_key(row) for row in sample}),
        "candidate_summaries": {
            candidate: {
                "h_code_counts": summary["h_code_counts"],
                "feature_counts": summary["feature_counts"],
                "unsupported_feature_counts": summary["unsupported_feature_counts"],
                "next_ablation_profile": summary["next_ablation_profile"],
            }
            for candidate, summary in candidate_summaries.items()
        },
        "audit_assessment": audit.get("round_assessment") if audit else None,
        "audit_issue_count": len(audit.get("issues", [])) if audit else None,
        "audit_error": audit_error,
        "qwen_audit_assessment": audit.get("round_assessment") if audit else None,
        "qwen_issue_count": len(audit.get("issues", [])) if audit else None,
        "qwen_audit_error": audit_error,
    }


def write_markdown_report(out_dir: Path, loop_summary: dict[str, Any]) -> None:
    lines = [
        "# Attribution to Harness-core Bridge Run",
        "",
        f"- Generated: `{loop_summary['generated_at']}`",
        f"- Input: `{loop_summary['input']}`",
        f"- Run group: `{loop_summary['run_group']}`",
        f"- Sample: `{loop_summary['rounds']} x {loop_summary['sample_size']}`",
        f"- LLM audit: `{loop_summary['judge_model'] if loop_summary['audit_with_llm'] else 'disabled'}`",
        "",
        "## Aggregate",
        "",
        "| Candidate | H-code counts | Feature counts | Audit issues | Audit errors |",
        "|---|---:|---:|---:|---:|",
    ]
    aggregate = loop_summary["aggregate"]
    for candidate, item in aggregate["candidates"].items():
        h_total = sum(item["h_code_counts"].values())
        feature_total = sum(item["feature_counts"].values())
        lines.append(
            f"| `{candidate}` | {h_total} | {feature_total} | "
            f"{aggregate['audit_issue_count']} | {aggregate['audit_error_count']} |"
        )
    lines.extend(["", "## Top Feature Counts", ""])
    for candidate, item in aggregate["candidates"].items():
        lines.append(f"### {candidate}")
        lines.append("")
        lines.append("| Feature | Count |")
        lines.append("|---|---:|")
        for feature_id, count in item["feature_counts"].items():
            lines.append(f"| `{feature_id}` / `{feature_switch_key(feature_id)}` | {count} |")
        lines.append("")
    lines.extend(["## Round Audit Outcomes", "", "| Round | Assessment | Issues | Error |", "|---:|---|---:|---|"])
    for item in loop_summary["rounds_summary"]:
        lines.append(
            f"| {item['round']} | `{item.get('audit_assessment')}` | "
            f"{item.get('audit_issue_count')} | {compact_text(item.get('audit_error'), limit=140)} |"
        )
    report = redact_sensitive_text("\n".join(lines) + "\n")
    (out_dir / "REPORT.md").write_text(report, encoding="utf-8")


def build_loop_aggregate(rounds_summary: list[dict[str, Any]], manifests: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidate_h_counts: dict[str, Counter[str]] = {candidate: Counter() for candidate in manifests}
    candidate_feature_counts: dict[str, Counter[str]] = {candidate: Counter() for candidate in manifests}
    candidate_unsupported_counts: dict[str, Counter[str]] = {candidate: Counter() for candidate in manifests}
    issue_count = 0
    error_count = 0

    for summary in rounds_summary:
        if summary.get("audit_error") or summary.get("qwen_audit_error"):
            error_count += 1
        issue_count += int(summary.get("audit_issue_count") or summary.get("qwen_issue_count") or 0)
        for candidate, item in summary["candidate_summaries"].items():
            candidate_h_counts[candidate].update(item["h_code_counts"])
            candidate_feature_counts[candidate].update(item["feature_counts"])
            candidate_unsupported_counts[candidate].update(item["unsupported_feature_counts"])

    return {
        "audit_issue_count": issue_count,
        "audit_error_count": error_count,
        "qwen_issue_count": issue_count,
        "qwen_audit_error_count": error_count,
        "candidates": {
            candidate: {
                "h_code_counts": dict(candidate_h_counts[candidate].most_common()),
                "feature_counts": dict(candidate_feature_counts[candidate].most_common()),
                "unsupported_feature_counts": dict(candidate_unsupported_counts[candidate].most_common()),
                "next_ablation_profile": {
                    "candidate": manifest.get("candidate") or candidate,
                    "enable_feature_ids": list(candidate_feature_counts[candidate].keys()),
                    "switches": {
                        feature_switch_key(feature_id): True
                        for feature_id in candidate_feature_counts[candidate]
                    },
                },
            }
            for candidate, manifest in manifests.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bridge PawBench H-code attribution rows to Harness-core switchable features."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--run-group", default=DEFAULT_RUN_GROUP)
    parser.add_argument("--candidate", default="all")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=2701)
    parser.add_argument("--out-dir", type=Path, default=HARNESS_WORK_ROOT / "attribution_bridge_deepseek_v4_pro_20x100")
    parser.add_argument("--audit-with-llm", action="store_true")
    parser.add_argument("--judge-model", default=DEFAULT_MODEL)
    parser.add_argument("--api-timeout", type=int, default=120)
    parser.add_argument("--api-retries", type=int, default=1)
    parser.add_argument("--allow-non-h-rows", action="store_true")
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Include manifests frozen on legacy_p0_20260709. They cannot be bridged to v2 without explicit migration.",
    )
    args = parser.parse_args()

    if args.rounds <= 0 or args.sample_size <= 0:
        raise SystemExit("--rounds and --sample-size must be positive")
    if args.include_legacy:
        raise SystemExit(
            "Legacy manifests are frozen for inspection only. Migrate their rows explicitly before using the v2 bridge."
        )

    manifests = load_manifests(args.candidate, include_legacy=args.include_legacy)
    all_rows = load_jsonl(args.input)
    filtered = filter_rows(all_rows, args.run_group, require_h_code=not args.allow_non_h_rows)
    if len(filtered) < args.sample_size:
        raise SystemExit(
            f"Only {len(filtered)} rows available after filtering, fewer than sample size {args.sample_size}"
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "generated_at": now(),
        "root": str(ROOT),
        "attribution_root": str(ATTRIBUTION_ROOT),
        "input": str(args.input),
        "run_group": args.run_group,
        "candidate": args.candidate,
        "candidate_dirs": sorted(manifests),
        "taxonomy_version": TAXONOMY_VERSION,
        "include_legacy": args.include_legacy,
        "rounds": args.rounds,
        "sample_size": args.sample_size,
        "seed": args.seed,
        "audit_with_llm": args.audit_with_llm,
        "judge_model": args.judge_model,
        "source_row_count": len(all_rows),
        "filtered_row_count": len(filtered),
        "h_to_feature_source": str(ATTRIBUTION_ROOT / "reference/hx_feature_two_layer_mapping.md"),
        "switchable_manifest_paths": [manifest["_manifest_path"] for manifest in manifests.values()],
    }
    write_json(args.out_dir / "bridge_config.json", config)

    samples = sample_rounds(filtered, rounds=args.rounds, sample_size=args.sample_size, seed=args.seed)
    rounds_summary = []
    for index, sample in enumerate(samples, start=1):
        print(f"[bridge] round {index}/{args.rounds}: sample={len(sample)} audit={args.audit_with_llm}")
        rounds_summary.append(
            run_round(
                round_no=index,
                sample=sample,
                manifests=manifests,
                out_dir=args.out_dir,
                audit_with_llm=args.audit_with_llm,
                model=args.judge_model,
                timeout=args.api_timeout,
                retries=args.api_retries,
            )
        )

    loop_summary = {
        **config,
        "finished_at": now(),
        "rounds_summary": rounds_summary,
        "aggregate": build_loop_aggregate(rounds_summary, manifests),
    }
    write_json(args.out_dir / "loop_summary.json", loop_summary)
    write_markdown_report(args.out_dir, loop_summary)
    print(json.dumps(loop_summary["aggregate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
