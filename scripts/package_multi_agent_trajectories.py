#!/usr/bin/env python3
"""Package multi-agent PawBench trajectories into a downloadable zip."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from pawbench.harbor_v2.verifier.input_contract import validate_atif

PACKAGE_ROOT = "pawbenchv2_0706_three_modes_trajectories"
EXPECTED_COUNTS = {"single": 10, "adaptive": 3, "forced": 3}
RAW_LOG_CANDIDATES = (
    "{agent}.txt",
    "claude-code.txt",
    "openclaw.txt",
    "hermes.txt",
    "codex.txt",
    "qwenpaw.txt",
    "mini-swe-agent.txt",
    "aider.txt",
)
TRAJECTORY_CANDIDATES = (
    "trajectory.json",
    "{agent}.trajectory.json",
)


def _harbor_parent(agent: str) -> str:
    return f"harbor:{agent}"


def _collect_mode(results_root: Path, mode: str, agent: str) -> tuple[dict, dict[str, Path]]:
    agent_root = results_root / mode / agent
    if not agent_root.exists():
        raise FileNotFoundError(f"No results for agent={agent} mode={mode}")
    candidates = [
        path
        for path in agent_root.rglob("*.json")
        if "trials" not in path.parts and path.parent.name == _harbor_parent(agent)
    ]
    if not candidates:
        raise FileNotFoundError(f"No run summary found for agent={agent} mode={mode}")

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
        raise FileNotFoundError(f"No trial directory found for {task_id} under {trials_dir}")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def _pick(agent_dir: Path, patterns: tuple[str, ...], agent: str) -> Path | None:
    for pattern in patterns:
        path = agent_dir / pattern.format(agent=agent)
        if path.exists() and path.is_file():
            return path
    return None


def _copy_trial(
    trial: Path,
    target: Path,
    agent: str,
    multi_agent: dict,
) -> dict | None:
    target.mkdir(parents=True, exist_ok=True)
    agent_dir = trial / "agent"
    result = trial / "result.json"
    if not result.exists():
        raise FileNotFoundError(f"Missing result.json: {result}")
    shutil.copy2(result, target / "result.json")

    traj = _pick(agent_dir, TRAJECTORY_CANDIDATES, agent)
    if traj is None:
        raise FileNotFoundError(f"No standard ATIF trajectory artifact under {agent_dir}")
    try:
        validate_atif(json.loads(traj.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid ATIF trajectory {traj}: {exc}") from exc
    shutil.copy2(traj, target / "trajectory.json")

    raw = _pick(agent_dir, RAW_LOG_CANDIDATES, agent)
    if raw is None:
        raise FileNotFoundError(f"No raw log under {agent_dir}")
    shutil.copy2(raw, target / "raw_log.txt")

    (target / "multi_agent.json").write_text(
        json.dumps(multi_agent, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if (trial / "verifier").exists():
        shutil.copytree(trial / "verifier", target / "verifier")
    user_state = agent_dir / "user_sim_state.json"
    if user_state.exists():
        shutil.copy2(user_state, target / "user_sim_state.json")
    system_prompt = agent_dir / "system_prompt.txt"
    if system_prompt.exists():
        shutil.copy2(system_prompt, target / "system_prompt.txt")
    workspace = trial / "artifacts" / "workspace"
    if workspace.is_dir():
        shutil.copytree(workspace, target / "workspace", symlinks=True)
    provenance = trial / "provenance.json"
    if provenance.is_file():
        shutil.copy2(provenance, target / "provenance.json")
        payload = json.loads(provenance.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    return None


def _evaluation_contract(provenance: dict) -> dict:
    """Fields that must agree when results are presented as one benchmark run."""
    agent = provenance.get("agent") or {}
    return {
        "schema_version": provenance.get("schema_version"),
        "dataset": provenance.get("dataset"),
        "target_model": agent.get("model"),
        "judge": provenance.get("judge"),
        "multi_agent_mode": provenance.get("multi_agent_mode"),
    }


def _write_summary(
    root: Path,
    packaged: dict[str, dict[str, dict]],
    sources: dict[str, str],
) -> None:
    lines = [
        "# Pawbenchv2_task_0706 多 Agent 三模式评测轨迹",
        "",
        "- **数据集**: `data/Pawbenchv2_task_0706`（single 全 10 任务；adaptive/forced 各 3 个 ma-* 任务）",
        "- **被测模型**: `qwen3.6-plus`",
        "- **Judge**: `qwen3.7-max`",
        "- **模式**: `single` / `adaptive` / `forced`",
        "",
        "## 数据来源",
        "",
    ]
    for agent, source in sources.items():
        lines.append(f"- `{agent}`: `{source}`")
    lines.extend(
        [
            "",
            "## Agent × Mode 汇总",
            "",
            "| Agent | Mode | 任务数 | 通过 | 平均分 | 委派调用合计 | Errors |",
            "|-------|------|------:|-----:|-------:|-------------:|-------:|",
        ]
    )
    for agent in sorted(packaged):
        for mode in ("single", "adaptive", "forced"):
            if mode not in packaged[agent]:
                continue
            summary = packaged[agent][mode]["summary"]
            ma = summary.get("multi_agent", {})
            errors = summary.get("errors", {}).get("total", 0)
            lines.append(
                f"| {agent} | {mode} | {summary.get('total_runs', 0)} | "
                f"{summary.get('passed', 0)} | {summary.get('avg_score', 0):.3f} | "
                f"{ma.get('delegations', 0)} | {errors} |"
            )

    lines.extend(
        [
            "",
            "## 逐任务分数",
            "",
            "| Agent | Mode | 任务 | 分数 | 状态 |",
            "|-------|------|------|-----:|------|",
        ]
    )
    for agent in sorted(packaged):
        for mode in ("single", "adaptive", "forced"):
            if mode not in packaged[agent]:
                continue
            for result in packaged[agent][mode]["results"]:
                lines.append(
                    f"| {agent} | {mode} | {result['task_id']} | "
                    f"{result.get('score', 0):.3f} | {result.get('status', '')} |"
                )
    lines.extend(
        [
            "",
            "每个任务目录包含 `trajectory.json`、`raw_log.txt`、`result.json`、"
            "`multi_agent.json`、`verifier/`；多轮任务还包含 `user_sim_state.json`。",
            "使用新版保存选项运行的任务还包含最终 `workspace/`；Claude Code "
            "任务另含实际传入的追加提示词 `system_prompt.txt`。",
            "",
            "说明：所有 `trajectory.json` 均经过 ATIF 契约校验；每个新评测任务还包含 "
            "`provenance.json`，包根目录的 `EVALUATION_CONTRACTS.json` 记录并校验"
            "数据集、目标模型、Judge 与评测模式是否一致。",
            "",
        ]
    )
    (root / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


def package(
    agent_roots: dict[str, Path],
    output: Path,
    *,
    allow_incomplete: bool = True,
    allow_missing_provenance: bool = False,
    allow_mixed_contracts: bool = False,
) -> None:
    packaged: dict[str, dict[str, dict]] = {}
    sources = {agent: str(root) for agent, root in agent_roots.items()}

    with tempfile.TemporaryDirectory(prefix="multi-agent-trajectories-") as temp:
        package_root = Path(temp) / PACKAGE_ROOT
        package_root.mkdir(parents=True, exist_ok=True)
        contracts: dict[str, dict] = {}
        missing_provenance: list[str] = []

        for agent, results_root in agent_roots.items():
            packaged[agent] = {}
            for mode, expected in EXPECTED_COUNTS.items():
                try:
                    data, source_paths = _collect_mode(results_root, mode, agent)
                except FileNotFoundError as exc:
                    if allow_incomplete:
                        print(f"skip {agent}/{mode}: {exc}")
                        continue
                    raise
                results = data.get("results", [])
                if len(results) != expected and not allow_incomplete:
                    raise RuntimeError(
                        f"agent={agent} mode={mode}: expected {expected}, got {len(results)}"
                    )
                if len(results) != expected:
                    print(f"warn {agent}/{mode}: expected {expected} results, got {len(results)}")
                packaged[agent][mode] = data
                mode_root = package_root / mode
                mode_root.mkdir(parents=True, exist_ok=True)
                agent_mode_root = mode_root / agent
                agent_mode_root.mkdir(parents=True, exist_ok=True)
                (agent_mode_root / "run_summary.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                for result in results:
                    task_id = result["task_id"]
                    trial = _find_trial(source_paths[task_id], task_id)
                    provenance = _copy_trial(
                        trial,
                        agent_mode_root / task_id,
                        agent,
                        result.get("multi_agent", {}),
                    )
                    if provenance is None:
                        missing_provenance.append(f"{agent}/{mode}/{task_id}")
                    else:
                        contract = _evaluation_contract(provenance)
                        fingerprint = json.dumps(
                            contract,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        contracts[fingerprint] = contract

            # per-mode top-level summary is written after all agents for that mode
        for mode in EXPECTED_COUNTS:
            mode_root = package_root / mode
            if not mode_root.exists():
                continue
            mode_rollup = {
                agent: packaged[agent][mode] for agent in packaged if mode in packaged[agent]
            }
            (mode_root / "run_summary.json").write_text(
                json.dumps(
                    {
                        agent: {
                            "summary": data["summary"],
                            "results": [
                                {
                                    "task_id": item["task_id"],
                                    "score": item.get("score"),
                                    "passed": item.get("passed"),
                                    "status": item.get("status"),
                                }
                                for item in data["results"]
                            ],
                        }
                        for agent, data in mode_rollup.items()
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        if missing_provenance and not allow_missing_provenance:
            sample = ", ".join(missing_provenance[:5])
            raise RuntimeError(
                f"{len(missing_provenance)} trials lack provenance.json "
                f"(examples: {sample}); pass --allow-missing-provenance only for legacy runs"
            )
        if len(contracts) > 1 and not allow_mixed_contracts:
            raise RuntimeError(
                "Refusing to mix incompatible evaluation contracts: "
                f"{list(contracts.values())}; pass --allow-mixed-contracts to override"
            )
        (package_root / "EVALUATION_CONTRACTS.json").write_text(
            json.dumps(
                {
                    "contracts": list(contracts.values()),
                    "missing_provenance": missing_provenance,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        _write_summary(package_root, packaged, sources)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_zip = output.with_suffix(output.suffix + ".tmp")
        temporary_zip.unlink(missing_ok=True)
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(package_root.parent))
        os.replace(temporary_zip, output)

    print(f"Packed trajectory package: {output.resolve()}")
    for agent in sorted(packaged):
        modes = ", ".join(
            f"{mode}({packaged[agent][mode]['summary']['total_runs']})"
            for mode in ("single", "adaptive", "forced")
            if mode in packaged[agent]
        )
        print(f"  {agent}: {modes}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allagents-root",
        type=Path,
        default=Path("results/allagents-3modes-20260721_205226"),
    )
    parser.add_argument(
        "--claude-root",
        type=Path,
        default=Path("results/claude-code-rerun-all-20260722_115825"),
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        default=[
            "openclaw",
            "mini-swe-agent",
            "hermes",
            "codex",
            "qwenpaw",
            "claude-code",
        ],
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("pawbenchv2_0706_three_modes_trajectories_multi_agents.zip"),
    )
    parser.add_argument(
        "--allow-missing-provenance",
        action="store_true",
        help="Permit legacy trials without provenance.json",
    )
    parser.add_argument(
        "--allow-mixed-contracts",
        action="store_true",
        help="Permit datasets/judges/models from incompatible evaluation contracts",
    )
    args = parser.parse_args()

    agent_roots: dict[str, Path] = {}
    for agent in args.agents:
        if agent == "claude-code":
            agent_roots[agent] = args.claude_root.resolve()
        else:
            agent_roots[agent] = args.allagents_root.resolve()
    package(
        agent_roots,
        args.output.resolve(),
        allow_missing_provenance=args.allow_missing_provenance,
        allow_mixed_contracts=args.allow_mixed_contracts,
    )


if __name__ == "__main__":
    main()
