from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.paths import HARNESS_WORK_ROOT  # noqa: E402


DEFAULT_OUT = HARNESS_WORK_ROOT / "pawbench_output_ingest"


@dataclass(frozen=True)
class PawBenchRunRecord:
    source_format: str
    run_group: str
    benchmark: str
    model: str
    harness: str
    task_id: str
    task_name: str | None
    score: float | None
    max_score: float | None
    passed: bool | None
    status: str | None
    grading_type: str | None
    breakdown: dict[str, Any]
    notes: str
    execution_time: float | None
    wall_time_s: float | None
    exit_code: int | None
    usage: dict[str, Any]
    transcript_length: int | None
    timed_out: bool | None
    error: str
    anomaly: dict[str, Any]
    labels: dict[str, Any]
    result_path: str
    metrics_path: str | None
    transcript_path: str | None
    workspace_path: str | None

    @property
    def run_key(self) -> str:
        return "::".join([self.run_group, self.model, self.harness, self.task_id])


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def compact(value: Any, limit: int = 320) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def normalize_anomaly(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        items = value.get("items")
        if isinstance(items, list):
            normalized_items = items
        elif items is None:
            normalized_items = []
        else:
            normalized_items = [items]
        return {
            "is_anomalous": bool(value.get("is_anomalous") or normalized_items),
            "has_error": bool(value.get("has_error")),
            "has_api_error": bool(value.get("has_api_error")),
            "items": normalized_items,
        }
    if isinstance(value, list):
        return {
            "is_anomalous": bool(value),
            "has_error": False,
            "has_api_error": False,
            "items": value,
        }
    if value:
        return {
            "is_anomalous": True,
            "has_error": False,
            "has_api_error": False,
            "items": [value],
        }
    return {"is_anomalous": False, "has_error": False, "has_api_error": False, "items": []}


def count_jsonl_rows(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def infer_checkpoint_identity(path: Path, payload: dict[str, Any], input_root: Path) -> tuple[str, str, str, str]:
    parts = path.parts
    benchmark = str(payload.get("benchmark") or "pawbench")
    model = str(payload.get("model") or "")
    harness = str(payload.get("harness") or payload.get("agent") or payload.get("agent_type") or "")
    run_group = input_root.name

    for index, part in enumerate(parts):
        if part == benchmark and index + 2 < len(parts):
            model = model or parts[index + 1]
            harness = harness or parts[index + 2]
            if index >= 1:
                run_group = parts[index - 1]
            break

    if not harness and path.parent.name != model:
        harness = path.parent.name
    return run_group, benchmark, model or "unknown_model", harness or "unknown_harness"


def transcript_for_checkpoint_result(
    results_dir: Path,
    task_id: str,
    occurrence_index: int,
) -> Path | None:
    transcripts_dir = results_dir / "transcripts"
    if not transcripts_dir.exists():
        return None
    candidates: list[Path] = []
    if occurrence_index > 1:
        candidates.append(transcripts_dir / f"{task_id}_run{occurrence_index}.jsonl")
    candidates.append(transcripts_dir / f"{task_id}.jsonl")
    candidates.extend(sorted(transcripts_dir.glob(f"{task_id}_run*.jsonl")))
    candidates.extend(sorted(transcripts_dir.glob(f"{task_id}*.jsonl")))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def workspace_for_checkpoint_result(results_dir: Path, task_id: str) -> Path | None:
    path = results_dir / "workspaces" / task_id
    return path if path.exists() else None


def records_from_checkpoint(path: Path, input_root: Path) -> list[PawBenchRunRecord]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return []
    run_group, benchmark, model, harness = infer_checkpoint_identity(path, payload, input_root)
    occurrence_by_task: Counter[str] = Counter()
    records: list[PawBenchRunRecord] = []
    for item in payload["results"]:
        if not isinstance(item, dict) or not item.get("task_id"):
            continue
        task_id = str(item["task_id"])
        occurrence_by_task[task_id] += 1
        transcript = transcript_for_checkpoint_result(path.parent, task_id, occurrence_by_task[task_id])
        workspace = workspace_for_checkpoint_result(path.parent, task_id)
        transcript_length = safe_int(item.get("transcript_length"))
        if transcript_length is None:
            transcript_length = count_jsonl_rows(transcript)
        records.append(
            PawBenchRunRecord(
                source_format="pawbench_checkpoint",
                run_group=run_group,
                benchmark=benchmark,
                model=model,
                harness=harness,
                task_id=task_id,
                task_name=item.get("task_name"),
                score=safe_float(item.get("score")),
                max_score=safe_float(item.get("max_score")),
                passed=safe_bool(item.get("passed")),
                status=item.get("status"),
                grading_type=item.get("grading_type"),
                breakdown=item.get("breakdown") if isinstance(item.get("breakdown"), dict) else {},
                notes=str(item.get("notes") or ""),
                execution_time=safe_float(item.get("execution_time")),
                wall_time_s=safe_float(item.get("wall_time_s")),
                exit_code=safe_int(item.get("exit_code")),
                usage=item.get("usage") if isinstance(item.get("usage"), dict) else {},
                transcript_length=transcript_length,
                timed_out=safe_bool(item.get("timed_out")),
                error=str(item.get("error") or ""),
                anomaly=normalize_anomaly(item.get("anomaly")),
                labels=item.get("labels") if isinstance(item.get("labels"), dict) else {},
                result_path=str(path),
                metrics_path=None,
                transcript_path=str(transcript) if transcript else None,
                workspace_path=str(workspace) if workspace else None,
            )
        )
    return records


def infer_legacy_identity(metrics_path: Path, input_root: Path) -> tuple[str, str, str, str]:
    rel = metrics_path.relative_to(input_root)
    parts = rel.parts
    output_index = parts.index("output")
    prefix = parts[:output_index]
    run_group = input_root.name
    model = harness = task_id = "unknown"
    if len(prefix) >= 4 and prefix[0].startswith("qwen3.7-max-"):
        run_group, harness, model, task_id = prefix[:4]
    elif len(prefix) >= 3:
        model, harness, task_id = prefix[-3:]
    return run_group, model, harness, task_id


def transcript_for_legacy_metrics(metrics_path: Path, model: str, harness: str) -> Path | None:
    output_dir = metrics_path.parent
    patterns = [
        f"results/*/pawbench/{model}/{harness}/transcripts/*.jsonl",
        f"run/*/pawbench/{model}/{harness}/transcripts/*.jsonl",
        "results/*/pawbench/*/*/transcripts/*.jsonl",
        "run/*/pawbench/*/*/transcripts/*.jsonl",
        "transcripts/*.jsonl",
        "**/transcripts/*.jsonl",
    ]
    seen: set[Path] = set()
    candidates: list[Path] = []
    for pattern in patterns:
        for candidate in sorted(output_dir.glob(pattern)):
            if candidate not in seen and candidate.is_file():
                seen.add(candidate)
                candidates.append(candidate)
    if not candidates:
        return None
    return candidates[0]


def workspace_for_legacy_metrics(metrics_path: Path) -> Path | None:
    task_root = metrics_path.parent.parent
    for candidate in (task_root / "workspace", metrics_path.parent / "workspace", metrics_path.parent / "workspaces"):
        if candidate.exists():
            return candidate
    return None


def record_from_legacy_metrics(metrics_path: Path, input_root: Path) -> PawBenchRunRecord:
    rel_parts = metrics_path.relative_to(input_root).parts
    if "workspaces" in rel_parts:
        raise ValueError(f"workspace metrics are not benchmark run metrics: {metrics_path}")
    metrics = read_json(metrics_path)
    if not isinstance(metrics, dict):
        metrics = {}
    run_group, model, harness, task_id = infer_legacy_identity(metrics_path, input_root)
    transcript = transcript_for_legacy_metrics(metrics_path, model, harness)
    workspace = workspace_for_legacy_metrics(metrics_path)
    anomaly = normalize_anomaly(metrics.get("anomaly"))
    transcript_length = safe_int(metrics.get("transcript_length"))
    if transcript_length is None:
        transcript_length = count_jsonl_rows(transcript)
    return PawBenchRunRecord(
        source_format="pawbench_legacy_metrics",
        run_group=run_group,
        benchmark="pawbench",
        model=model,
        harness=harness,
        task_id=str(metrics.get("instance_id") or task_id),
        task_name=metrics.get("task_name"),
        score=safe_float(metrics.get("task_score", metrics.get("score"))),
        max_score=safe_float(metrics.get("max_score", 1.0)),
        passed=safe_bool(metrics.get("passed")),
        status=metrics.get("status"),
        grading_type=metrics.get("grading_type"),
        breakdown=metrics.get("breakdown") if isinstance(metrics.get("breakdown"), dict) else {},
        notes=str(metrics.get("notes") or ""),
        execution_time=safe_float(metrics.get("execution_time")),
        wall_time_s=safe_float(metrics.get("wall_time_s")),
        exit_code=safe_int(metrics.get("exit_code")),
        usage=metrics.get("usage") if isinstance(metrics.get("usage"), dict) else {},
        transcript_length=transcript_length,
        timed_out=safe_bool(metrics.get("timed_out")),
        error=str(metrics.get("error") or ""),
        anomaly=anomaly,
        labels=metrics.get("labels") if isinstance(metrics.get("labels"), dict) else {},
        result_path=str(metrics_path),
        metrics_path=str(metrics_path),
        transcript_path=str(transcript) if transcript else None,
        workspace_path=str(workspace) if workspace else None,
    )


def is_checkpoint_json(path: Path) -> bool:
    if path.name in {"metrics.json", "manifest.json", "summary.json"}:
        return False
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("results"), list)


def is_legacy_embedded_checkpoint(path: Path, input_root: Path) -> bool:
    try:
        parts = path.relative_to(input_root).parts
    except ValueError:
        return False
    if "output" not in parts or "pawbench" not in parts:
        return False
    output_index = parts.index("output")
    pawbench_index = parts.index("pawbench")
    if output_index >= pawbench_index:
        return False
    between = set(parts[output_index + 1 : pawbench_index])
    return bool(between & {"results", "run"})


def dedupe_records(records: list[PawBenchRunRecord]) -> list[PawBenchRunRecord]:
    by_key: dict[tuple[str, str, str, str, str], PawBenchRunRecord] = {}
    for record in records:
        key = (
            record.run_group,
            record.model,
            record.harness,
            record.task_id,
            record.transcript_path or record.metrics_path or record.result_path,
        )
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = record
            continue
        existing_rank = (existing.metrics_path is not None, existing.transcript_path is not None)
        record_rank = (record.metrics_path is not None, record.transcript_path is not None)
        if record_rank > existing_rank:
            by_key[key] = record
    return sorted(by_key.values(), key=lambda item: (item.run_group, item.model, item.harness, item.task_id, item.result_path))


def collect_records(input_root: Path, *, include_legacy: bool = True, include_checkpoints: bool = True) -> list[PawBenchRunRecord]:
    records: list[PawBenchRunRecord] = []
    if include_checkpoints:
        for path in sorted(input_root.rglob("*.json")):
            if is_legacy_embedded_checkpoint(path, input_root):
                continue
            if is_checkpoint_json(path):
                records.extend(records_from_checkpoint(path, input_root))
    if include_legacy:
        for metrics_path in sorted(input_root.rglob("metrics.json")):
            try:
                records.append(record_from_legacy_metrics(metrics_path, input_root))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
    return dedupe_records(records)


def record_to_score_matrix_row(record: PawBenchRunRecord) -> dict[str, Any]:
    return {
        "run_key": record.run_key,
        "run_group": record.run_group,
        "benchmark": record.benchmark,
        "model": record.model,
        "harness": record.harness,
        "task_id": record.task_id,
        "score": record.score,
        "max_score": record.max_score,
        "passed": record.passed,
        "status": record.status,
        "grading_type": record.grading_type,
        "breakdown": record.breakdown,
        "notes": compact(record.notes, 600),
        "execution_time": record.execution_time,
        "wall_time_s": record.wall_time_s,
        "exit_code": record.exit_code,
        "usage": record.usage,
        "transcript_length": record.transcript_length,
        "timed_out": record.timed_out,
        "anomaly_items": record.anomaly.get("items", []),
        "labels": record.labels,
        "metrics_found": bool(record.metrics_path),
        "transcript_found": bool(record.transcript_path),
        "result_path": record.result_path,
        "metrics_path": record.metrics_path,
        "transcript_path": record.transcript_path,
        "workspace_path": record.workspace_path,
        "source_format": record.source_format,
    }


def record_to_attribution_input_row(record: PawBenchRunRecord) -> dict[str, Any]:
    return {
        "run_group": record.run_group,
        "benchmark": record.benchmark,
        "model": record.model,
        "harness": record.harness,
        "task_id": record.task_id,
        "task_name": record.task_name,
        "trajectory_path": record.transcript_path,
        "transcript_path": record.transcript_path,
        "metrics_path": record.metrics_path,
        "result_path": record.result_path,
        "workspace_path": record.workspace_path,
        "score": record.score,
        "max_score": record.max_score,
        "passed": record.passed,
        "status": record.status,
        "grading_type": record.grading_type,
        "breakdown": record.breakdown,
        "notes": record.notes,
        "execution_time": record.execution_time,
        "wall_time_s": record.wall_time_s,
        "exit_code": record.exit_code,
        "usage": record.usage,
        "transcript_length": record.transcript_length,
        "timed_out": record.timed_out,
        "error": record.error,
        "anomaly": record.anomaly,
        "labels": record.labels,
        "source_format": record.source_format,
    }


def build_summary(records: list[PawBenchRunRecord]) -> dict[str, Any]:
    by_format = Counter(record.source_format for record in records)
    by_harness = Counter(record.harness for record in records)
    by_model = Counter(record.model for record in records)
    missing_transcripts = sum(record.transcript_path is None for record in records)
    missing_scores = sum(record.score is None for record in records)
    return {
        "generated_at": now(),
        "record_count": len(records),
        "source_formats": dict(by_format.most_common()),
        "harnesses": dict(by_harness.most_common()),
        "models": dict(by_model.most_common()),
        "missing_transcripts": missing_transcripts,
        "missing_scores": missing_scores,
        "ready_for_reasoning": missing_transcripts == 0,
        "ready_for_score_matrix": missing_scores < len(records) if records else False,
    }


def write_outputs(records: list[PawBenchRunRecord], out_dir: Path, input_root: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = [asdict(record) | {"run_key": record.run_key} for record in records]
    score_rows = [record_to_score_matrix_row(record) for record in records]
    attribution_rows = [record_to_attribution_input_row(record) for record in records]
    write_jsonl(out_dir / "normalized_pawbench_records.jsonl", raw_rows)
    write_jsonl(out_dir / "score_matrix_long.jsonl", score_rows)
    write_jsonl(out_dir / "attribution_input_runs.jsonl", attribution_rows)
    summary = {
        **build_summary(records),
        "input_root": str(input_root),
        "outputs": {
            "normalized_pawbench_records": str(out_dir / "normalized_pawbench_records.jsonl"),
            "score_matrix_long": str(out_dir / "score_matrix_long.jsonl"),
            "attribution_input_runs": str(out_dir / "attribution_input_runs.jsonl"),
        },
    }
    write_json(out_dir / "ingest_summary.json", summary)
    write_report(out_dir / "REPORT.md", summary)
    return summary


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# PawBench Output Ingest",
        "",
        "## Summary",
        "",
        f"- Records: {summary['record_count']}",
        f"- Ready for reasoning: `{summary['ready_for_reasoning']}`",
        f"- Ready for score matrix: `{summary['ready_for_score_matrix']}`",
        f"- Missing transcripts: {summary['missing_transcripts']}",
        f"- Missing scores: {summary['missing_scores']}",
        "",
        "## Source Formats",
        "",
        "| Format | Count |",
        "| --- | ---: |",
    ]
    for key, value in summary["source_formats"].items():
        lines.append(f"| `{key}` | {value} |")
    lines += [
        "",
        "## Harnesses",
        "",
        "| Harness | Count |",
        "| --- | ---: |",
    ]
    for key, value in summary["harnesses"].items():
        lines.append(f"| `{key}` | {value} |")
    lines += [
        "",
        "## Artifacts",
        "",
        f"- `{summary['outputs']['normalized_pawbench_records']}`",
        f"- `{summary['outputs']['score_matrix_long']}`",
        f"- `{summary['outputs']['attribution_input_runs']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize PawBench eval outputs for Reasoning and Harness-core.")
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-legacy", action="store_true", help="Skip legacy metrics.json tree parsing.")
    parser.add_argument("--no-checkpoints", action="store_true", help="Skip official PawBench checkpoint/result JSON parsing.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    records = collect_records(
        args.input_root,
        include_legacy=not args.no_legacy,
        include_checkpoints=not args.no_checkpoints,
    )
    summary = write_outputs(records, args.out_dir, args.input_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
