from __future__ import annotations

import json
from pathlib import Path

from pawbench.harbor_v2.backend import HarborV2Backend


def test_save_system_prompt_records_effective_append_prompt(tmp_path: Path) -> None:
    trial_dir = tmp_path / "trial"

    HarborV2Backend._save_system_prompt(trial_dir, "first line\nsecond line")

    assert (trial_dir / "agent" / "system_prompt.txt").read_text(
        encoding="utf-8"
    ) == "first line\nsecond line\n"


def test_save_run_provenance_records_evaluation_contract(tmp_path: Path) -> None:
    trial_dir = tmp_path / "trial"
    provenance = {
        "schema_version": 1,
        "dataset": "data/full",
        "task_id": "task-001",
        "judge": {"framework": "openjudge", "model": "judge-model"},
    }

    HarborV2Backend._save_run_provenance(trial_dir, provenance)

    assert json.loads((trial_dir / "provenance.json").read_text()) == provenance


# NOTE: the trajectory/reward/workspace bundling previously done here by
# HarborV2Backend._copy_saved_workspace() now happens in
# pawbench/runner.py::_save_task_bundle() (see
# pawbench/runner/tests/test_runner.py), reading straight from
# TaskResult.trial_dir instead of a backend-specific method.
