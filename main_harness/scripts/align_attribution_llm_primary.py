from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.bridge_attribution_to_harness_core import (  # noqa: E402
    aggregate_bridged,
    bridge_row,
    compact_text,
    load_json,
    load_jsonl,
    write_json,
    write_jsonl,
)
from scripts.feature_taxonomy import H_TO_FEATURES, feature_label  # noqa: E402
from scripts.paths import (  # noqa: E402
    ATTRIBUTION_ROOT,
    HARNESS_WORK_ROOT,
    LEGACY_ARTIFACTS_ROOT,
    OUTPUT_REPORTS,
    RUN_RECORDS_ROOT,
)


LEGACY_ATTRIBUTION_ANALYSIS = LEGACY_ARTIFACTS_ROOT / "attribution_analysis" / "analysis"
DEFAULT_HEURISTIC = LEGACY_ATTRIBUTION_ANALYSIS / "trajectory_analysis/runs.jsonl"
DEFAULT_LLM = (
    RUN_RECORDS_ROOT
    / "PawBenchV1-deepseek-v4-pro-20260709"
    / "runs/runs.jsonl"
)
AGENTSCOPE_MANIFEST = ROOT / "candidates/agentscope/feature_manifest.json"
DEFAULT_OUT = HARNESS_WORK_ROOT / "attribution_alignment_llm_primary"
REPORT_PATH = OUTPUT_REPORTS / "llm_primary_attribution_alignment.md"
H_CODES = tuple(H_TO_FEATURES)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_key(row: dict[str, Any]) -> str:
    return "::".join(str(row.get(field) or "") for field in ("run_group", "model", "harness", "task_id"))


def h_codes(row: dict[str, Any]) -> list[str]:
    codes = row.get("codes") or []
    return [code for code in H_CODES if code in codes]


def code_family_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        for code in row.get("codes") or []:
            if code.startswith("H"):
                counts["H"] += 1
            elif code.startswith("M"):
                counts["M"] += 1
            elif code.startswith("Ex"):
                counts["Ex"] += 1
            else:
                counts["other"] += 1
    return dict(counts)


def qwenpaw_manifest_from_agentscope() -> dict[str, Any]:
    manifest = load_json(AGENTSCOPE_MANIFEST)
    manifest["candidate"] = "QwenPaw (AgentScope-as-QwenPaw)"
    manifest["_candidate_dir"] = "agentscope_as_qwenpaw"
    manifest["_manifest_path"] = str(AGENTSCOPE_MANIFEST)
    return manifest


