# -*- coding: utf-8 -*-
"""pawbench — agent benchmark runner for the pawbench dataset.

Public API
----------
* :class:`HarborV2Backend`  — load + run + grade tasks via Harbor Trial
* :class:`BenchmarkBackend` — abstract contract
* :class:`TaskResult`       — per-task result dataclass
* :class:`BenchmarkRunner`  — orchestrates concurrent execution + checkpointing
* :class:`Task`, :class:`TaskLoader` — task data model and Markdown parser
* :class:`GradeResult`, :func:`grade_task` — grading entry points
"""

from .backend import BenchmarkBackend, TaskResult
from .grader import GradeResult, grade_task
from .runner import BenchmarkRunner
from .task_loader import Task, TaskLoader

__all__ = [
    "BenchmarkBackend",
    "BenchmarkRunner",
    "GradeResult",
    "Task",
    "TaskLoader",
    "TaskResult",
    "grade_task",
    "get_harbor_v2_backend",
]


def get_harbor_v2_backend():
    """Return the :class:`HarborV2Backend` class (imported on demand)."""
    from .harbor_v2 import HarborV2Backend
    return HarborV2Backend
