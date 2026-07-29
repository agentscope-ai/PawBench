#!/usr/bin/env python3
"""Package completed Claude Code benchmark runs and atomically replace the zip."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

PACKAGE_ROOT = "pawbenchv2_0706_three_modes_trajectories"
EXPECTED_COUNTS = {"single": 10, "adaptive": 3, "forced": 3}


def _collect_mode(results_root: Path, mode: str) -> tuple[dict, dict[str, Path]]:
    candidates = [
        path
        for path in (results_root / mode / "claude-code").rglob("*.json")
        if "trials" not in path.parts and path.parent.name == "harbor:claude-code"
    ]
    if not candidates:
        raise FileNotFoundError(f"No run summary found for mode={mode}")
    latest_by_task: dict[str, tuple[dict, Path]] = {}
    latest_data: dict = {}
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime):
        data = json.loads(path.read_text(encoding="utf-8"))
        latest_data = data
        for result in data.get("results", []):
            latest_by_task[result["task_id"]] = (result, path)

    results = [latest_by_task[task_id][0] for task_id in sorted(latest_by_task)]
    merged = copy.deepcopy(latest_data)
    merged["results"] = results
    total = len(results)
    passed = sum(bool(result.get("passed")) for result in results)
    total_time = sum(float(result.get("execution_time", 0)) for result in results)
    usage = {
        key: sum(int(result.get("usage", {}).get(key, 0)) for result in results)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    errors = sum(result.get("status") == "error" for result in results)
    timed_out = sum(bool(result.get("timed_out")) for result in results)
    merged["summary"] = {
        "total_runs": total,
        "tasks_completed": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "avg_score": (
            sum(float(result.get("score", 0)) for result in results) / total if total else 0.0
        ),
        "runs_per_task": 1,
        "total_time": round(total_time, 3),
        "avg_execution_time": round(total_time / total, 3) if total else 0.0,
        "total_usage": usage,
        "errors": {
            "total": errors,
            "timed_out": timed_out,
            "failed": errors,
        },
        "multi_agent": {
            "forced_violations": sum(
                bool(result.get("multi_agent", {}).get("forced_violation")) for result in results
            ),
            "delegations": sum(
                int(result.get("multi_agent", {}).get("delegation_count", 0)) for result in results
            ),
        },
    }
    source_paths = {task_id: summary_path for task_id, (_, summary_path) in latest_by_task.items()}
    return merged, source_paths


def _find_trial(summary_path: Path, task_id: str) -> Path:
    trials_dir = summary_path.parent / "trials"
    candidates = [path for path in trials_dir.glob(f"{task_id}__*") if path.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No trial directory found for {task_id}")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def _copy_trial(trial: Path, target: Path, multi_agent: dict) -> None:
    target.mkdir(parents=True, exist_ok=True)
    required = {
        trial / "agent" / "trajectory.json": target / "trajectory.json",
        trial / "agent" / "claude-code.txt": target / "raw_log.txt",
        trial / "result.json": target / "result.json",
    }
    for source, destination in required.items():
        if not source.exists():
            raise FileNotFoundError(f"Required trajectory artifact missing: {source}")
        shutil.copy2(source, destination)

    (target / "multi_agent.json").write_text(
        json.dumps(multi_agent, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if (trial / "verifier").exists():
        shutil.copytree(trial / "verifier", target / "verifier")
    user_state = trial / "agent" / "user_sim_state.json"
    if user_state.exists():
        shutil.copy2(user_state, target / "user_sim_state.json")
    system_prompt = trial / "agent" / "system_prompt.txt"
    if system_prompt.exists():
        shutil.copy2(system_prompt, target / "system_prompt.txt")
    workspace = trial / "artifacts" / "workspace"
    if workspace.is_dir():
        shutil.copytree(workspace, target / "workspace", symlinks=True)


def _write_summary(root: Path, runs: dict[str, dict]) -> None:
    lines = [
        "# Pawbenchv2_task_0706 Claude Code 三模式评测轨迹",
        "",
        "- **数据集**: `data/Pawbenchv2_task_0706`（single 全 10 任务；adaptive/forced 各 3 个 ma-* 任务）",
        "- **被测模型**: `qwen3.6-plus`",
        "- **Judge**: `qwen3.7-max`",
        "- **Agent**: `harbor:claude-code`",
        "- **模式**: `single` / `adaptive` / `forced`",
        "",
        "## 模式汇总",
        "",
        "| Mode | 任务数 | 通过 | 平均分 | 委派调用合计 |",
        "|------|------:|-----:|-------:|-------------:|",
    ]
    for mode in ("single", "adaptive", "forced"):
        summary = runs[mode]["summary"]
        ma = summary.get("multi_agent", {})
        lines.append(
            f"| {mode} | {summary.get('total_runs', 0)} | "
            f"{summary.get('passed', 0)} | {summary.get('avg_score', 0):.3f} | "
            f"{ma.get('delegations', 0)} |"
        )

    lines.extend(
        [
            "",
            "## 逐任务分数",
            "",
            "| Mode | 任务 | 分数 | 状态 |",
            "|------|------|-----:|------|",
        ]
    )
    for mode in ("single", "adaptive", "forced"):
        for result in runs[mode]["results"]:
            lines.append(
                f"| {mode} | {result['task_id']} | {result.get('score', 0):.3f} | "
                f"{result.get('status', '')} |"
            )
    lines.extend(
        [
            "",
            "每个任务目录包含 `trajectory.json`、`raw_log.txt`、`result.json`、"
            "`multi_agent.json`、`verifier/`；多轮任务还包含 `user_sim_state.json`。",
            "使用新版保存选项运行的任务还包含最终 `workspace/` 和 Claude Code "
            "实际传入的追加提示词 `system_prompt.txt`。",
            "",
        ]
    )
    (root / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


def package(results_root: Path, output: Path) -> None:
    runs: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="claude-trajectories-") as temp:
        package_root = Path(temp) / PACKAGE_ROOT
        for mode, expected in EXPECTED_COUNTS.items():
            data, source_paths = _collect_mode(results_root, mode)
            results = data.get("results", [])
            if len(results) != expected:
                raise RuntimeError(f"mode={mode}: expected {expected} results, got {len(results)}")
            error_count = data.get("summary", {}).get("errors", {}).get("total", 0)
            if error_count:
                raise RuntimeError(
                    f"mode={mode}: refusing to replace package with "
                    f"{error_count} infrastructure/error result(s)"
                )
            runs[mode] = data
            mode_root = package_root / mode
            mode_root.mkdir(parents=True, exist_ok=True)
            (mode_root / "run_summary.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for result in results:
                task_id = result["task_id"]
                trial = _find_trial(source_paths[task_id], task_id)
                _copy_trial(
                    trial,
                    mode_root / "claude-code" / task_id,
                    result.get("multi_agent", {}),
                )

        _write_summary(package_root, runs)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_zip = output.with_suffix(output.suffix + ".tmp")
        temporary_zip.unlink(missing_ok=True)
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(package_root.parent))
        os.replace(temporary_zip, output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.results_root.resolve(), args.output.resolve())
    print(f"Replaced trajectory package: {args.output.resolve()}")


if __name__ == "__main__":
    main()
