from pathlib import Path

import pytest

from pawbench.backend import PawBenchBackend


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("selector", ["T002", "T150"])
def test_numeric_task_filter_selects_filename_index_only(selector):
    tasks = PawBenchBackend(ROOT).load_tasks([selector])

    assert len(tasks) == 1
    assert tasks[0].file_path.stem.startswith(f"{selector}_")


def test_full_legacy_task_id_remains_selectable():
    tasks = PawBenchBackend(ROOT).load_tasks(["T150_project_progress_report"])

    assert [task.task_id for task in tasks] == ["T150_project_progress_report"]
