#!/usr/bin/env python3
"""Aggregate raw evaluation runs under ``result/`` into submission JSONs.

For each ``result/<run>/<model>/<harness>/<task>/output/metrics.json`` we read
the per-task ``task_score`` and ``breakdown`` and join with the per-task
metadata in ``site/src/data/tasks.json`` (built by ``build_tasks.py``) to
produce a single rolled-up JSON per ``(run, model, harness)`` under
``submissions/``. ``build_leaderboard.py`` then consumes those JSONs.

This script is idempotent: re-running regenerates all ``<run>__*.json`` files.
Old submission files for retired runs can be removed manually; the aggregator
never deletes anything it didn't write.

Why an intermediate file?

- ``submissions/`` already exists in the project as the canonical hand-off
  format, so manually-curated rows can sit alongside auto-generated ones.
- Decoupling raw runs from the leaderboard lets us host multiple runs (e.g.
  ``pawbench-rerun-20260519`` + a future ``pawbench-rerun-20260612``) and let
  ``build_leaderboard.py`` pick the freshest ``(model, harness)``.

Usage::

    python3 site/scripts/aggregate_results.py
    python3 site/scripts/aggregate_results.py --run pawbench-rerun-20260519
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = REPO_ROOT / "result"
SUBMISSIONS_DIR = REPO_ROOT / "submissions"
TASKS_JSON = SITE_ROOT / "src" / "data" / "tasks.json"

# Map raw model directory name → display name.
# Edit here when a new run uses a vendor-prefixed or codename folder. We do
# not auto-strip ``vendor.`` prefixes because version dots like
# ``qwen3.6-27b`` would be misread.
MODEL_ALIAS: dict[str, str] = {
    "openai.claude-opus-4-6": "claude-opus-4.6",
    "deepseek.deepseek-v4-pro": "deepseek-v4-pro",
    # `q37max0515s12ep` looks like an internal codename — keep verbatim until
    # we know the official display name; users can edit this mapping.
    "q37max0515s12ep": "q37max0515s12ep",
}

# Vendor prefixes that are safe to strip ("openai.foo" → "foo"). Anything
# matching `<vendor>.<rest>` where vendor is in this set gets the prefix
# dropped if no explicit alias was provided.
SAFE_VENDOR_PREFIXES: set[str] = {"openai", "deepseek", "anthropic", "google", "meta"}

# Map raw harness directory name → canonical slug used in the leaderboard.
# Display names live in build_leaderboard.py; this map is just for storage.
HARNESS_ALIAS: dict[str, str] = {
    "copaw": "qwenpaw",
}

RUN_DATE_RE = re.compile(r"(\d{4})(\d{2})(\d{2})")


def display_model(raw: str) -> str:
    if raw in MODEL_ALIAS:
        return MODEL_ALIAS[raw]
    head, sep, tail = raw.partition(".")
    if sep and head in SAFE_VENDOR_PREFIXES and tail:
        return tail
    return raw


def canonical_harness(raw: str) -> str:
    return HARNESS_ALIAS.get(raw, raw)


def run_updated(run_name: str) -> str:
    m = RUN_DATE_RE.search(run_name)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo}-{d}"
    return date.today().isoformat()


def load_task_metadata() -> dict[str, dict[str, Any]]:
    if not TASKS_JSON.exists():
        print(
            f"[aggregate_results] {TASKS_JSON.relative_to(REPO_ROOT)} missing — "
            "run build_tasks.py first.",
            file=sys.stderr,
        )
        sys.exit(2)
    tasks = json.loads(TASKS_JSON.read_text(encoding="utf-8"))
    return {t["t_id"]: t for t in tasks}


def split_breakdown(d: dict[str, float]) -> tuple[float | None, float | None]:
    """Pull ``automated.*`` / ``llm_judge.*`` means out of a hybrid breakdown.

    For purely-automated or purely-judge breakdowns the keys are unprefixed and
    we leave the partition to the caller (see ``score_partition``).
    """
    auto_vals = [v for k, v in d.items() if k.startswith("automated.")]
    judge_vals = [v for k, v in d.items() if k.startswith("llm_judge.")]
    a = mean(auto_vals) if auto_vals else None
    j = mean(judge_vals) if judge_vals else None
    return a, j


def score_partition(
    grading_type: str, task_score: float, breakdown: dict[str, float]
) -> tuple[float | None, float | None]:
    """Return ``(automated_score, judge_score)`` for a single task.

    Tasks that don't contribute to a side return ``None`` for that side, which
    excludes them from the corresponding column mean.
    """
    if grading_type == "hybrid":
        return split_breakdown(breakdown)
    if grading_type == "automated":
        return task_score, None
    if grading_type == "llm_judge":
        return None, task_score
    # error / unknown
    return None, None


def safe_mean(xs: list[float]) -> float:
    return round(mean(xs), 4) if xs else 0.0


def safe_mean_or_none(xs: list[float]) -> float | None:
    return round(mean(xs), 4) if xs else None


def discover_runs(only: list[str] | None) -> list[Path]:
    if not RESULT_ROOT.is_dir():
        return []
    runs = sorted(p for p in RESULT_ROOT.iterdir() if p.is_dir() and not p.name.startswith("."))
    if only:
        wanted = set(only)
        runs = [r for r in runs if r.name in wanted]
    return runs


def _record_task(
    *,
    t_id: str | None,
    score: float,
    grading_type: str,
    breakdown: dict[str, float],
    status: str,
    task_meta: dict[str, dict[str, Any]],
    per_task_score: list[float],
    automated_scores: list[float],
    judge_scores: list[float],
    buckets: dict[str, dict[str, list[float]]],
) -> tuple[int, int]:
    """Apply a single task result to the running aggregates.

    Returns ``(errored, missing_meta)`` deltas (0 or 1 each) so the caller
    can keep cumulative counters.
    """
    errored = 0
    missing_meta = 0

    if grading_type in ("error", "missing") or status in ("error", "missing"):
        errored = 1
        score = 0.0  # ensure error/missing tasks count as 0 toward all means
    else:
        a, j = score_partition(grading_type, score, breakdown)
        if a is not None:
            automated_scores.append(a)
        if j is not None:
            judge_scores.append(j)

    per_task_score.append(score)

    meta = task_meta.get(t_id) if t_id else None
    if meta is None:
        return errored, missing_meta + 1

    labels = meta.get("labels") or {}
    if labels.get("complexity"):
        buckets["by_complexity"][labels["complexity"]].append(score)
    if labels.get("environment"):
        buckets["by_environment"][labels["environment"]].append(score)
    if labels.get("scenario"):
        sc = labels["scenario"]
        buckets["by_scenario"][sc].append(score)
        buckets["by_scenario_top"][sc.split("/", 1)[0]].append(score)
    modality = labels.get("modality") or {}
    if modality.get("type"):
        buckets["by_modality"][modality["type"]].append(score)
    for ch in modality.get("channels") or []:
        buckets["by_channel"][ch].append(score)
    for cap in labels.get("capabilities") or []:
        buckets["by_capability"][cap].append(score)
    if meta.get("source_dataset"):
        buckets["by_source"][meta["source_dataset"]].append(score)
    if meta.get("category"):
        buckets["by_category"][meta["category"]].append(score)
    if meta.get("subcategory"):
        buckets["by_subcategory"][meta["subcategory"]].append(score)
    # use the *task's* original grading_type (from md), not the run-time
    # grading_type which may be ``error``
    orig_gt = meta.get("grading_type")
    if orig_gt:
        buckets["by_grading"][orig_gt].append(score)

    return errored, missing_meta


def _build_task_name_index(
    task_meta: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Map normalised task display name → t_id for fallback lookups.

    Used for the hermes single-summary format whose ``task_id`` field uses
    internal hermes slugs that don't match our ``t_id`` system. ``task_name``
    matches our ``name`` field exactly.
    """
    index: dict[str, str] = {}
    for t_id, meta in task_meta.items():
        name = (meta.get("name") or "").strip().lower()
        if name:
            index[name] = t_id
    return index


