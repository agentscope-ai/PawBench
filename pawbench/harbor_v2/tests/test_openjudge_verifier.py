import json
from pathlib import Path

import pytest
from pawbench.harbor_v2.verifier import (
    load_agent_judge_framework,
    materialize_openjudge_task,
    uses_openjudge,
)
from pawbench.harbor_v2.verifier.input_contract import validate_atif


def _write_agent_judge_task(
    root: Path,
    *,
    framework: str | None,
    test_script: str | None = None,
) -> Path:
    task = root / "task"
    quality = task / "tests" / "quality"
    quality.mkdir(parents=True)
    judge_lines = ["[judge]"]
    if framework is not None:
        judge_lines.append(f'framework = "{framework}"')
    judge_lines.extend(['judge = "claude-code"', 'model = "test-model"'])
    (quality / "agent_judge.toml").write_text(
        "\n".join(judge_lines) + "\n",
        encoding="utf-8",
    )
    if test_script is not None:
        (task / "tests" / "test.sh").write_text(test_script, encoding="utf-8")
    return task


def test_framework_defaults_to_rewardkit(tmp_path: Path):
    task = _write_agent_judge_task(tmp_path, framework=None)

    assert load_agent_judge_framework(task) == "rewardkit"
    assert not uses_openjudge(task)


def test_materialize_injects_central_openjudge_files(tmp_path: Path):
    task = _write_agent_judge_task(
        tmp_path,
        framework="openjudge",
        test_script="#!/bin/sh\nexit 99\n",
    )
    destination = tmp_path / "runtime" / "openjudge-task"

    runtime = materialize_openjudge_task(
        task,
        destination,
        provenance={"dataset": "data/full", "task_id": "task-1"},
    )

    assert runtime == destination
    assert uses_openjudge(runtime)
    runner_path = runtime / "tests" / "quality" / "run_openjudge.py"
    assert runner_path.is_file()
    runner = runner_path.read_text(encoding="utf-8")
    assert "openjudge-judge-stream.jsonl" in runner
    assert "openjudge-judge-stderr.log" in runner
    assert "openjudge-judge-spec.json" in runner
    assert "openjudge-judge-result.json" in runner
    assert "openjudge-harness.json" in runner
    assert "def _persist_harness_logs(" in runner
    assert "from input_contract import validate_atif" in runner
    assert (runtime / "tests" / "quality" / "input_contract.py").is_file()
    provenance = json.loads(
        (runtime / "tests" / "quality" / "pawbench-provenance.json").read_text()
    )
    assert provenance["dataset"] == "data/full"
    assert provenance["task_id"] == "task-1"
    assert len(provenance["task_contract_sha256"]) == 64
    assert len(provenance["openjudge_adapter_sha256"]) == 64
    dispatcher = (runtime / "tests" / "test.sh").read_text(encoding="utf-8")
    assert 'case "${FRAMEWORK:-rewardkit}"' in dispatcher
    assert "py-openjudge" in dispatcher
    assert "harbor-rewardkit" in dispatcher
    assert (runtime / "tests" / "test.sh").stat().st_mode & 0o111
    # Runtime injection must not mutate source datasets.
    assert not (task / "tests" / "quality" / "run_openjudge.py").exists()
    assert (task / "tests" / "test.sh").read_text(encoding="utf-8").endswith(
        "exit 99\n"
    )


def test_materialize_rejects_rewardkit_task(tmp_path: Path):
    task = _write_agent_judge_task(tmp_path, framework="rewardkit")

    with pytest.raises(ValueError, match="does not declare"):
        materialize_openjudge_task(task, tmp_path / "runtime")


def test_openjudge_input_contract_rejects_native_session() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        validate_atif({"agent": {"state": {"context": []}}})


def test_openjudge_input_contract_accepts_atif() -> None:
    payload = {
        "schema_version": "ATIF-v1.7",
        "session_id": "session",
        "agent": {"name": "qwenpaw", "version": "2.0"},
        "steps": [{"step_id": 1, "source": "user", "message": "hello"}],
    }

    assert validate_atif(payload) is payload
