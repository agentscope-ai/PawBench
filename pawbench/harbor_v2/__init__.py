# -*- coding: utf-8 -*-
"""Harbor-native (v2) dataset support for pawbench.

The v2 dataset is the *native Harbor task package* format: each task is a
directory containing ``task.toml`` + ``instruction.md`` + ``environment/`` +
``solution/`` + ``tests/`` (RewardKit verifier).  Rather than re-implementing
the Harbor execution/grading pipeline inside pawbench, this package drives
Harbor's own :class:`~harbor.trial.trial.Trial` runner for each task and maps
the resulting :class:`~harbor.models.trial.result.TrialResult` back into a
pawbench :class:`~pawbench.backend.TaskResult`, so the existing
:class:`~pawbench.runner.BenchmarkRunner` keeps producing pass@k / label
reports / anomaly detection / result JSON unchanged.

Public API
----------
* :class:`HarborV2Task`     — one v2 task parsed from task.toml + instruction.md
* :class:`HarborV2Loader`   — discover + parse v2 task directories
* :class:`HarborV2Backend`  — run + grade v2 tasks via Harbor Trial
"""

from .task_loader import HarborV2Loader, HarborV2Task
from .backend import HarborV2Backend

__all__ = [
    "HarborV2Backend",
    "HarborV2Loader",
    "HarborV2Task",
]
