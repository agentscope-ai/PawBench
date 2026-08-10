#!/usr/bin/env python3
"""Build the leaderboard JSON consumed by the website.

The script reads every ``submissions/*.json`` entry (one per
``(run, model, harness)``, produced by ``aggregate_results.py`` for raw runs
under ``result/`` or hand-written by submitters), dedupes by
``(model, harness)`` keeping the freshest ``updated`` date, and writes
``site/src/data/leaderboard.json``.

When ``submissions/`` is empty it falls back to a small inline mock so the UI
still renders.

Submission schema::

    {
      "run":     "pawbench-rerun-20260519",   # optional, for traceability
      "model":   "gpt-5.4",
      "harness": "openclaw",
      "overall": 0.612,
      "automated": 0.71,
      "judge": 0.55,
      "tasks": 150,
      "tasks_errored": 0,
      "by_source":     { "claweval": 0.65, ... },
      "by_capability": { "Tool_Use": 0.72, ... },
      "by_complexity": { "L1": 0.81, "L2": 0.66, "L3": 0.58 },
      "updated": "2026-05-18"
    }
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_ROOT = Path(__file__).resolve().parents[1]
SUBMISSIONS_DIR = REPO_ROOT / "submissions"
OUT_DIR = SITE_ROOT / "src" / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Harness display name + current version, surfaced in the UI under each column
# header. Edit when a new release lands.
HARNESS_META: dict[str, dict[str, str]] = {
    "qwenpaw": {"display": "QwenPaw", "version": "1.1.3"},
    "openclaw": {"display": "OpenClaw", "version": "2026.4.24"},
    "hermes": {"display": "Hermes", "version": "2026.4.23"},
    "workharness": {"display": "WorkHarness", "version": "2026.8.4"},
}

_BASE_ROWS: list[dict[str, Any]] = [
    {
        "model": "claude-opus-4.6",
        "harness": "openclaw",
        "overall": 0.704,
        "automated": 0.75,
        "judge": 0.66,
    },
    {
        "model": "claude-opus-4.6",
        "harness": "qwenpaw",
        "overall": 0.681,
        "automated": 0.74,
        "judge": 0.62,
    },
    {
        "model": "claude-opus-4.6",
        "harness": "hermes",
        "overall": 0.659,
        "automated": 0.72,
        "judge": 0.59,
    },
    {
        "model": "kimi-k2.6",
        "harness": "openclaw",
        "overall": 0.623,
        "automated": 0.70,
        "judge": 0.55,
    },
    {"model": "glm-5.1", "harness": "openclaw", "overall": 0.623, "automated": 0.69, "judge": 0.56},
    {"model": "gpt-5.4", "harness": "qwenpaw", "overall": 0.612, "automated": 0.69, "judge": 0.54},
    {"model": "gpt-5.4", "harness": "openclaw", "overall": 0.603, "automated": 0.68, "judge": 0.53},
    {
        "model": "deepseek-v4-pro",
        "harness": "openclaw",
        "overall": 0.598,
        "automated": 0.67,
        "judge": 0.53,
    },
    {
        "model": "qwen3.6-plus",
        "harness": "openclaw",
        "overall": 0.588,
        "automated": 0.66,
        "judge": 0.52,
    },
    {
        "model": "qwen3.6-plus",
        "harness": "qwenpaw",
        "overall": 0.572,
        "automated": 0.65,
        "judge": 0.50,
    },
    {
        "model": "minimax-m2.7",
        "harness": "openclaw",
        "overall": 0.487,
        "automated": 0.55,
        "judge": 0.43,
    },
]


def _synth_slices(seed: int, overall: float) -> dict[str, dict[str, float]]:
    """Deterministically jitter ``overall`` to fabricate per-slice scores.

    Used only when MOCK_ROWS are emitted — real submissions ship their own
    ``by_*`` payloads.
    """
    import random

    r = random.Random(seed)

    def jitter(amount: float, bias: float = 0.0) -> float:
        return max(0.0, min(1.0, overall + r.uniform(-amount, amount) + bias))

    by_complexity = {
        "L1": jitter(0.10, +0.10),
        "L2": jitter(0.06),
        "L3": jitter(0.05, -0.05),
    }
    by_capability = {
        "Tool_Use": jitter(0.04),
        "Planning": jitter(0.06),
        "Logic_Reasoning": jitter(0.05),
        "Self_Verification": jitter(0.07),
        "Code_Manipulation": jitter(0.06),
        "Math_Computation": jitter(0.07),
        "Skill_Use": jitter(0.08),
    }
    by_source = {
        "claweval": jitter(0.05),
        "qwenclawbench": jitter(0.05),
        "pinchbench": jitter(0.07),
        "qwenpawbench": jitter(0.06),
        "skillsbench": jitter(0.08),
        "wildclawbench": jitter(0.06),
    }
    by_modality = {
        "text": jitter(0.04, +0.05),
        "multimodal": jitter(0.07, -0.08),
    }
    by_channel = {
        "image": jitter(0.07, -0.05),
        "audio": jitter(0.08, -0.10),
        "video": jitter(0.09, -0.12),
    }
    by_grading = {
        "automated": jitter(0.05, +0.05),
        "llm_judge": jitter(0.06),
        "hybrid": jitter(0.04),
    }
    by_environment = {
        "closed": jitter(0.04, +0.03),
        "open": jitter(0.07, -0.03),
    }
    by_scenario = {
        "Software_Engineering/Code": jitter(0.05),
        "Data_Analytics/Business_Intelligence": jitter(0.06),
        "Office_Productivity/Document": jitter(0.05),
        "Automation_Platform/Agent": jitter(0.07),
        "Content_Creation/Design": jitter(0.07),
        "Safety_Alignment/Data_Protection": jitter(0.05, +0.05),
        "Safety_Alignment/Prompt_Injection": jitter(0.06, +0.04),
        "Office_Productivity/Task_Management": jitter(0.05),
        "Knowledge/QA": jitter(0.04),
        "Content_Creation/Writing": jitter(0.06),
        "Software_Engineering/DevOps": jitter(0.06),
        "Information_Retrieval/Market": jitter(0.07),
        "Manufacturing_Engineering/Quality_Control": jitter(0.08, -0.04),
    }
    by_scenario_top = {
        "Software_Engineering": jitter(0.05),
        "Data_Analytics": jitter(0.06),
        "Office_Productivity": jitter(0.05),
        "Automation_Platform": jitter(0.07),
        "Content_Creation": jitter(0.06),
        "Safety_Alignment": jitter(0.05, +0.04),
        "Knowledge": jitter(0.05),
        "Information_Retrieval": jitter(0.06),
        "Manufacturing_Engineering": jitter(0.08, -0.05),
        "Legal": jitter(0.08, -0.05),
        "Finance_Investment": jitter(0.07, -0.03),
    }

    def round_vals(d: dict[str, float]) -> dict[str, float]:
        return {k: round(v, 4) for k, v in d.items()}

    return {
        "by_complexity": round_vals(by_complexity),
        "by_capability": round_vals(by_capability),
        "by_source": round_vals(by_source),
        "by_modality": round_vals(by_modality),
        "by_channel": round_vals(by_channel),
        "by_grading": round_vals(by_grading),
        "by_environment": round_vals(by_environment),
        "by_scenario": round_vals(by_scenario),
        "by_scenario_top": round_vals(by_scenario_top),
    }


MOCK_ROWS: list[dict[str, Any]] = []
for _i, _row in enumerate(_BASE_ROWS):
    _full = {**_row, "tasks": 150, **_synth_slices(_i * 1009 + 17, _row["overall"])}
    MOCK_ROWS.append(_full)


def load_submissions() -> list[dict[str, Any]]:
    if not SUBMISSIONS_DIR.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for p in sorted(SUBMISSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[build_leaderboard] skip {p.name}: {exc}", file=sys.stderr)
            continue
        if isinstance(data, list):
            rows.extend(data)
        else:
            rows.append(data)
    return rows


def normalize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize, dedupe by (model, harness), and sort.

    When two rows share the same ``(model, harness)`` we keep the one with the
    later ``updated`` date — typical case is a re-run replacing an older run.
    """
    today = date.today().isoformat()
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if "model" not in r or "harness" not in r:
            continue
        key = (r["model"], r["harness"])
        normalized = {
            "model": r["model"],
            "harness": r["harness"],
            "overall": float(r.get("overall", 0.0)),
            "automated": float(r.get("automated", 0.0)),
            "judge": float(r.get("judge", 0.0)),
            "tasks": int(r.get("tasks", 0)),
            "tasks_errored": int(r.get("tasks_errored", 0)),
            "run": r.get("run"),
            "by_source": r.get("by_source", {}),
            "by_capability": r.get("by_capability", {}),
            "by_complexity": r.get("by_complexity", {}),
            "by_modality": r.get("by_modality", {}),
            "by_channel": r.get("by_channel", {}),
            "by_scenario": r.get("by_scenario", {}),
            "by_scenario_top": r.get("by_scenario_top", {}),
            "by_grading": r.get("by_grading", {}),
            "by_environment": r.get("by_environment", {}),
            "by_category": r.get("by_category", {}),
            "by_subcategory": r.get("by_subcategory", {}),
            "updated": r.get("updated", today),
        }
        prev = by_key.get(key)
        if prev is None or normalized["updated"] >= prev["updated"]:
            by_key[key] = normalized
    out = list(by_key.values())
    out.sort(key=lambda x: x["overall"], reverse=True)
    return out


