#!/usr/bin/env python3
"""Summarize repeated closed-loop comparisons without scheduling new runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CANDIDATE_ROOT.parents[2]
for value in (PROJECT_ROOT / "main_harness", CANDIDATE_ROOT / "src"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from pawbench_agentscope.reliability import (  # noqa: E402
    RELIABILITY_SCHEMA_VERSION,
    aggregate_by_feature,
)
from pawbench_agentscope._atomic_io import (  # noqa: E402
    atomic_write_text,
    prepare_marked_output,
    read_text_no_follow,
)


MAX_RECORDING_BYTES = 32 * 1024 * 1024
MAX_RECORDING_FILES = 10_000
OUTPUT_MARKER = ".harness-core-ablation-reliability"


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _read_comparisons(root: Path) -> list[dict[str, Any]]:
    recordings = root / "recordings"
    if recordings.is_symlink() or not recordings.is_dir():
        raise ValueError(f"recordings directory is missing: {recordings}")
    comparisons: list[dict[str, Any]] = []
    paths = sorted(recordings.glob("*.json"))
    if len(paths) > MAX_RECORDING_FILES:
        raise ValueError(f"recordings directory exceeds {MAX_RECORDING_FILES} JSON files")
    for path in paths:
        payload = json.loads(
            read_text_no_follow(path, max_bytes=MAX_RECORDING_BYTES),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_nonfinite,
        )
        if not isinstance(payload, Mapping):
            raise ValueError(f"recording must be an object: {path}")
        values = payload.get("comparisons", [])
        if not isinstance(values, list):
            raise ValueError(f"comparisons must be an array: {path}")
        for index, item in enumerate(values):
            if not isinstance(item, Mapping):
                raise ValueError(f"comparison {index} must be an object: {path}")
            comparisons.append(dict(item))
    return comparisons


def _report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Repeated Feature-Ablation Evidence",
        "",
        "This report aggregates existing paired comparisons; it does not alter attribution or schedule runs.",
        "",
        "| Feature | Trials | Decision | Support (95% Wilson) | Mean score drop | False acceptance |",
        "| --- | ---: | --- | --- | ---: | ---: |",
    ]
    for row in rows:
        interval = row["supported_rate_interval_95"]
        support = (
            f"{row['supported_rate']:.3f} "
            f"[{interval[0]:.3f}, {interval[1]:.3f}]"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["feature_id"]),
                    str(row["trial_count"]),
                    str(row["status"]),
                    support,
                    f"{row['mean_score_drop']:.3f}",
                    str(row["false_acceptance_regressions"]),
                ]
            )
            + " |"
        )
    lines += [
        "",
        "A decision remains `inconclusive` when the configured evidence threshold is not met.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--closed-loop-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--minimum-trials", type=int, default=5)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="replace an existing output only when it carries this script's exact marker",
    )
    args = parser.parse_args(argv)
    if args.minimum_trials < 1:
        parser.error("--minimum-trials must be positive")
    root = args.closed_loop_root.expanduser().resolve()
    output = args.output.expanduser().absolute()
    if output.exists() and not args.fresh:
        raise SystemExit(f"refusing to replace existing output: {output}")
    comparisons = _read_comparisons(root)
    aggregates = aggregate_by_feature(
        comparisons,
        minimum_trials=args.minimum_trials,
    )
    rows = [item.model_dump(mode="json") for item in aggregates]
    payload = {
        "schema_version": RELIABILITY_SCHEMA_VERSION,
        "source_root": str(root),
        "comparison_count": len(comparisons),
        "feature_count": len(rows),
        "minimum_trials": args.minimum_trials,
        "status_counts": {
            status: sum(row["status"] == status for row in rows)
            for status in ("supported", "contradicted", "inconclusive", "insufficient_trials")
        },
        "features": rows,
    }
    try:
        output = prepare_marked_output(
            output,
            marker_name=OUTPUT_MARKER,
            marker_text=RELIABILITY_SCHEMA_VERSION + "\n",
            replace=args.fresh,
        ).resolve()
    except ValueError as exc:
        parser.error(str(exc))
    atomic_write_text(
        output / "summary.json",
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    atomic_write_text(output / "REPORT_EN.md", _report(rows))
    print(
        json.dumps(
            {
                "output": str(output),
                "comparison_count": payload["comparison_count"],
                "feature_count": payload["feature_count"],
                "status_counts": payload["status_counts"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
