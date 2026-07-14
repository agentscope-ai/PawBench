from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.bridge_attribution_to_harness_core import (
    aggregate_bridged,
    bridge_row,
    call_qwen_audit,
    feature_switch_key,
    write_json,
    write_jsonl,
)
from scripts.feature_taxonomy import FEATURE_IDS, H_TO_FEATURES, TAXONOMY_VERSION
from scripts.paths import ATTRIBUTION_ROOT, HARNESS_WORK_ROOT, RUN_RECORDS_ROOT


RUN_GROUP = "pawbench-4models-opusjudge-20260529"
ATTRIBUTION_RUNS = (
    RUN_RECORDS_ROOT
    / "PawBenchV1-deepseek-v4-pro-20260709"
    / "runs"
    / "runs.jsonl"
)
RUN_ROOT = ATTRIBUTION_ROOT / "result/pawbench-4models-opusjudge-20260529"
AGENTSCOPE_MANIFEST = ROOT / "candidates/agentscope/feature_manifest.json"
DEFAULT_OUT = HARNESS_WORK_ROOT / "pawbench1_10pct_deepseek_primary_pipeline"
DEFAULT_MODEL = "deepseek-v4-pro"

H_CODES = tuple(H_TO_FEATURES)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def row_key(row: dict[str, Any]) -> str:
    return "::".join(
        str(row.get(field) or "")
        for field in ("run_group", "model", "harness", "task_id")
    )


def sample_fraction_stratified(
    rows: list[dict[str, Any]],
    *,
    fraction: float,
    seed: int,
    strata_fields: tuple[str, ...] = ("harness", "model"),
) -> list[dict[str, Any]]:
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    if not rows:
        return []
    target = max(1, round(len(rows) * fraction))
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row.get(field) or "") for field in strata_fields)].append(row)

    rng = random.Random(seed)
    quota_items = []
    assigned = 0
    for key, items in groups.items():
        raw = len(items) * fraction
        quota = int(raw)
        assigned += quota
        quota_items.append((raw - quota, key, quota))
    quota_items.sort(reverse=True)
    remainder = target - assigned
    quotas = {key: quota for _, key, quota in quota_items}
    for _, key, _ in quota_items[:remainder]:
        quotas[key] += 1

    sampled: list[dict[str, Any]] = []
    for key, items in groups.items():
        group_items = list(items)
        rng.shuffle(group_items)
        sampled.extend(group_items[: quotas[key]])
    sampled.sort(key=row_key)
    return sampled


def metrics_path_for(row: dict[str, Any]) -> Path:
    run_group = row.get("run_group")
    if run_group == RUN_GROUP:
        return RUN_ROOT / row["model"] / row["harness"] / row["task_id"] / "output" / "metrics.json"
    return RUN_ROOT / run_group / row["harness"] / row["model"] / row["task_id"] / "output" / "metrics.json"


def score_matrix_rows(sampled: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in sampled:
        path = metrics_path_for(row)
        metrics: dict[str, Any] = {}
        if path.exists():
            metrics = load_json(path)
        anomaly = metrics.get("anomaly") or {}
        rows.append(
            {
                "run_key": row_key(row),
                "run_group": row.get("run_group"),
                "model": row.get("model"),
                "harness": row.get("harness"),
                "task_id": row.get("task_id"),
                "score": metrics.get("task_score", row.get("score")),
                "passed": metrics.get("passed"),
                "status": metrics.get("status", row.get("status")),
                "exit_code": metrics.get("exit_code"),
                "grading_type": metrics.get("grading_type"),
                "wall_time_s": metrics.get("wall_time_s"),
                "anomaly_items": anomaly.get("items", row.get("anomaly") or []) if isinstance(anomaly, dict) else row.get("anomaly") or [],
                "metrics_path": str(path),
                "metrics_found": path.exists(),
            }
        )
    return rows


def score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [row["score"] for row in rows if isinstance(row.get("score"), (int, float))]
    passed = [row["passed"] for row in rows if row.get("passed") is not None]

    def group_stats(field: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get(field))].append(row)
        out = {}
        for key, items in sorted(grouped.items()):
            item_scores = [row["score"] for row in items if isinstance(row.get("score"), (int, float))]
            out[key] = {
                "runs": len(items),
                "avg_score": round(mean(item_scores), 4) if item_scores else None,
                "passed": sum(row.get("passed") is True for row in items),
                "status_counts": dict(Counter(str(row.get("status")) for row in items)),
            }
        return out

    return {
        "row_count": len(rows),
        "metrics_found": sum(row["metrics_found"] for row in rows),
        "avg_score": round(mean(scores), 4) if scores else None,
        "min_score": min(scores) if scores else None,
        "max_score": max(scores) if scores else None,
        "pass_rate": round(sum(value is True for value in passed) / len(passed), 4) if passed else None,
        "by_harness": group_stats("harness"),
        "by_model": group_stats("model"),
    }