def align_rows(heuristic_rows: list[dict[str, Any]], llm_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    heuristic_by_key: dict[str, dict[str, Any]] = {}
    for row in heuristic_rows:
        heuristic_by_key.setdefault(run_key(row), row)

    aligned: list[dict[str, Any]] = []
    exact = 0
    overlap = 0
    missing_heuristic = 0
    confusion: Counter[str] = Counter()
    llm_only_counter: Counter[str] = Counter()
    heuristic_only_counter: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []

    for llm_row in llm_rows:
        key = run_key(llm_row)
        heuristic = heuristic_by_key.get(key)
        llm_h = set(h_codes(llm_row))
        heuristic_h = set(h_codes(heuristic or {}))
        if heuristic is None:
            missing_heuristic += 1
        if llm_h == heuristic_h:
            exact += 1
        if llm_h & heuristic_h:
            overlap += 1

        left = sorted(heuristic_h) or ["NO_H"]
        right = sorted(llm_h) or ["NO_H"]
        for h_code in left:
            for llm_code in right:
                confusion[f"{h_code}->{llm_code}"] += 1
        for code in sorted(llm_h - heuristic_h):
            llm_only_counter[code] += 1
        for code in sorted(heuristic_h - llm_h):
            heuristic_only_counter[code] += 1

        if heuristic_h != llm_h and len(examples) < 20:
            examples.append(
                {
                    "run_key": key,
                    "model": llm_row.get("model"),
                    "harness": llm_row.get("harness"),
                    "task_id": llm_row.get("task_id"),
                    "heuristic_h_codes": sorted(heuristic_h),
                    "llm_h_codes": sorted(llm_h),
                    "llm_evidence": compact_text(llm_row.get("evidence"), limit=300),
                    "heuristic_evidence": compact_text((heuristic or {}).get("evidence"), limit=300),
                }
            )

        aligned.append(
            {
                **llm_row,
                "alignment": {
                    "mode": "llm_primary",
                    "run_key": key,
                    "primary_source": "deepseek-v4-pro trajectory judge",
                    "heuristic_source": "trajectory_analysis heuristic",
                    "heuristic_codes": (heuristic or {}).get("codes", []),
                    "heuristic_h_codes": sorted(heuristic_h),
                    "llm_h_codes": sorted(llm_h),
                    "exact_h_match": llm_h == heuristic_h,
                    "h_overlap": bool(llm_h & heuristic_h),
                    "decision": "use_llm_codes_for_feature_evolution",
                },
            }
        )

    summary = {
        "row_count": len(llm_rows),
        "heuristic_pool_count": len(heuristic_rows),
        "missing_heuristic_matches": missing_heuristic,
        "exact_h_agreement": exact,
        "exact_h_agreement_rate": round(exact / len(llm_rows), 4) if llm_rows else None,
        "h_overlap": overlap,
        "h_overlap_rate": round(overlap / len(llm_rows), 4) if llm_rows else None,
        "llm_code_counts": dict(Counter(code for row in llm_rows for code in row.get("codes", [])).most_common()),
        "heuristic_code_counts_on_matched_sample": dict(
            Counter(
                code
                for row in llm_rows
                for code in (heuristic_by_key.get(run_key(row), {}).get("codes", []))
            ).most_common()
        ),
        "llm_code_family_counts": code_family_counts(llm_rows),
        "llm_only_h_codes": dict(llm_only_counter.most_common()),
        "heuristic_only_h_codes": dict(heuristic_only_counter.most_common()),
        "h_confusion_pairs": dict(confusion.most_common()),
        "disagreement_examples": examples,
    }
    return aligned, summary


def render_report(alignment_summary: dict[str, Any], feature_summary: dict[str, Any], out_dir: Path) -> str:
    top_features = feature_summary.get("feature_counts", {})
    lines = [
        "# LLM-Primary Attribution Alignment",
        "",
        "Conclusion: Harness-core uses DeepSeek-v4-Pro trajectory judge as the default primary attribution source, with Qwen.37-Max retained as a secondary high-recall disagreement auditor.",
        "",
        "## Alignment Summary",
        "",
        f"- LLM-primary rows: {alignment_summary['row_count']}",
        f"- Heuristic pool rows: {alignment_summary['heuristic_pool_count']}",
        f"- Exact H-code agreement: {alignment_summary['exact_h_agreement']} ({alignment_summary['exact_h_agreement_rate']})",
        f"- H-code overlap: {alignment_summary['h_overlap']} ({alignment_summary['h_overlap_rate']})",
        f"- Missing heuristic matches: {alignment_summary['missing_heuristic_matches']}",
        "",
        "## LLM Code Counts",
        "",
        "| Code | Count |",
        "| --- | ---: |",
    ]
    for code, count in alignment_summary["llm_code_counts"].items():
        lines.append(f"| `{code}` | {count} |")

    lines += [
        "",
        "## Feature Attribution For QwenPaw-as-AgentScope",
        "",
        "| Feature | Count |",
        "| --- | ---: |",
    ]
    for feature_id, count in top_features.items():
        lines.append(f"| `{feature_label(feature_id, zh=True)}` | {count} |")

    lines += [
        "",
        "## Top H Confusion Pairs",
        "",
        "| Heuristic -> LLM | Count |",
        "| --- | ---: |",
    ]
    for pair, count in list(alignment_summary["h_confusion_pairs"].items())[:12]:
        lines.append(f"| `{pair}` | {count} |")

    lines += [
        "",
        "## Example Disagreements",
        "",
        "| Task | Heuristic H | LLM H | LLM evidence |",
        "| --- | --- | --- | --- |",
    ]
    for item in alignment_summary["disagreement_examples"][:10]:
        lines.append(
            "| `{task}` | `{heuristic}` | `{llm}` | {evidence} |".format(
                task=item["task_id"],
                heuristic=",".join(item["heuristic_h_codes"]) or "NO_H",
                llm=",".join(item["llm_h_codes"]) or "NO_H",
                evidence=compact_text(item["llm_evidence"], limit=180).replace("|", "\\|"),
            )
        )

    lines += [
        "",
        "## Artifacts",
        "",
        f"- `{out_dir / 'llm_primary_runs.jsonl'}`",
        f"- `{out_dir / 'alignment_summary.json'}`",
        f"- `{out_dir / 'qwenpaw_as_agentscope_feature_summary.json'}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Align heuristic attribution with LLM-primary attribution.")
    parser.add_argument("--heuristic-runs", type=Path, default=DEFAULT_HEURISTIC)
    parser.add_argument("--llm-runs", type=Path, default=DEFAULT_LLM)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    heuristic_rows = load_jsonl(args.heuristic_runs)
    llm_rows = load_jsonl(args.llm_runs)
    aligned, alignment_summary = align_rows(heuristic_rows, llm_rows)

    manifest = qwenpaw_manifest_from_agentscope()
    bridged = [bridge_row(row, manifest) for row in aligned]
    feature_summary = aggregate_bridged(bridged)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "llm_primary_runs.jsonl", aligned)
    write_jsonl(args.out_dir / "qwenpaw_as_agentscope_feature_rows.jsonl", bridged)
    write_json(args.out_dir / "alignment_summary.json", {"generated_at": now(), **alignment_summary})
    write_json(args.out_dir / "qwenpaw_as_agentscope_feature_summary.json", feature_summary)
    report = render_report(alignment_summary, feature_summary, args.out_dir)
    (args.out_dir / "REPORT.md").write_text(report, encoding="utf-8")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    print(json.dumps({"ok": True, "out_dir": str(args.out_dir), "row_count": len(aligned)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