def _build_matrix_for(
    rows: list[dict[str, Any]],
    score_of: Any,  # Callable[[row], float | None]
) -> dict[str, Any]:
    """Generic matrix builder. ``score_of(row)`` picks a scalar (or None) per row.

    Returns ``{models, harnesses, rows: [{model, <h>: float|None, ...}]}``,
    sorted by per-row mean across harnesses (descending).
    """
    models = sorted({r["model"] for r in rows})
    harnesses = sorted({r["harness"] for r in rows})
    cells: dict[str, dict[str, float | None]] = {m: dict.fromkeys(harnesses) for m in models}
    for r in rows:
        v = score_of(r)
        if v is None:
            continue
        try:
            cells[r["model"]][r["harness"]] = float(v)
        except (TypeError, ValueError):
            continue
    matrix = []
    for m in models:
        row: dict[str, Any] = {"model": m}
        for h in harnesses:
            v = cells[m][h]
            row[h] = None if v is None else round(v, 4)
        matrix.append(row)

    def _row_avg(row: dict[str, Any]) -> float:
        vals = [row[h] for h in harnesses if isinstance(row.get(h), int | float)]
        return sum(vals) / len(vals) if vals else 0.0

    matrix.sort(key=_row_avg, reverse=True)
    return {"models": [r["model"] for r in matrix], "harnesses": harnesses, "rows": matrix}


