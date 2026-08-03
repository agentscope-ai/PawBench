# -*- coding: utf-8 -*-
"""PawBench backend — abstract contract and TaskResult dataclass.

Concrete backend: :class:`pawbench.harbor_v2.HarborV2Backend`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaskResult:
    """Unified result returned by the backend after running one task."""

    task_id: str
    task_name: str
    score: float
    max_score: float
    passed: bool
    grading_type: str
    breakdown: dict[str, float]
    notes: str
    execution_time: float
    status: str          # "success" | "timeout" | "error"
    usage: dict[str, Any]
    transcript_length: int
    timed_out: bool
    error: str = ""
    transcript: list = field(default_factory=list)
    # Anomaly detection result (from anomalies.detect_anomalies).
    # has_error=True means the score is unreliable (API quota, OOM, etc.).
    anomaly: dict = field(default_factory=dict)
    # Task taxonomy labels extracted from the task's YAML front-matter.
    # Keys: scenario, capabilities, complexity, modality, environment.
    labels: dict = field(default_factory=dict)
    # Requested/effective agent mode and observed delegation evidence.
    multi_agent: dict = field(default_factory=dict)
    # Absolute path to this result's backing trial directory (e.g. Harbor's
    # ``trials/<task>__<run_id>__<hash>/``), if any. Lets the runner persist
    # the harness's own raw trajectory file verbatim instead of re-deriving
    # one from ``transcript``. Empty when the backend has no such concept.
    trial_dir: str = ""


class BenchmarkBackend(ABC):
    """Abstract contract for a benchmark backend.

    Concrete implementation: :class:`pawbench.harbor_v2.HarborV2Backend`.
    """

    def __init__(self, benchmark_path: str | Path) -> None:
        self.benchmark_path = Path(benchmark_path).resolve()
        if not self.benchmark_path.exists():
            raise FileNotFoundError(
                f"Benchmark path not found: {self.benchmark_path}"
            )

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. ``'pawbench'``."""

    @abstractmethod
    def load_tasks(
        self,
        task_filter: list[str] | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """Load native Task objects from the benchmark directory."""

    @abstractmethod
    def run_and_grade(
        self,
        task: Any,
        agent_config: dict[str, Any],
    ) -> TaskResult:
        """Execute *task* and grade the result."""
