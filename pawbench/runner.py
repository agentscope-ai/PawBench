# -*- coding: utf-8 -*-
"""BenchmarkRunner — orchestrates execution of native benchmark backends.

The runner wraps the sync ``backend.run_and_grade()`` calls in
``asyncio.to_thread`` so they don't block the event loop and can be run
concurrently when ``concurrency > 1``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .backend import BenchmarkBackend, TaskResult


class BenchmarkRunner:
    """Run a native benchmark backend against one agent configuration.

    Args:
        backend:       An instantiated :class:`BenchmarkBackend`.
        results_dir:   Directory where JSON result files are written.
        concurrency:   Maximum number of tasks to execute in parallel.
        max_retries:   How many times to retry a failed or timed-out task
                       before giving up (default 3).
        runs_per_task: Run each task this many times and aggregate
                       mean/std/min/max + pass@k (default 1).  Mirrors the
                       ``--runs`` option of the original QwenClawBench runner.
    """

    def __init__(
        self,
        backend: BenchmarkBackend,
        results_dir: str | Path = "./results",
        concurrency: int = 1,
        max_retries: int = 3,
        runs_per_task: int = 1,
    ) -> None:
        self.backend = backend
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.concurrency = max(1, concurrency)
        self.max_retries = max(1, max_retries)
        self.runs_per_task = max(1, runs_per_task)

    # ── public API ────────────────────────────────────────────────────────────

    async def run(
        self,
        agent_config: dict[str, Any],
        task_filter: list[str] | None = None,
        **load_kwargs: Any,
    ) -> list[TaskResult]:
        """Execute all (or filtered) tasks and return results.

        When ``runs_per_task > 1``, each task is executed that many times and
        the results are aggregated (mean/std/min/max per task, pass@k over the
        whole run).  This matches the ``--runs N`` semantics of the original
        QwenClawBench benchmark runner.

        Args:
            agent_config: Dict passed to ``backend.run_and_grade()``.
                          Must contain at minimum ``"model"``.
            task_filter:  Optional list of task IDs to run.
            **load_kwargs: Extra kwargs forwarded to ``backend.load_tasks()``
                           (e.g. ``dataset="..."`` for QwenClawBench).
        """
        import statistics

        tasks = self.backend.load_tasks(task_filter, **load_kwargs)
        if not tasks:
            print(f"[{self.backend.name}] No tasks loaded — nothing to run.")
            return []

        n_runs = self.runs_per_task
        total_work = len(tasks) * n_runs
        print(
            f"[{self.backend.name}] {len(tasks)} task(s) loaded"
            + (f", filter={task_filter}" if task_filter else "")
            + (f", runs_per_task={n_runs}" if n_runs > 1 else "")
        )

        # Generate stable paths before any task runs so checkpoints accumulate
        # in the same file even if the process is interrupted mid-run.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        checkpoint_path = self.results_dir / f"{ts}.json"
        transcripts_dir = self.results_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)

        # all_results[task_id] = list of TaskResult (one per run)
        all_results: dict[str, list[TaskResult]] = {t.task_id: [] for t in tasks}
        flat_results: list[TaskResult] = []

        workspaces_dir: Path | None = None
        if agent_config.get("save_workspace"):
            workspaces_dir = self.results_dir / "workspaces"
            workspaces_dir.mkdir(parents=True, exist_ok=True)

        docker_images_dir: Path | None = None
        if agent_config.get("save_docker_image"):
            docker_images_dir = self.results_dir / "docker_images"
            docker_images_dir.mkdir(parents=True, exist_ok=True)

        # Build work items: (task, run_index 1-based) in order
        work_items = [
            (task, run_idx)
            for task in tasks
            for run_idx in range(1, n_runs + 1)
        ]
        done_so_far = 0

        if self.concurrency == 1:
            for task, run_idx in work_items:
                done_so_far += 1
                label = f"{task.task_id}" + (f" [run {run_idx}/{n_runs}]" if n_runs > 1 else "")
                print(f"\n  [{done_so_far}/{total_work}] {label}")
                cfg = {**agent_config, "run_id": f"{task.task_id}_r{run_idx}"}
                if workspaces_dir is not None:
                    cfg["workspace_save_dir"] = str(workspaces_dir)
                if docker_images_dir is not None:
                    cfg["docker_images_save_dir"] = str(docker_images_dir)
                result = await self._run_with_retry(task, cfg)
                all_results[task.task_id].append(result)
                flat_results.append(result)
                _save_transcript(result, transcripts_dir, run_idx=run_idx if n_runs > 1 else None)
                _write_checkpoint(flat_results, agent_config, self.backend.name, checkpoint_path)
                self._print_progress_line(done_so_far, total_work, label)
                self._print_result(result)
        else:
            sem = asyncio.Semaphore(self.concurrency)
            progress_lock = asyncio.Lock()
            done_count: list[int] = [0]

            async def bounded(task: Any, run_idx: int) -> TaskResult:
                result: TaskResult
                label = f"{task.task_id}" + (f" [run {run_idx}/{n_runs}]" if n_runs > 1 else "")
                try:
                    async with sem:
                        cfg = {**agent_config, "run_id": f"{task.task_id}_r{run_idx}"}
                        if workspaces_dir is not None:
                            cfg["workspace_save_dir"] = str(workspaces_dir)
                        if docker_images_dir is not None:
                            cfg["docker_images_save_dir"] = str(docker_images_dir)
                        result = await self._run_with_retry(task, cfg)
                except BaseException as exc:
                    result = _error_result(task, str(exc), elapsed=0.0)
                async with progress_lock:
                    done_count[0] += 1
                    cur = done_count[0]
                    all_results[task.task_id].append(result)
                    flat_results.append(result)
                    _save_transcript(result, transcripts_dir, run_idx=run_idx if n_runs > 1 else None)
                    _write_checkpoint(flat_results, agent_config, self.backend.name, checkpoint_path)
                self._print_progress_line(cur, total_work, label)
                self._print_result(result)
                return result

            gathered = await asyncio.gather(
                *[bounded(task, run_idx) for task, run_idx in work_items],
                return_exceptions=True,
            )
            for (task, run_idx), outcome in zip(work_items, gathered):
                if isinstance(outcome, BaseException):
                    r = _error_result(task, str(outcome))
                    all_results[task.task_id].append(r)
                    flat_results.append(r)

        # Aggregate per-task stats when runs_per_task > 1
        task_stats: dict[str, dict[str, Any]] = {}
        if n_runs > 1:
            from .grader import pass_k_stats
            for t in tasks:
                runs = all_results[t.task_id]
                scores = [r.score for r in runs]
                task_stats[t.task_id] = {
                    "runs": n_runs,
                    "mean": statistics.mean(scores) if scores else 0.0,
                    "std": statistics.stdev(scores) if len(scores) > 1 else 0.0,
                    "min": min(scores) if scores else 0.0,
                    "max": max(scores) if scores else 0.0,
                    "scores": scores,
                }
            # pass@k — convert scores list to the format expected by pass_k_stats
            grades_for_pk = {
                tid: {"runs": [{"score": sc} for sc in s["scores"]]}
                for tid, s in task_stats.items()
            }
            pk = pass_k_stats(grades_for_pk, n_runs)
        else:
            pk = {}

        self._print_summary(flat_results, task_stats=task_stats, pass_k=pk)
        self._save_results(
            flat_results, agent_config, checkpoint_path, transcripts_dir,
            task_stats=task_stats, pass_k=pk,
        )
        return flat_results

    # ── internals ─────────────────────────────────────────────────────────────

    # Anomaly IDs that indicate an API quota/rate-limit problem.  When any of
    # these fire we always retry — even if the run finished with status=success,
    # because the agent's internal retry may have masked the 429 at the
    # transcript level while still producing a degraded result.
    _RATE_LIMIT_ANOMALY_IDS = frozenset({
        "API_RATE_LIMIT",
        "API_SERVER_ERROR",
        "TERMINAL_API_FAILURE",
    })
    # How long to wait before a rate-limit retry (seconds).  Each successive
    # attempt doubles the delay so we back off gracefully.
    _RATE_LIMIT_RETRY_BASE_SECONDS = 30

    async def _run_with_retry(self, task: Any, cfg: dict[str, Any]) -> TaskResult:
        """Execute a task with up to ``self.max_retries`` attempts.

        A retry is triggered when:
        * ``status == "error"`` or ``timed_out == True`` (execution failure), OR
        * ``anomaly.has_error`` is True (infrastructure failure detected after
          a nominally "successful" run), OR
        * any API rate-limit / quota anomaly fired (API_RATE_LIMIT,
          API_SERVER_ERROR, TERMINAL_API_FAILURE) — these are retried with an
          exponential back-off even when status=success, because the agent's
          internal retry mechanism can mask 429 errors at the transcript level
          while still producing a degraded / zero-score result.

        Passing results with no anomaly errors are returned immediately.
        """
        last: TaskResult | None = None
        for attempt in range(1, self.max_retries + 1):
            if attempt > 1:
                print(f"    ↻ retry {attempt}/{self.max_retries} for {task.task_id}")
            last = await self._run_one(task, cfg)
            execution_failed = last.status in ("error",) or last.timed_out
            anomaly_error = last.anomaly.get("has_error", False)
            triggered_ids = {i["id"] for i in last.anomaly.get("items", [])}
            rate_limit_hit = bool(triggered_ids & self._RATE_LIMIT_ANOMALY_IDS)
            if not execution_failed and not anomaly_error and not rate_limit_hit:
                return last
            if attempt < self.max_retries:
                if last.timed_out:
                    reason = "timed_out"
                elif execution_failed:
                    reason = f"status={last.status}"
                elif rate_limit_hit:
                    matched = triggered_ids & self._RATE_LIMIT_ANOMALY_IDS
                    reason = f"rate_limit=[{', '.join(sorted(matched))}]"
                else:
                    ids = [i["id"] for i in last.anomaly.get("items", []) if i.get("severity") == "error"]
                    reason = f"anomaly_errors=[{', '.join(ids[:3])}]"
                wait = 0.0
                if rate_limit_hit and not execution_failed:
                    wait = self._RATE_LIMIT_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                    print(f"    ! attempt {attempt} failed ({reason}), backing off {wait:.0f}s before retry…")
                    await asyncio.sleep(wait)
                else:
                    print(f"    ! attempt {attempt} failed ({reason}), will retry…")
        return last  # type: ignore[return-value]

    async def _run_one(self, task: Any, cfg: dict[str, Any]) -> TaskResult:
        t0 = time.time()
        try:
            return await asyncio.to_thread(self.backend.run_and_grade, task, cfg)
        except Exception as exc:
            print(f"    ERROR: {exc}")
            return _error_result(task, str(exc), elapsed=time.time() - t0)

    # ── output helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _print_progress_line(done: int, total: int, task_id: str, width: int = 36) -> None:
        """One-line ASCII progress bar + counter (for log / nohup friendly)."""
        if total <= 0:
            return
        done = min(done, total)
        filled = int(width * done / total)
        filled = min(filled, width)
        bar = "#" * filled + "-" * (width - filled)
        pct = 100.0 * done / total
        line = (
            f"  [native] [{bar}]  {done:>3}/{total}  {pct:5.1f}%  ·  {task_id}"
        )
        print(line, flush=True, file=sys.stdout)

    @staticmethod
    def _print_result(r: TaskResult) -> None:
        mark = "✓" if r.passed else "✗"
        pct = r.score / r.max_score * 100 if r.max_score > 0 else 0.0
        parts = [
            f"    {mark}",
            f"score={r.score:.2f}/{r.max_score:.2f} ({pct:.0f}%)",
            f"elapsed={r.execution_time:.1f}s",
            f"[{r.grading_type}]",
            f"status={r.status}",
        ]
        if r.usage.get("total_tokens"):
            parts.append(f"tokens={r.usage['total_tokens']:,}")
        if r.notes:
            parts.append(r.notes[:80])
        if r.error:
            parts.append(f"ERR:{r.error[:60]}")
        if r.anomaly.get("is_anomalous"):
            ids = [i["id"] for i in r.anomaly.get("items", [])]
            severity = "ERR" if r.anomaly.get("has_error") else "WARN"
            parts.append(f"[{severity}:{','.join(ids[:3])}]")
        print("  ".join(parts))

    def _print_summary(
        self,
        results: list[TaskResult],
        task_stats: dict[str, Any] | None = None,
        pass_k: dict[str, Any] | None = None,
    ) -> None:
        n = len(results)
        if n == 0:
            return
        passed = sum(1 for r in results if r.passed)
        avg = sum(r.score for r in results) / n
        total_time = sum(r.execution_time for r in results)
        bench = self.backend.name.upper()
        usage = _sum_usage(results)

        print(f"\n{'='*70}")
        print(f"  {bench} — {n} run(s)  passed={passed}/{n}  "
              f"avg_score={avg:.3f}  total_time={total_time:.1f}s")
        if usage["total_tokens"]:
            print(
                f"  tokens: prompt={usage['prompt_tokens']:,}  "
                f"completion={usage['completion_tokens']:,}  "
                f"total={usage['total_tokens']:,}"
            )
        if pass_k:
            for k, v in sorted(pass_k.items()):
                print(f"  {k}: {v:.4f}")
        print("="*70)
        if task_stats:
            for tid, s in sorted(task_stats.items()):
                print(
                    f"  {tid:<40}  mean={s['mean']:.3f}  "
                    f"std={s['std']:.3f}  min={s['min']:.3f}  max={s['max']:.3f}"
                )
        else:
            for r in sorted(results, key=lambda x: x.task_id):
                mark = "✓" if r.passed else "✗"
                pct = r.score / r.max_score * 100 if r.max_score > 0 else 0.0
                tok = f"  {r.usage['total_tokens']:>8,} tok" if r.usage.get("total_tokens") else ""
                print(f"  {mark}  {r.task_id:<35}  {pct:5.1f}%  {r.status}{tok}")
        print("="*70)

        _print_label_report(results)

    def _save_results(
        self,
        results: list[TaskResult],
        agent_config: dict[str, Any],
        out_path: Path | None = None,
        transcripts_dir: Path | None = None,
        task_stats: dict[str, Any] | None = None,
        pass_k: dict[str, Any] | None = None,
    ) -> None:
        bench = self.backend.name
        model = agent_config.get("model", "unknown")
        if out_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = self.results_dir / f"{ts}.json"
        if transcripts_dir is None:
            transcripts_dir = self.results_dir / "transcripts"
            transcripts_dir.mkdir(parents=True, exist_ok=True)

        _write_checkpoint(
            results, agent_config, bench, out_path,
            task_stats=task_stats, pass_k=pass_k,
        )
        print(f"\n  Results → {out_path}")

        # Save any transcripts not yet persisted by the per-task checkpoint path.
        newly_written = 0
        for r in results:
            if r.transcript:
                traj_path = transcripts_dir / f"{r.task_id}.jsonl"
                if not traj_path.exists():
                    _save_transcript(r, transcripts_dir)
                    newly_written += 1
        if newly_written:
            print(f"  Transcripts → {transcripts_dir}/ ({newly_written} new file(s))")
        existing = sum(
            1 for r in results
            if r.transcript and (transcripts_dir / f"{r.task_id}.jsonl").exists()
        )
        if existing:
            print(f"  Transcripts → {transcripts_dir}/ ({existing} file(s) total)")


# ── helpers ───────────────────────────────────────────────────────────────────

def _error_result(task: Any, error: str, elapsed: float = 0.0) -> TaskResult:
    labels = {}
    if hasattr(task, "frontmatter") and isinstance(task.frontmatter, dict):
        labels = {
            k: task.frontmatter[k]
            for k in ("scenario", "capabilities", "complexity", "modality", "environment")
            if task.frontmatter.get(k) is not None
        }
    return TaskResult(
        task_id=task.task_id,
        task_name=getattr(task, "task_name", getattr(task, "name", task.task_id)),
        score=0.0,
        max_score=1.0,
        passed=False,
        grading_type="error",
        breakdown={},
        notes="",
        execution_time=elapsed,
        status="error",
        usage={},
        transcript_length=0,
        timed_out=False,
        error=error,
        labels=labels,
    )


def _save_transcript(
    result: TaskResult,
    transcripts_dir: Path,
    run_idx: int | None = None,
) -> None:
    """Write a single task's transcript to ``transcripts_dir/{task_id}[_runN].jsonl``.

    When *run_idx* is provided (multi-run mode), the file is named
    ``{task_id}_run{run_idx}.jsonl`` so successive runs don't overwrite each
    other.  Called immediately after each task finishes.
    """
    if not result.transcript:
        return
    suffix = f"_run{run_idx}" if run_idx is not None else ""
    traj_path = transcripts_dir / f"{result.task_id}{suffix}.jsonl"
    with open(traj_path, "w", encoding="utf-8") as fh:
        for entry in result.transcript:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_checkpoint(
    results: list[TaskResult],
    agent_config: dict[str, Any],
    bench_name: str,
    out_path: Path,
    task_stats: dict[str, Any] | None = None,
    pass_k: dict[str, Any] | None = None,
) -> None:
    """Overwrite *out_path* with all results accumulated so far.

    When *task_stats* is provided (runs_per_task > 1), the checkpoint also
    includes per-task mean/std/min/max and pass@k statistics, matching the
    output schema of the original QwenClawBench ``summary.json``.
    """
    model = agent_config.get("model", "unknown")
    n_results = len(results)
    runs_per_task = n_results // len({r.task_id for r in results}) if results else 1
    avg_score = sum(r.score for r in results) / n_results if n_results else 0.0
    usage_total = _sum_usage(results)

    passed_count = sum(1 for r in results if r.passed)
    total_time = sum(r.execution_time for r in results)
    summary: dict[str, Any] = {
        "total_runs": n_results,
        "tasks_completed": len({r.task_id for r in results}),
        "passed": passed_count,
        "pass_rate": round(passed_count / n_results, 4) if n_results else 0.0,
        "avg_score": avg_score,
        "runs_per_task": runs_per_task,
        "total_time": round(total_time, 3),
        "avg_execution_time": round(total_time / n_results, 3) if n_results else 0.0,
        "total_usage": usage_total,
        "errors": {
            "total": sum(1 for r in results if r.status == "error"),
            "timed_out": sum(1 for r in results if r.timed_out),
            "failed": sum(1 for r in results if r.status == "error" and not r.timed_out),
        },
        "multi_agent": {
            "forced_violations": sum(
                1 for r in results
                if (r.multi_agent or {}).get("forced_violation")
            ),
            "delegations": sum(
                int((r.multi_agent or {}).get("delegation_count", 0))
                for r in results
            ),
        },
        "by_label": _build_label_summary(results),
    }
    if pass_k:
        summary.update(pass_k)

    payload: dict[str, Any] = {
        "benchmark": bench_name,
        "model": model,
        "run_config": {
            "agent_type": agent_config.get("agent_type", "harbor:qwenpaw"),
            "backend": bench_name,
            "multi_agent": agent_config.get(
                "multi_agent",
                {
                    "requested_mode": "native",
                    "effective_mode": "native",
                    "enabled": False,
                },
            ),
        },
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "results": [
            {
                "task_id": r.task_id,
                "task_name": r.task_name,
                "score": r.score,
                "max_score": r.max_score,
                "passed": r.passed,
                "grading_type": r.grading_type,
                "breakdown": r.breakdown,
                "notes": r.notes,
                "execution_time": r.execution_time,
                "status": r.status,
                "usage": r.usage,
                "transcript_length": r.transcript_length,
                "timed_out": r.timed_out,
                "error": r.error,
                "anomaly": r.anomaly,
                "labels": r.labels,
                "multi_agent": r.multi_agent,
            }
            for r in results
        ],
    }
    if task_stats:
        payload["task_stats"] = task_stats

    # Write atomically via a temp file to avoid a corrupt file if interrupted
    # mid-write.
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(out_path)


# ── label-dimension helpers ───────────────────────────────────────────────────

def _build_label_summary(results: list[TaskResult]) -> dict[str, Any]:
    """Aggregate pass-rate and avg-score for every label dimension value.

    Returns a dict keyed by dimension name (``capabilities``, ``modality_type``,
    ``modality_channel``, ``scenario``, ``complexity``, ``environment``).
    Each value is a dict keyed by the label value with ``total``, ``passed``,
    ``pass_rate``, and ``avg_score``.

    For list-valued dimensions (``capabilities``) every element is treated as
    an independent key so a task tagged with both ``Tool_Use`` and
    ``Code_Manipulation`` contributes to both buckets.
    """
    buckets: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"total": 0, "passed": 0, "score_sum": 0.0})
    )

    for r in results:
        labels = r.labels or {}

        for cap in labels.get("capabilities") or []:
            b = buckets["capabilities"][str(cap)]
            b["total"] += 1
            b["passed"] += int(r.passed)
            b["score_sum"] += r.score

        modality = labels.get("modality") or {}
        if modality:
            mod_type = str(modality.get("type", "unknown")) if isinstance(modality, dict) else str(modality)
            b = buckets["modality_type"][mod_type]
            b["total"] += 1
            b["passed"] += int(r.passed)
            b["score_sum"] += r.score
            if isinstance(modality, dict):
                for ch in modality.get("channels") or []:
                    b2 = buckets["modality_channel"][str(ch)]
                    b2["total"] += 1
                    b2["passed"] += int(r.passed)
                    b2["score_sum"] += r.score

        scenario = labels.get("scenario")
        if scenario:
            b = buckets["scenario"][str(scenario)]
            b["total"] += 1
            b["passed"] += int(r.passed)
            b["score_sum"] += r.score

        complexity = labels.get("complexity")
        if complexity:
            b = buckets["complexity"][str(complexity)]
            b["total"] += 1
            b["passed"] += int(r.passed)
            b["score_sum"] += r.score

        environment = labels.get("environment")
        if environment:
            b = buckets["environment"][str(environment)]
            b["total"] += 1
            b["passed"] += int(r.passed)
            b["score_sum"] += r.score

    out: dict[str, Any] = {}
    for dim, values in buckets.items():
        out[dim] = {}
        for label_val, data in sorted(values.items()):
            t = data["total"]
            out[dim][label_val] = {
                "total": t,
                "passed": data["passed"],
                "pass_rate": round(data["passed"] / t, 4) if t else 0.0,
                "avg_score": round(data["score_sum"] / t, 4) if t else 0.0,
            }
    return out


def _sum_usage(results: list[TaskResult]) -> dict[str, int]:
    """Sum token usage across all task results."""
    prompt = sum(r.usage.get("prompt_tokens", 0) for r in results)
    completion = sum(r.usage.get("completion_tokens", 0) for r in results)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _print_label_report(results: list[TaskResult]) -> None:
    """Print a human-readable breakdown of scores grouped by each label dimension."""
    summary = _build_label_summary(results)
    if not summary:
        return

    dim_titles = {
        "capabilities":     "Capabilities",
        "modality_type":    "Modality (type)",
        "modality_channel": "Modality (channel)",
        "scenario":         "Scenario",
        "complexity":       "Complexity",
        "environment":      "Environment",
    }

    print(f"\n{'─'*70}")
    print("  Label-Dimension Report")
    print(f"{'─'*70}")

    for dim_key in ("capabilities", "modality_type", "modality_channel",
                    "scenario", "complexity", "environment"):
        if dim_key not in summary:
            continue
        title = dim_titles.get(dim_key, dim_key)
        print(f"\n  [{title}]")
        col_w = max((len(k) for k in summary[dim_key]), default=10)
        col_w = max(col_w, 10)
        header = (
            f"  {'Label':<{col_w}}  {'Total':>6}  {'Passed':>6}  "
            f"{'Pass%':>7}  {'AvgScore':>9}"
        )
        print(header)
        print("  " + "-" * (col_w + 35))
        for label_val, data in summary[dim_key].items():
            pass_pct = data["pass_rate"] * 100
            print(
                f"  {label_val:<{col_w}}  {data['total']:>6}  {data['passed']:>6}  "
                f"{pass_pct:>6.1f}%  {data['avg_score']:>9.4f}"
            )

    print(f"{'─'*70}\n")