def build_matrix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _build_matrix_for(rows, lambda r: r.get("overall"))


def build_matrix_by_modality(rows: list[dict[str, Any]], modality: str) -> dict[str, Any]:
    """Build a matrix using ``by_modality[modality]`` as the cell value.

    A model × harness cell is ``None`` when that subset has no scored tasks
    (e.g. text-only models with no multimodal samples in the run).
    """
    return _build_matrix_for(rows, lambda r: (r.get("by_modality") or {}).get(modality))


def collect_harness_meta(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Pick out display+version for the harnesses that actually appear."""
    seen = {r["harness"] for r in rows}
    out: dict[str, dict[str, str]] = {}
    for h in seen:
        meta = HARNESS_META.get(h)
        out[h] = {
            "display": (meta or {}).get("display", h),
            "version": (meta or {}).get("version", ""),
        }
    return out


def main() -> int:
    submissions = load_submissions()
    is_mock = not submissions
    rows = normalize(submissions or MOCK_ROWS)

    payload = {
        "is_mock": is_mock,
        "rows": rows,
        "matrix": build_matrix(rows),
        "matrix_text": build_matrix_by_modality(rows, "text"),
        "matrix_multimodal": build_matrix_by_modality(rows, "multimodal"),
        "harnesses": collect_harness_meta(rows),
        "generated_at": date.today().isoformat(),
    }

    out = OUT_DIR / "leaderboard.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[build_leaderboard] wrote {len(rows)} rows "
        f"({'mock' if is_mock else 'real'}) → {out.relative_to(SITE_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