def _build_task_slug_index(
    task_meta: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Map hermes ``task_id`` slugs (``core_id`` / ``task_id``) → ``t_id``."""
    index: dict[str, str] = {}
    for t_id, meta in task_meta.items():
        index[t_id.lower()] = t_id
        for key in ("core_id", "task_id"):
            slug = (meta.get(key) or "").strip().lower()
            if slug:
                index[slug] = t_id
        fn = (meta.get("filename") or "").strip()
        if fn.endswith(".md"):
            _prefix, sep, tail = fn[:-3].partition("_")
            if sep and tail:
                index[tail.lower()] = t_id
    return index


def _resolve_t_id_from_hermes_result(
    r: dict[str, Any],
    name_to_tid: dict[str, str],
    slug_to_tid: dict[str, str],
) -> str | None:
    name_key = (r.get("task_name") or "").strip().lower()
    if name_key and name_key in name_to_tid:
        return name_to_tid[name_key]
    slug = (r.get("task_id") or "").strip().lower()
    if slug and slug in slug_to_tid:
        return slug_to_tid[slug]
    # Some hermes exports use ``T042``-style ids in ``task_id``.
    if slug and slug.upper() in slug_to_tid:
        return slug_to_tid[slug.upper()]
    return None


def _is_pawbench_metrics(doc: dict[str, Any]) -> bool:
    """True when ``metrics.json`` follows the standard per-task layout."""
    return isinstance(doc, dict) and "task_score" in doc


def _find_hermes_summary(harness_dir: Path) -> Path | None:
    """Pick the hermes roll-up JSON under ``<harness>/`` (not ``workspaces/``)."""
    best: tuple[int, Path] | None = None
    for sf in sorted(harness_dir.glob("*.json")):
        try:
            head = json.loads(sf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(head, dict):
            continue
        results = head.get("results")
        if not isinstance(results, list) or not results:
            continue
        n = len(results)
        if best is None or n > best[0]:
            best = (n, sf)
    return best[1] if best else None


def _discover_per_task_metrics(harness_dir: Path) -> list[Path]:
    """``T*/output/metrics.json`` files that contain pawbench ``task_score``."""
    out: list[Path] = []
    for mp in sorted(harness_dir.glob("T*/output/metrics.json")):
        try:
            doc = json.loads(mp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if _is_pawbench_metrics(doc):
            out.append(mp)
    return out


def _aggregate_from_per_task_dirs(
    metrics_files: list[Path],
    task_meta: dict[str, dict[str, Any]],
    per_task_score: list[float],
    automated_scores: list[float],
    judge_scores: list[float],
    buckets: dict[str, dict[str, list[float]]],
) -> tuple[int, int]:
    errored = 0
    missing_meta = 0
    for mp in metrics_files:
        try:
            d = json.loads(mp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[aggregate_results] skip {mp}: {exc}", file=sys.stderr)
            continue

        # Use the directory name as the canonical t-id source.
        t_id = mp.parent.parent.name.split("_", 1)[0]
        de, dm = _record_task(
            t_id=t_id,
            score=float(d.get("task_score") or 0.0),
            grading_type=d.get("grading_type") or "unknown",
            breakdown=d.get("breakdown") or {},
            status=d.get("status") or "",
            task_meta=task_meta,
            per_task_score=per_task_score,
            automated_scores=automated_scores,
            judge_scores=judge_scores,
            buckets=buckets,
        )
        errored += de
        missing_meta += dm
    return errored, missing_meta


def _aggregate_from_single_summary(
    summary_file: Path,
    task_meta: dict[str, dict[str, Any]],
    per_task_score: list[float],
    automated_scores: list[float],
    judge_scores: list[float],
    buckets: dict[str, dict[str, list[float]]],
) -> tuple[int, int]:
    """Parse hermes' single-file summary format.

    Schema (one JSON per ``<harness_dir>/<timestamp>.json``)::

        {
          "summary": {...},
          "results": [
            {"task_id": "...", "task_name": "...",
             "score": float, "max_score": float, "passed": bool,
             "grading_type": "hybrid|automated|llm_judge|error",
             "breakdown": {...}, "status": "success|error|timeout",
             "timed_out": bool, "labels": {...}},
            ...
          ]
        }

    The ``task_id`` field uses internal hermes slugs that don't match our
    ``t_id`` system, so we map back via ``task_name`` against ``tasks.json``.
    """
    try:
        d = json.loads(summary_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[aggregate_results] skip {summary_file}: {exc}", file=sys.stderr)
        return 0, 0

    results = d.get("results") or []
    if not results:
        return 0, 0

    name_to_tid = _build_task_name_index(task_meta)
    slug_to_tid = _build_task_slug_index(task_meta)
    errored = 0
    missing_meta = 0
    for r in results:
        # ``score`` is the canonical 0-1 task score for hermes; fall back to
        # ``task_score`` for safety in case the schema evolves.
        score = float(r.get("score") if r.get("score") is not None else r.get("task_score") or 0.0)
        status = r.get("status") or ""
        if r.get("timed_out") and status not in {"error", "timeout"}:
            # treat timeouts as errors for accounting parity with the
            # per-task-dir format
            status = "error"

        t_id = _resolve_t_id_from_hermes_result(r, name_to_tid, slug_to_tid)

        de, dm = _record_task(
            t_id=t_id,
            score=score,
            grading_type=r.get("grading_type") or "unknown",
            breakdown=r.get("breakdown") or {},
            status=status,
            task_meta=task_meta,
            per_task_score=per_task_score,
            automated_scores=automated_scores,
            judge_scores=judge_scores,
            buckets=buckets,
        )
        errored += de
        missing_meta += dm
    return errored, missing_meta


def aggregate_pair(
    run_dir: Path,
    model_dir: Path,
    harness_dir: Path,
    task_meta: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Aggregate a single ``(run, model, harness)`` triple.

    Tries two on-disk layouts:

    1. Per-task directories with ``T*/output/metrics.json`` (the default
       hermes/openclaw/qwenpaw layout).
    2. A single ``<timestamp>.json`` summary file directly under the harness
       directory (used by some hermes runs, e.g.
       ``result/.../openai.claude-opus-4-6/hermes/20260525_115007.json``).
    """
    per_task_score: list[float] = []
    automated_scores: list[float] = []
    judge_scores: list[float] = []

    # bucket → list of task scores (errors counted as 0)
    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    metrics_files = _discover_per_task_metrics(harness_dir)
    summary_file = _find_hermes_summary(harness_dir)

    if metrics_files:
        errored, missing_meta = _aggregate_from_per_task_dirs(
            metrics_files,
            task_meta,
            per_task_score,
            automated_scores,
            judge_scores,
            buckets,
        )
    elif summary_file is not None:
        # Hermes roll-up export (e.g. ``openai.claude-opus-4-6/hermes/<ts>.json``)
        # with ``results[]`` instead of per-task ``T*/output/metrics.json``.
        errored, missing_meta = _aggregate_from_single_summary(
            summary_file,
            task_meta,
            per_task_score,
            automated_scores,
            judge_scores,
            buckets,
        )
    else:
        return None

    if not per_task_score:
        return None

    # Fill missing tasks with score=0 so the denominator is always the full
    # task set, preventing harness crashes from inflating the mean.
    tasks_recorded = len(per_task_score)
    total_expected = len(task_meta)
    missing_count = max(0, total_expected - tasks_recorded)

    # For per-task dirs we can identify exactly which tasks are missing.
    missing_tids: set[str] = set()
    if metrics_files and missing_count > 0:
        seen_tids = {mp.parent.parent.name.split("_", 1)[0] for mp in metrics_files}
        missing_tids = set(task_meta.keys()) - seen_tids
    elif not metrics_files and missing_count > 0:
        # Summary format: task_name resolution can have collisions (duplicate
        # names across zh/en variants), so fall back to count-based fill.
        missing_tids = set(f"_missing_{i}" for i in range(missing_count))
    for t_id in missing_tids:
        _record_task(
            t_id=t_id,
            score=0.0,
            grading_type="missing",
            breakdown={},
            status="missing",
            task_meta=task_meta,
            per_task_score=per_task_score,
            automated_scores=automated_scores,
            judge_scores=judge_scores,
            buckets=buckets,
        )

    # Compute slice means
    by_dims = {dim: {k: safe_mean(v) for k, v in d.items()} for dim, d in buckets.items()}

    return {
        "run": run_dir.name,
        "model": display_model(model_dir.name),
        "harness": canonical_harness(harness_dir.name),
        "overall": safe_mean(per_task_score),
        "automated": safe_mean(automated_scores),
        "judge": safe_mean(judge_scores),
        "tasks": len(per_task_score),
        "tasks_total": len(task_meta) or len(per_task_score),
        "tasks_errored": errored,
        "tasks_missing": len(missing_tids),
        "missing_metadata": missing_meta,
        "_raw_model_dir": model_dir.name,
        "_raw_harness_dir": harness_dir.name,
        "updated": run_updated(run_dir.name),
        **by_dims,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--run",
        action="append",
        default=None,
        help="Restrict to specific run directory name(s); default = all under result/",
    )
    ap.add_argument(
        "--out-dir",
        default=str(SUBMISSIONS_DIR),
        help="Where to write submission JSONs (default: <repo>/submissions/)",
    )
    args = ap.parse_args()

    runs = discover_runs(args.run)
    if not runs:
        print(
            f"[aggregate_results] no runs found under {RESULT_ROOT.relative_to(REPO_ROOT)}",
            file=sys.stderr,
        )
        return 0

    task_meta = load_task_metadata()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for run_dir in runs:
        for model_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            for harness_dir in sorted(p for p in model_dir.iterdir() if p.is_dir()):
                row = aggregate_pair(run_dir, model_dir, harness_dir, task_meta)
                if not row:
                    continue
                # Use the canonical harness slug in the filename so that
                # renaming a harness alias (e.g. ``copaw`` → ``qwenpaw``)
                # automatically retires the old file once the user re-runs
                # the aggregator and removes the stale submissions.
                fname = f"{run_dir.name}__{row['_raw_model_dir']}__{row['harness']}.json"
                out_path = out_dir / fname
                # don't ship the internal raw-name keys into the public file
                public = {k: v for k, v in row.items() if not k.startswith("_")}
                out_path.write_text(
                    json.dumps(public, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                written += 1
                print(
                    f"[aggregate_results] {row['model']:30s} × {row['harness']:10s}  "
                    f"overall={row['overall']:.3f}  "
                    f"errored={row['tasks_errored']:>3d}  "
                    f"→ {out_path.relative_to(REPO_ROOT)}"
                )

    print(f"[aggregate_results] wrote {written} submission file(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