def score_matrix_by_task(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matrix: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        matrix[row["task_id"]][row["model"]][row["harness"]] = {
            "score": row.get("score"),
            "passed": row.get("passed"),
            "status": row.get("status"),
        }
    return {task: {model: dict(harnesses) for model, harnesses in models.items()} for task, models in matrix.items()}


def attribution_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    code_counts = Counter(code for row in rows for code in row.get("codes", []))
    h_code_counts = Counter(code for row in rows for code in row.get("codes", []) if code in H_CODES)
    by_harness: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        codes = [code for code in row.get("codes", []) if code in H_CODES]
        if not codes:
            by_harness[row["harness"]]["NO_H_CODE"] += 1
        for code in codes:
            by_harness[row["harness"]][code] += 1
    return {
        "row_count": len(rows),
        "runs_with_any_code": sum(bool(row.get("codes")) for row in rows),
        "runs_with_h_code": sum(any(code in H_CODES for code in row.get("codes", [])) for row in rows),
        "code_counts": dict(code_counts.most_common()),
        "h_code_counts": dict(h_code_counts.most_common()),
        "by_harness_h_codes": {key: dict(value.most_common()) for key, value in sorted(by_harness.items())},
    }


def qwenpaw_manifest_from_agentscope() -> dict[str, Any]:
    manifest = load_json(AGENTSCOPE_MANIFEST)
    manifest["candidate"] = "QwenPaw (AgentScope-as-QwenPaw)"
    manifest["_candidate_dir"] = "agentscope_as_qwenpaw"
    manifest["_manifest_path"] = str(AGENTSCOPE_MANIFEST)
    return manifest


def feature_toggle_plan(feature_summary: dict[str, Any], *, min_support: int = 5) -> dict[str, Any]:
    recommendations = feature_summary.get("recommendations", [])
    observed = [item["feature_id"] for item in recommendations]
    strong = [item["feature_id"] for item in recommendations if item.get("count", 0) >= min_support]
    weak = [item["feature_id"] for item in recommendations if 0 < item.get("count", 0) < min_support]
    unobserved = [feature_id for feature_id in FEATURE_IDS if feature_id not in observed]
    cases = [
        {
            "case": "all_features",
            "enabled_feature_ids": list(FEATURE_IDS),
            "disabled_feature_id": None,
            "purpose": "baseline with all 15 taxonomy-v2 features enabled",
        },
        {
            "case": "strong_recommended_from_10pct_attribution",
            "enabled_feature_ids": strong,
            "disabled_feature_id": None,
            "purpose": f"features with at least {min_support} supporting rows in the 10% PawBench v1 sample",
        },
    ]
    for feature_id in FEATURE_IDS:
        cases.append(
            {
                "case": f"without_{feature_switch_key(feature_id)}",
                "enabled_feature_ids": [item for item in FEATURE_IDS if item != feature_id],
                "disabled_feature_id": feature_id,
                "purpose": f"ablate {feature_id} and verify its trace/event is absent",
            }
        )
    return {
        "feature_universe": list(FEATURE_IDS),
        "taxonomy_version": TAXONOMY_VERSION,
        "min_support": min_support,
        "observed_feature_ids": observed,
        "strong_recommended_feature_ids": strong,
        "low_support_feature_ids": weak,
        "unobserved_feature_ids": unobserved,
        "recommended_feature_ids": strong,
        "recommended_switches": {feature_switch_key(feature_id): True for feature_id in strong},
        "exclusion_rationale": {
            **{
                feature_id: f"low support below min_support={min_support}; keep as ablation-only until more evidence"
                for feature_id in weak
            },
            **{
                feature_id: "not observed in this 10% H-code attribution sample"
                for feature_id in unobserved
            },
        },
        "cases": cases,
    }


def build_qwen_prompt(payload: dict[str, Any]) -> str:
    audit_view = {
        "goal": "Run PawBench v1 10% pipeline: score matrix -> attribution -> QwenPaw feature attribution -> feature on/off plan.",
        "score_summary": payload["score_summary"],
        "attribution_summary": payload["attribution_summary"],
        "qwenpaw_feature_summary": {
            "h_code_counts": payload["feature_summary"]["h_code_counts"],
            "feature_counts": payload["feature_summary"]["feature_counts"],
            "h_feature_pair_counts": payload["feature_summary"]["h_feature_pair_counts"],
            "broad_mapping_gap_counts": payload["feature_summary"]["unsupported_feature_counts"],
        },
        "toggle_plan": {
            "feature_universe": payload["toggle_plan"]["feature_universe"],
            "recommended_feature_ids": payload["toggle_plan"]["recommended_feature_ids"],
            "low_support_feature_ids": payload["toggle_plan"]["low_support_feature_ids"],
            "unobserved_feature_ids": payload["toggle_plan"]["unobserved_feature_ids"],
            "exclusion_rationale": payload["toggle_plan"]["exclusion_rationale"],
            "pipeline_ablation_profile": {
                "enable_feature_ids": payload["toggle_plan"]["recommended_feature_ids"],
                "switches": payload["toggle_plan"]["recommended_switches"],
            },
            "case_count": len(payload["toggle_plan"]["cases"]),
        },
    }
    return (
        "You audit a PawBench harness self-evolution pipeline. Treat AgentScope as QwenPaw for this pilot. "
        "The pipeline uses 10 current P0 features only. Return JSON only.\n\n"
        "Check whether the score matrix, attribution summary, QwenPaw feature mapping, and on/off feature plan "
        "are coherent enough for a first 10% PawBench v1 run. Identify blockers before scaling.\n\n"
        "Important: broad_mapping_gap_counts records features from the wider research map that are not in the current "
        "10-feature candidate mapping. It does not mean the H-code has no supported P0 feature when h_feature_pair_counts "
        "contains supported pairs.\n\n"
        "Return schema:\n"
        "{\n"
        '  "round_assessment": "pass|warn|fail",\n'
        '  "issues": [\n'
        '    {"severity":"low|medium|high","type":"score_matrix|attribution|feature_mapping|toggle_plan|data_quality",'
        '"h_code":"H1-H5 or null","feature_id":"Fx.y or null","reason":"short","suggested_fix":"short"}\n'
        "  ],\n"
        '  "recommended_next_run": {"run_full_10pct_with_llm_judge": true, "feature_cases_to_prioritize": ["Fx.y"]},\n'
        '  "keep_rules": ["short rule"]\n'
        "}\n\n"
        f"Audit data:\n{json.dumps(audit_view, ensure_ascii=False, indent=2)}"
    )


def write_report(out_dir: Path, payload: dict[str, Any], qwen_audit: dict[str, Any] | None) -> None:
    score = payload["score_summary"]
    attribution = payload["attribution_summary"]
    feature = payload["feature_summary"]
    lines = [
        "# PawBench v1 10% QwenPaw Pipeline Pilot",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Run group: `{payload['run_group']}`",
        f"- Sample fraction: `{payload['sample_fraction']}`",
        f"- Sample rows: `{payload['sample_count']}` / `{payload['source_count']}`",
        f"- Audit model: `{payload['judge_model']}`",
        "",
        "## Score Matrix",
        "",
        f"- Rows: {score['row_count']}",
        f"- Metrics found: {score['metrics_found']}",
        f"- Avg score: {score['avg_score']}",
        f"- Pass rate: {score['pass_rate']}",
        "",
        "## Attribution",
        "",
        f"- Runs with any code: {attribution['runs_with_any_code']}",
        f"- Runs with H-code: {attribution['runs_with_h_code']}",
        "",
        "| H-code | Count |",
        "|---|---:|",
    ]
    for code, count in attribution["h_code_counts"].items():
        lines.append(f"| `{code}` | {count} |")
    lines.extend(["", "## QwenPaw as AgentScope Feature Attribution", "", "| Feature | Count |", "|---|---:|"])
    for feature_id, count in feature["feature_counts"].items():
        lines.append(f"| `{feature_id}` / `{feature_switch_key(feature_id)}` | {count} |")
    lines.extend(["", "## Feature Toggle Plan", ""])
    lines.append(f"- Feature universe: {', '.join(f'`{item}`' for item in payload['toggle_plan']['feature_universe'])}")
    lines.append(f"- Strong recommended from attribution: {', '.join(f'`{item}`' for item in payload['toggle_plan']['recommended_feature_ids']) or 'none'}")
    lines.append(f"- Low-support features: {', '.join(f'`{item}`' for item in payload['toggle_plan']['low_support_feature_ids']) or 'none'}")
    lines.append(f"- Unobserved features: {', '.join(f'`{item}`' for item in payload['toggle_plan']['unobserved_feature_ids']) or 'none'}")
    lines.append(f"- Cases: {len(payload['toggle_plan']['cases'])}")
    if qwen_audit:
        lines.extend(["", f"## {payload['judge_model']} Audit", ""])
        lines.append(f"- Assessment: `{qwen_audit.get('round_assessment')}`")
        lines.append(f"- Issues: {len(qwen_audit.get('issues', []))}")
        lines.extend(["", "| Severity | Type | H-code | Feature | Reason |", "|---|---|---|---|---|"])
        for issue in qwen_audit.get("issues", [])[:12]:
            lines.append(
                f"| `{issue.get('severity')}` | `{issue.get('type')}` | `{issue.get('h_code')}` | "
                f"`{issue.get('feature_id')}` | {issue.get('reason')} |"
            )
    (out_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a 10% PawBench v1 score->attribution->feature pipeline pilot.")
    parser.add_argument("--fraction", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=3707)
    parser.add_argument("--attribution-runs", type=Path, default=ATTRIBUTION_RUNS)
    parser.add_argument("--run-group", default=RUN_GROUP)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--judge-model", default=DEFAULT_MODEL)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--api-timeout", type=int, default=120)
    parser.add_argument("--api-retries", type=int, default=1)
    args = parser.parse_args()

    rows = [
        row for row in load_jsonl(args.attribution_runs)
        if row.get("run_group") == args.run_group
    ]
    sampled = sample_fraction_stratified(rows, fraction=args.fraction, seed=args.seed)
    score_rows = score_matrix_rows(sampled)
    score_stats = score_summary(score_rows)
    attribution_stats = attribution_summary(sampled)
    manifest = qwenpaw_manifest_from_agentscope()
    bridged_rows = [bridge_row(row, manifest) for row in sampled]
    feature_stats = aggregate_bridged(bridged_rows)
    toggle = feature_toggle_plan(feature_stats)
    feature_stats["raw_observed_profile"] = feature_stats.pop("next_ablation_profile")
    feature_stats["pipeline_ablation_profile"] = {
        "candidate": manifest["candidate"],
        "enable_feature_ids": toggle["recommended_feature_ids"],
        "switches": toggle["recommended_switches"],
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "sampled_attribution_rows.jsonl", sampled)
    (args.out_dir / "sampled_trajectory_paths.txt").write_text(
        "\n".join(str((ATTRIBUTION_ROOT / row["trajectory_path"]).resolve()) for row in sampled) + "\n",
        encoding="utf-8",
    )
    write_jsonl(args.out_dir / "score_matrix_long.jsonl", score_rows)
    write_json(args.out_dir / "score_matrix_by_task.json", score_matrix_by_task(score_rows))
    write_json(args.out_dir / "score_matrix_summary.json", score_stats)
    write_json(args.out_dir / "attribution_summary.json", attribution_stats)
    write_jsonl(args.out_dir / "qwenpaw_as_agentscope_feature_rows.jsonl", bridged_rows)
    write_json(args.out_dir / "qwenpaw_as_agentscope_feature_summary.json", feature_stats)
    write_json(args.out_dir / "feature_toggle_plan.json", toggle)

    payload = {
        "generated_at": now(),
        "root": str(ROOT),
        "attribution_runs": str(args.attribution_runs),
        "run_group": args.run_group,
        "source_count": len(rows),
        "sample_fraction": args.fraction,
        "sample_count": len(sampled),
        "seed": args.seed,
        "judge_model": args.judge_model,
        "score_summary": score_stats,
        "attribution_summary": attribution_stats,
        "feature_summary": feature_stats,
        "toggle_plan": toggle,
        "outputs": {
            "score_matrix_long": str(args.out_dir / "score_matrix_long.jsonl"),
            "score_matrix_by_task": str(args.out_dir / "score_matrix_by_task.json"),
            "sampled_trajectory_paths": str(args.out_dir / "sampled_trajectory_paths.txt"),
            "feature_summary": str(args.out_dir / "qwenpaw_as_agentscope_feature_summary.json"),
            "toggle_plan": str(args.out_dir / "feature_toggle_plan.json"),
        },
    }

    qwen_audit = None
    if not args.no_llm:
        prompt = build_qwen_prompt(payload)
        (args.out_dir / "llm_pipeline_audit_prompt.txt").write_text(prompt, encoding="utf-8")
        qwen_audit = call_qwen_audit(
            prompt,
            model=args.judge_model,
            timeout=args.api_timeout,
            retries=args.api_retries,
        )
        write_json(args.out_dir / "llm_pipeline_audit.json", qwen_audit)
    payload["llm_audit"] = qwen_audit
    payload["qwen_audit"] = qwen_audit
    write_json(args.out_dir / "pipeline_summary.json", payload)
    write_report(args.out_dir, payload, qwen_audit)
    print(json.dumps({
        "ok": True,
        "out_dir": str(args.out_dir),
        "source_count": len(rows),
        "sample_count": len(sampled),
        "score_rows": len(score_rows),
        "h_code_runs": attribution_stats["runs_with_h_code"],
        "recommended_features": toggle["recommended_feature_ids"],
        "audit_assessment": qwen_audit.get("round_assessment") if qwen_audit else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
