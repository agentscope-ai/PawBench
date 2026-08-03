from __future__ import annotations

import json
from pathlib import Path

from pawbench.backend import TaskResult
from pawbench.runner import (
    _save_task_bundle,
    _update_combined_report,
)


def _make_result(task_id: str, trial_dir: Path | None = None, **overrides) -> TaskResult:
    defaults: dict = dict(
        task_id=task_id,
        task_name=task_id,
        score=1.0,
        max_score=1.0,
        passed=True,
        grading_type="auto",
        breakdown={},
        notes="",
        execution_time=0.1,
        status="success",
        usage={},
        transcript_length=0,
        timed_out=False,
        error="",
        transcript=[],
        trial_dir=str(trial_dir) if trial_dir else "",
    )
    defaults.update(overrides)
    return TaskResult(**defaults)


def test_save_task_bundle_copies_raw_trajectory_reward_and_workspace(tmp_path: Path) -> None:
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "trajectory.json").write_text('{"steps": []}', encoding="utf-8")

    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir(parents=True)
    (verifier_dir / "reward.json").write_text('{"reward": 1.0}', encoding="utf-8")

    workspace_dir = trial_dir / "artifacts" / "workspace"
    (workspace_dir / "reports").mkdir(parents=True)
    (workspace_dir / "reports" / "audit.md").write_text("done\n", encoding="utf-8")

    summary_dir = tmp_path / "summary"
    summary_dir.mkdir()

    result = _make_result("task-001", trial_dir=trial_dir)
    _save_task_bundle(result, summary_dir)

    assert json.loads((summary_dir / "trajectory.json").read_text()) == {"steps": []}
    assert json.loads((summary_dir / "reward" / "reward.json").read_text()) == {"reward": 1.0}
    assert (summary_dir / "workspace" / "reports" / "audit.md").read_text(
        encoding="utf-8"
    ) == "done\n"


def test_save_task_bundle_falls_back_to_qwenpaw_session(tmp_path: Path) -> None:
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "qwenpaw.session.json").write_text('{"session": true}', encoding="utf-8")

    summary_dir = tmp_path / "summary"
    summary_dir.mkdir()

    result = _make_result("task-002", trial_dir=trial_dir)
    _save_task_bundle(result, summary_dir)

    assert json.loads((summary_dir / "trajectory.json").read_text()) == {"session": True}
    assert not (summary_dir / "reward").exists()
    assert not (summary_dir / "workspace").exists()


def test_save_task_bundle_falls_back_to_transcript_jsonl_without_trial_dir(tmp_path: Path) -> None:
    summary_dir = tmp_path / "summary"
    summary_dir.mkdir()
    result = _make_result("task-003", transcript=[{"role": "user", "content": "hi"}])

    _save_task_bundle(result, summary_dir)

    lines = (summary_dir / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(l) for l in lines] == [{"role": "user", "content": "hi"}]


def test_save_task_bundle_multi_run_suffix(tmp_path: Path) -> None:
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "trajectory.json").write_text("{}", encoding="utf-8")

    summary_dir = tmp_path / "summary"
    summary_dir.mkdir()
    result = _make_result("task-004", trial_dir=trial_dir)

    _save_task_bundle(result, summary_dir, run_idx=2)

    assert (summary_dir / "trajectory_run2.json").exists()


def test_update_combined_report_merges_across_agents(tmp_path: Path) -> None:
    combined_path = tmp_path / "run_ts" / "run_ts_combined_report.json"
    result_a = _make_result("taskA", score=1.0, passed=True)
    result_b = _make_result("taskB", score=0.0, passed=False)

    _update_combined_report(
        combined_path, [result_a], {"model": "m1", "agent_type": "harbor:agentX"},
        "m1", "harbor:agentX",
    )
    _update_combined_report(
        combined_path, [result_b], {"model": "m2", "agent_type": "harbor:agentY"},
        "m2", "harbor:agentY",
    )

    combined = json.loads(combined_path.read_text(encoding="utf-8"))
    assert {a["agent_type"] for a in combined["agents"]} == {"harbor:agentX", "harbor:agentY"}
    assert combined["score_matrix"] == {"taskA": {"harbor:agentX": 1.0}, "taskB": {"harbor:agentY": 0.0}}
    assert combined["overall"]["total_agents"] == 2
    assert combined["overall"]["total_results"] == 2


def test_update_combined_report_overwrites_same_agent_results(tmp_path: Path) -> None:
    combined_path = tmp_path / "run_ts" / "run_ts_combined_report.json"
    result1 = _make_result("taskA", score=0.0, passed=False)

    _update_combined_report(
        combined_path, [result1], {"model": "m1", "agent_type": "harbor:agentX"},
        "m1", "harbor:agentX",
    )
    # Same agent finishes a second task; the merge must not duplicate its
    # earlier (now superseded) entry.
    result2 = _make_result("taskB", score=1.0, passed=True)
    _update_combined_report(
        combined_path, [result1, result2], {"model": "m1", "agent_type": "harbor:agentX"},
        "m1", "harbor:agentX",
    )

    combined = json.loads(combined_path.read_text(encoding="utf-8"))
    assert len(combined["agents"]) == 1
    assert len(combined["results"]) == 2
