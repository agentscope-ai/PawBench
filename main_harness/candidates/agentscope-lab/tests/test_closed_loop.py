from __future__ import annotations

import json
from pathlib import Path

import pytest

from pawbench_agentscope import closed_loop
from pawbench_agentscope.closed_loop import (
    RunObservation,
    build_ablation_plan,
    compare_feature_off,
    execute_task_plan,
    load_reasoning_outcome,
    observation_from_harbor,
    write_closed_loop_run,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _observation(
    task_id: str,
    variant: str,
    *,
    passed: bool,
    score: float,
    disabled: list[str] | None = None,
) -> RunObservation:
    return RunObservation(
        task_id=task_id,
        variant=variant,
        disabled_features=disabled or [],
        passed=passed,
        accepted=passed,
        verifier_ok=passed,
        score=score,
        event_counts={"workspace_binding": 1} if passed else {},
        artifact_hashes={"answer.txt": "good" if passed else "bad"},
        artifact_sizes={"answer.txt": 4 if passed else 3},
    )


def test_load_reasoning_outcome_prefers_agentic_verdict_and_blocks_ex_m(tmp_path: Path) -> None:
    reasoning = tmp_path / "reasoning"
    source = tmp_path / "source"
    task_id = "ws-demo-001"
    _write_json(
        reasoning / "recordings" / f"{task_id}.json",
        {
            "task_id": task_id,
            "task_family": "ws",
            "passed": False,
            "score": 0.0,
            "accepted": True,
            "result": {"attribution_status": "coded_failure", "codes": ["H1"]},
        },
    )
    _write_json(
        reasoning / "agentic-audit" / "verdicts" / f"{task_id}.json",
        {
            "task_id": task_id,
            "decision": "coded_failure",
            "codes": [
                {"code": "Ex-1", "reason": "hidden requirement"},
                {"code": "M2", "reason": "visible path violation"},
            ],
            "codex_audit": {"action": "revise"},
        },
    )

    outcome = load_reasoning_outcome(reasoning, source, task_id)
    plan = build_ablation_plan(outcome)

    assert outcome["reasoning_source"] == "agentic_final_verdict"
    assert outcome["codes"] == ["Ex-1", "M2"]
    assert outcome["route"] == "non_h_failure_no_ablation"
    assert plan["experiments"] == []
    assert plan["blocked_non_h_codes"] == ["Ex-1", "M2"]


def test_load_reasoning_outcome_reuses_existing_pass_validation(tmp_path: Path) -> None:
    reasoning = tmp_path / "reasoning"
    source = tmp_path / "source"
    task_id = "ua-demo-001"
    _write_json(
        reasoning / "recordings" / f"{task_id}.json",
        {
            "task_id": task_id,
            "task_family": "ua",
            "passed": True,
            "score": 1.0,
            "accepted": True,
            "result": {"attribution_status": "no_attributable_failure", "codes": []},
        },
    )
    _write_json(
        reasoning / "agentic-audit" / "pass-validations" / f"{task_id}.json",
        {
            "task_id": task_id,
            "status": "validated_pass",
            "checks": [
                {"code": "Ex-1", "status": "clear"},
                {"code": "Ex-2", "status": "clear"},
                {"code": "Ex-3", "status": "clear"},
            ],
            "audit": {"status": "ok"},
        },
    )

    outcome = load_reasoning_outcome(reasoning, source, task_id)
    assert outcome["route"] == "pass_validated_no_ablation"
    assert outcome["pass_validation"]["status"] == "validated_pass"
    assert build_ablation_plan(outcome)["experiments"] == []


@pytest.mark.parametrize(
    "mutation",
    ["wrong_task", "missing_check", "duplicate_check", "flagged_check", "bad_audit"],
)
def test_load_reasoning_outcome_fails_closed_on_untrusted_pass_receipt(
    tmp_path: Path,
    mutation: str,
) -> None:
    reasoning = tmp_path / "reasoning"
    source = tmp_path / "source"
    task_id = "ua-pass-receipt-001"
    _write_json(
        reasoning / "recordings" / f"{task_id}.json",
        {
            "task_id": task_id,
            "task_family": "ua",
            "passed": True,
            "score": 1.0,
            "accepted": True,
            "result": {"attribution_status": "no_attributable_failure", "codes": []},
        },
    )
    receipt = {
        "task_id": task_id,
        "status": "validated_pass",
        "checks": [
            {"code": "Ex-1", "status": "clear"},
            {"code": "Ex-2", "status": "clear"},
            {"code": "Ex-3", "status": "clear"},
        ],
        "audit": {"status": "ok"},
    }
    if mutation == "wrong_task":
        receipt["task_id"] = "ua-other-task"
    elif mutation == "missing_check":
        receipt["checks"].pop()
    elif mutation == "duplicate_check":
        receipt["checks"][-1] = {"code": "Ex-2", "status": "clear"}
    elif mutation == "flagged_check":
        receipt["checks"][0]["status"] = "review"
    elif mutation == "bad_audit":
        receipt["audit"]["status"] = "needs_review"
    _write_json(
        reasoning / "agentic-audit" / "pass-validations" / f"{task_id}.json",
        receipt,
    )

    outcome = load_reasoning_outcome(reasoning, source, task_id)

    assert outcome["route"] == "pass_requires_review_no_ablation"
    assert build_ablation_plan(outcome)["experiments"] == []


def test_load_reasoning_outcome_rejects_nonfinite_score(tmp_path: Path) -> None:
    reasoning = tmp_path / "reasoning"
    source = tmp_path / "source"
    task_id = "ua-nonfinite-score"
    _write_json(
        reasoning / "recordings" / f"{task_id}.json",
        {
            "task_id": task_id,
            "task_family": "ua",
            "passed": False,
            "score": float("nan"),
            "accepted": True,
            "result": {"attribution_status": "coded_failure", "codes": ["H1"]},
        },
    )

    try:
        load_reasoning_outcome(reasoning, source, task_id)
    except ValueError as exc:
        assert "non-finite" in str(exc)
    else:
        raise AssertionError("non-finite reasoning score should be rejected")


def test_reasoning_loader_refuses_symlink_recording(tmp_path: Path) -> None:
    reasoning = tmp_path / "reasoning"
    source = tmp_path / "source"
    task_id = "ua-symlink-recording"
    external = tmp_path / "external.json"
    _write_json(
        external,
        {
            "task_id": task_id,
            "passed": False,
            "score": 0,
            "accepted": True,
            "result": {"codes": ["H1"]},
        },
    )
    recording = reasoning / "recordings" / f"{task_id}.json"
    recording.parent.mkdir(parents=True)
    recording.symlink_to(external)

    with pytest.raises(ValueError, match="must not be a symlink"):
        load_reasoning_outcome(reasoning, source, task_id)


def test_reasoning_loader_bounds_json_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "recording.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(closed_loop, "MAX_REASONING_JSON_BYTES", 1)

    with pytest.raises(ValueError, match="JSON input exceeds"):
        closed_loop._read_json(path)


def test_reasoning_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "recording.json"
    path.write_text('{"task_id":"one","task_id":"two"}', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON key"):
        closed_loop._read_json(path)


def test_harbor_observation_skips_external_workspace_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret outside", encoding="utf-8")
    (workspace / "outside-link.txt").symlink_to(outside)

    observation = observation_from_harbor(
        {
            "task_id": "safe-observation",
            "accepted": True,
            "verifier": {"ok": True},
            "files": {},
        },
        variant="all_features_on",
        workspace_root=workspace,
    )

    assert set(observation.artifact_hashes) == {"inside.txt"}
    assert set(observation.artifact_sizes) == {"inside.txt"}


def test_harbor_observation_refuses_symlink_trace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "trace.jsonl"
    external.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "trace-link.jsonl"
    link.symlink_to(external)

    with pytest.raises(ValueError, match="must not be a symlink"):
        observation_from_harbor(
            {
                "task_id": "safe-observation",
                "accepted": True,
                "verifier": {"ok": True},
                "files": {"trace": str(link)},
            },
            variant="all_features_on",
            workspace_root=workspace,
        )


def test_h_evidence_maps_to_one_feature_and_ex_m_do_not_add_experiments() -> None:
    outcome = {
        "task_id": "ws-demo-002",
        "route": "h_feature_ablation",
        "score": 0.0,
        "decision": "coded_failure",
        "reasoning_source": "agentic_final_verdict",
        "codes": ["Ex-3", "H1", "M3"],
        "h_codes": ["H1"],
        "code_entries": [
            {
                "code": "H1",
                "reason": "The workspace binding and artifact path map used the wrong root.",
                "evidence": [{"section": "environment_setting", "source_path": "/cwd"}],
            }
        ],
    }

    plan = build_ablation_plan(outcome)

    assert plan["route"] == "single_feature_off"
    assert plan["recommended_feature_ids"] == ["F1.1"]
    assert plan["blocked_non_h_codes"] == ["Ex-3", "M3"]
    assert plan["experiments"][0]["all_other_features"] == "ON"


def test_compare_feature_off_supports_reproduced_failure() -> None:
    task_id = "ma-demo-001"
    observed = _observation(task_id, "observed", passed=False, score=0.0, disabled=["F5.1"])
    baseline = _observation(task_id, "all_features_on", passed=True, score=1.0)
    feature_off = _observation(
        task_id,
        "without_F5_1",
        passed=False,
        score=0.0,
        disabled=["F5.1"],
    )

    comparison = compare_feature_off(observed, baseline, feature_off, "F5.1")

    assert comparison.status == "supported"
    assert comparison.score_drop == 1.0
    assert comparison.reproduced_observed_failure is True
    assert comparison.changed_artifacts == ["answer.txt"]


def test_compare_verification_off_supports_reproduced_false_acceptance() -> None:
    task_id = "ws-verification-001"
    observed = RunObservation(
        task_id=task_id,
        variant="observed",
        disabled_features=["F4.3"],
        passed=False,
        accepted=True,
        verifier_ok=False,
        score=0.0,
    )
    baseline = RunObservation(
        task_id=task_id,
        variant="all_features_on",
        disabled_features=[],
        passed=False,
        accepted=False,
        verifier_ok=False,
        score=0.0,
    )
    feature_off = observed.model_copy(update={"variant": "without_F4_3"})

    comparison = compare_feature_off(observed, baseline, feature_off, "F4.3")

    assert comparison.status == "supported"
    assert comparison.introduced_false_acceptance is True
    assert comparison.reproduced_observed_failure is True
    assert "false acceptance" in comparison.rationale


def test_non_h_plan_never_calls_experiment_executor() -> None:
    task_id = "ua-demo-002"
    outcome = {
        "task_id": task_id,
        "route": "non_h_failure_no_ablation",
        "codes": ["M1"],
        "h_codes": [],
    }
    plan = build_ablation_plan(outcome)
    observed = _observation(task_id, "observed", passed=False, score=0.0)

    def should_not_run(_variant: str, _feature_id: str | None) -> RunObservation:
        raise AssertionError("Ex/M route invoked Feature executor")

    receipt = execute_task_plan(outcome, plan, observed, should_not_run)
    assert receipt["execution_status"] == "not_applicable"
    assert receipt["comparisons"] == []


def test_forged_experiment_is_rejected_before_executor_runs() -> None:
    calls: list[tuple[str, str | None]] = []
    outcome = {
        "task_id": "ws-forged-plan",
        "route": "h_feature_ablation",
        "codes": ["H1"],
        "h_codes": ["H1"],
    }
    plan = {
        "experiments": [
            {
                "feature_id": "F2.2",
                "owned_by_h_codes": ["H1"],
                "switch": "OFF",
                "all_other_features": "ON",
            }
        ]
    }
    observed = _observation("ws-forged-plan", "observed", passed=False, score=0.0)

    def executor(variant: str, feature_id: str | None) -> RunObservation:
        calls.append((variant, feature_id))
        return observed

    with pytest.raises(ValueError, match="not owned by accepted H evidence"):
        execute_task_plan(outcome, plan, observed, executor)

    assert calls == []


def test_plan_rejects_malformed_h_codes_and_recommendation_drift_before_execution() -> None:
    calls: list[tuple[str, str | None]] = []
    observed = _observation("ws-malformed-plan", "observed", passed=False, score=0.0)

    def executor(variant: str, feature_id: str | None) -> RunObservation:
        calls.append((variant, feature_id))
        return observed

    experiment = {
        "feature_id": "F1.1",
        "owned_by_h_codes": ["H1"],
        "switch": "OFF",
        "all_other_features": "ON",
    }
    with pytest.raises(ValueError, match="h_codes must be an array"):
        execute_task_plan(
            {"task_id": "ws-malformed-plan", "h_codes": None},
            {"experiments": [experiment]},
            observed,
            executor,
        )
    with pytest.raises(ValueError, match="must exactly match experiments"):
        execute_task_plan(
            {"task_id": "ws-malformed-plan", "h_codes": ["H1"]},
            {
                "recommended_feature_ids": ["F1.2"],
                "experiments": [experiment],
            },
            observed,
            executor,
        )
    assert calls == []


def test_run_observation_rejects_inconsistent_or_nonfinite_state() -> None:
    with pytest.raises(ValueError, match="passed must equal"):
        RunObservation(
            task_id="invalid-observation",
            variant="observed",
            passed=True,
            accepted=True,
            verifier_ok=False,
            score=1.0,
        )
    with pytest.raises(ValueError, match="score must be finite"):
        RunObservation(
            task_id="invalid-observation",
            variant="observed",
            passed=False,
            accepted=False,
            verifier_ok=False,
            score=float("nan"),
        )


def test_write_closed_loop_run_uses_aligned_bilingual_names(tmp_path: Path) -> None:
    task_id = "ua-demo-003"
    outcome = {
        "task_id": task_id,
        "passed": True,
        "score": 1.0,
        "codes": [],
        "h_codes": [],
        "pass_validation": {
            "status": "validated_pass",
            "checks": [
                {"code": "Ex-1", "status": "clear"},
                {"code": "Ex-2", "status": "clear"},
                {"code": "Ex-3", "status": "clear"},
            ],
            "audit": {"status": "ok"},
        },
    }
    plan = {
        "route": "pass_validated_no_ablation",
        "recommended_feature_ids": [],
        "experiments": [],
    }
    recording = {
        "task_id": task_id,
        "outcome": outcome,
        "ablation_plan": plan,
        "comparisons": [],
    }

    summary = write_closed_loop_run(tmp_path, [recording], run_name="demo")

    assert summary["task_count"] == 1
    assert summary["policy_checks"]["ex_m_never_trigger_automatically"] is True
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "REPORT_EN.md").is_file()
    assert (tmp_path / "REPORT_ZH.md").is_file()
    report = (tmp_path / "REPORT_EN.md").read_text(encoding="utf-8")
    assert "task design=clear" in report
    assert "judge=clear" in report
    assert "external resources=clear" in report
    assert "audit=ok" in report
    assert (tmp_path / "recordings" / f"{task_id}.json").is_file()


def test_write_closed_loop_run_refuses_symlink_recordings_directory(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    (output / "recordings").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="recordings directory must not be a symlink"):
        write_closed_loop_run(output, [])

    assert list(external.iterdir()) == []


def test_write_closed_loop_run_refuses_duplicate_or_forged_records(tmp_path: Path) -> None:
    record = {
        "task_id": "ws-policy-check",
        "outcome": {"task_id": "ws-policy-check", "h_codes": []},
        "ablation_plan": {"experiments": []},
        "comparisons": [],
    }
    with pytest.raises(ValueError, match="unique task IDs"):
        write_closed_loop_run(tmp_path / "duplicates", [record, record])

    forged = {
        **record,
        "ablation_plan": {
            "experiments": [
                {
                    "feature_id": "F1.1",
                    "owned_by_h_codes": ["H1"],
                    "switch": "OFF",
                    "all_other_features": "ON",
                }
            ]
        },
    }
    with pytest.raises(ValueError, match="violates H-only"):
        write_closed_loop_run(tmp_path / "forged", [forged])

    duplicate_feature = {
        **record,
        "outcome": {"task_id": "ws-policy-check", "h_codes": ["H1"]},
        "ablation_plan": {
            "recommended_feature_ids": ["F1.1", "F1.1"],
            "experiments": [
                {
                    "feature_id": "F1.1",
                    "owned_by_h_codes": ["H1"],
                    "switch": "OFF",
                    "all_other_features": "ON",
                },
                {
                    "feature_id": "F1.1",
                    "owned_by_h_codes": ["H1"],
                    "switch": "OFF",
                    "all_other_features": "ON",
                },
            ],
        },
    }
    with pytest.raises(ValueError, match="duplicates Feature"):
        write_closed_loop_run(tmp_path / "duplicate-feature", [duplicate_feature])


def test_write_closed_loop_run_refuses_stale_recording_mix(tmp_path: Path) -> None:
    output = tmp_path / "output"
    stale = output / "recordings" / "stale.json"
    _write_json(stale, {"task_id": "stale"})
    record = {
        "task_id": "ua-fresh",
        "outcome": {"task_id": "ua-fresh", "h_codes": []},
        "ablation_plan": {"experiments": []},
        "comparisons": [],
    }

    with pytest.raises(ValueError, match="refusing to mix"):
        write_closed_loop_run(output, [record])

    assert json.loads(stale.read_text(encoding="utf-8"))["task_id"] == "stale"
