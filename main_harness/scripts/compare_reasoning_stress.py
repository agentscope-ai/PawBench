from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.bridge_attribution_to_harness_core import bridge_row  # noqa: E402
from scripts.feature_taxonomy import H_TO_FEATURES, TAXONOMY_VERSION  # noqa: E402
from scripts.paths import HARNESS_WORK_ROOT, REASONING_WORK_ROOT  # noqa: E402


DEFAULT_LEFT = REASONING_WORK_ROOT / "taxonomy_v2_real12_deepseek/runs.jsonl"
DEFAULT_RIGHT = REASONING_WORK_ROOT / "taxonomy_v2_real12_qwen/runs.jsonl"
DEFAULT_MANIFEST = ROOT / "candidates/agentscope/feature_manifest.json"
DEFAULT_OUT = HARNESS_WORK_ROOT / "reasoning_v2_real12_cross_judge"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def row_key(row: dict[str, Any]) -> str:
    return str(row.get("trajectory_path") or row.get("path") or row.get("task_id"))


def structural_errors(row: dict[str, Any], bridged: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    codes = row.get("codes") or []
    if "H6" in codes:
        errors.append("legacy H6 emitted")
    for mapping in bridged.get("h_to_features", []):
        selected = mapping.get("switchable_features", [])
        if len(selected) > 2:
            errors.append(f"{mapping['h_code']} selected more than two features")
        for item in selected:
            if item["feature_id"] not in H_TO_FEATURES.get(mapping["h_code"], ()):
                errors.append(f"invalid pair {mapping['h_code']}+{item['feature_id']}")
            if not item.get("evidence_matches"):
                errors.append(f"{item['feature_id']} has no evidence match")
    if "Ex-3" in codes and not bridged.get("h_codes") and bridged.get("recommended_feature_ids"):
        errors.append("Ex-3 automatically mapped to a feature")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two taxonomy-v2 trajectory judge runs.")
    parser.add_argument("--left", type=Path, default=DEFAULT_LEFT)
    parser.add_argument("--right", type=Path, default=DEFAULT_RIGHT)
    parser.add_argument("--left-label", default="deepseek-v4-pro")
    parser.add_argument("--right-label", default="qwen3.7-max")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    left = {row_key(row): row for row in load_jsonl(args.left)}
    right = {row_key(row): row for row in load_jsonl(args.right)}
    if set(left) != set(right):
        raise SystemExit("Judge runs do not contain the same trajectory keys")

    rows: list[dict[str, Any]] = []
    for key in sorted(left):
        left_row = left[key]
        right_row = right[key]
        left_bridge = bridge_row(left_row, manifest)
        right_bridge = bridge_row(right_row, manifest)
        left_codes = sorted(left_row.get("codes") or [])
        right_codes = sorted(right_row.get("codes") or [])
        left_features = left_bridge["recommended_feature_ids"]
        right_features = right_bridge["recommended_feature_ids"]
        left_errors = structural_errors(left_row, left_bridge)
        right_errors = structural_errors(right_row, right_bridge)
        rows.append(
            {
                "trajectory_path": key,
                "task_id": left_row.get("task_id"),
                "harness": left_row.get("harness"),
                "model": left_row.get("model"),
                args.left_label: {
                    "codes": left_codes,
                    "features": left_features,
                    "evidence": left_row.get("evidence") or [],
                    "structural_errors": left_errors,
                },
                args.right_label: {
                    "codes": right_codes,
                    "features": right_features,
                    "evidence": right_row.get("evidence") or [],
                    "structural_errors": right_errors,
                },
                "code_agreement": left_codes == right_codes,
                "feature_agreement": left_features == right_features,
            }
        )

    summary = {
        "taxonomy_version": TAXONOMY_VERSION,
        "row_count": len(rows),
        "left_label": args.left_label,
        "right_label": args.right_label,
        "exact_code_agreement": sum(row["code_agreement"] for row in rows),
        "exact_feature_agreement": sum(row["feature_agreement"] for row in rows),
        "structurally_valid_rows": sum(
            not row[args.left_label]["structural_errors"] and not row[args.right_label]["structural_errors"]
            for row in rows
        ),
        "disagreement_count": sum(not row["code_agreement"] for row in rows),
        "rows": rows,
    }
    summary["ok"] = summary["structurally_valid_rows"] == summary["row_count"]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Taxonomy V2 Cross-Judge Stress",
        "",
        f"- Taxonomy: `{TAXONOMY_VERSION}`",
        f"- Rows: {summary['row_count']}",
        f"- Exact code agreement: {summary['exact_code_agreement']}/{summary['row_count']}",
        f"- Exact feature agreement: {summary['exact_feature_agreement']}/{summary['row_count']}",
        f"- Structurally valid: {summary['structurally_valid_rows']}/{summary['row_count']}",
        "",
        "| Task | Harness | DeepSeek codes/features | Qwen codes/features |",
        "|---|---|---|---|",
    ]
    for row in rows:
        left_view = f"{','.join(row[args.left_label]['codes']) or '-'} / {','.join(row[args.left_label]['features']) or '-'}"
        right_view = f"{','.join(row[args.right_label]['codes']) or '-'} / {','.join(row[args.right_label]['features']) or '-'}"
        lines.append(f"| `{row['task_id']}` | `{row['harness']}` | `{left_view}` | `{right_view}` |")
    (args.out_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("row_count", "exact_code_agreement", "exact_feature_agreement", "structurally_valid_rows", "disagreement_count", "ok")}, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
