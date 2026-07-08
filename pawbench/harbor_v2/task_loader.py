# -*- coding: utf-8 -*-
"""Load Harbor-native (v2) task packages.

A v2 task is a directory shaped like::

    <task_id>/
      task.toml               # Harbor TaskConfig (schema_version, metadata, verifier, agent, environment)
      instruction.md          # the agent prompt
      environment/Dockerfile  # per-task environment image
      solution/solve.sh       # reference / placeholder solution
      tests/
        test.sh               # runs `uvx harbor-rewardkit /tests`
        reward.toml           # reward aggregation (e.g. all_pass)
        structure/…           # stage-1 structural checks
        quality/…             # stage-2 LLM-judge checks (criteria.md + verifier.py)

Datasets extracted under ``data/<dataset>/`` may nest the task directories one
level deeper inside a ``data_v2/`` folder (as shipped in the 0706 zip).  The
loader auto-detects both layouts.

The parsed :class:`HarborV2Task` deliberately mirrors the attribute surface of
:class:`pawbench.task_loader.Task` (``task_id`` / ``name`` / ``prompt`` /
``timeout_seconds`` / ``frontmatter`` / ``file_path``) so that
:class:`pawbench.runner.BenchmarkRunner` and its label/anomaly reporting can
consume v2 tasks without modification.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

# Files that identify a directory as a Harbor task package.
_TASK_TOML = "task.toml"
_INSTRUCTION_MD = "instruction.md"


class HarborV2Task:
    """A single Harbor-native task parsed from ``task.toml`` + ``instruction.md``."""

    def __init__(
        self,
        task_id: str,
        name: str,
        prompt: str,
        timeout_seconds: int,
        task_dir: Path,
        metadata: Dict[str, Any],
        raw_config: Dict[str, Any],
        frontmatter: Dict[str, Any],
    ) -> None:
        self.task_id = task_id
        self.name = name
        self.prompt = prompt
        self.timeout_seconds = timeout_seconds
        self.task_dir = task_dir
        self.metadata = metadata
        # Full parsed task.toml (agent/verifier/environment sections included).
        self.raw_config = raw_config
        # Label dimensions consumed by pawbench's reporting layer.  Mirrors the
        # YAML front-matter of v1 tasks so ``runner._build_label_summary`` works.
        self.frontmatter = frontmatter
        # ``file_path`` is used by task-id filtering (``.stem``); point it at the
        # task directory so ``--tasks <dir-name>`` keeps working.
        self.file_path = task_dir

    def __repr__(self) -> str:
        return f"HarborV2Task(id={self.task_id!r}, name={self.name!r})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "timeout_seconds": self.timeout_seconds,
            "task_dir": str(self.task_dir),
            "metadata": self.metadata,
            "frontmatter": self.frontmatter,
        }


class HarborV2Loader:
    """Discover and parse Harbor-native task directories under a dataset root."""

    def __init__(self, dataset_root: Path) -> None:
        self.dataset_root = dataset_root
        logger.info("HarborV2Loader initialised: %s", dataset_root)

    # ── discovery ──────────────────────────────────────────────────────────────

    def _task_dirs(self) -> List[Path]:
        """Return every directory that looks like a Harbor task package.

        Handles both ``<dataset>/<task>/`` and ``<dataset>/data_v2/<task>/``
        layouts by scanning up to two levels deep for a ``task.toml`` marker.
        """
        found: List[Path] = []
        seen: set[Path] = set()

        def _maybe_add(d: Path) -> None:
            if (d / _TASK_TOML).is_file() and d not in seen:
                seen.add(d)
                found.append(d)

        # Level 1: <dataset>/<task>/
        for child in sorted(self.dataset_root.iterdir()):
            if not child.is_dir():
                continue
            _maybe_add(child)
            # Level 2: <dataset>/<subdir>/<task>/  (e.g. data_v2/<task>/)
            if not (child / _TASK_TOML).is_file():
                for grandchild in sorted(child.iterdir()):
                    if grandchild.is_dir():
                        _maybe_add(grandchild)
        return found

    # ── public API ───────────────────────────────────────────────────────────

    def load_all_tasks(self) -> List[HarborV2Task]:
        task_dirs = self._task_dirs()
        logger.info("Found %d Harbor task package(s)", len(task_dirs))
        tasks: List[HarborV2Task] = []
        for task_dir in task_dirs:
            try:
                tasks.append(self.load_task(task_dir))
            except Exception:
                logger.exception("Failed to load Harbor task from %s", task_dir)
        logger.info("Loaded %d Harbor task(s) successfully", len(tasks))
        return tasks

    def load_task(self, task_dir: Path) -> HarborV2Task:
        toml_path = task_dir / _TASK_TOML
        raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))

        metadata: Dict[str, Any] = raw.get("metadata", {}) or {}
        agent_cfg: Dict[str, Any] = raw.get("agent", {}) or {}
        verifier_cfg: Dict[str, Any] = raw.get("verifier", {}) or {}

        instruction_path = task_dir / _INSTRUCTION_MD
        prompt = ""
        if instruction_path.is_file():
            prompt = instruction_path.read_text(encoding="utf-8")

        task_id = task_dir.name
        # ``[task].name`` is the packaged name when present; else use the dir.
        pkg = raw.get("task", {}) or {}
        name = pkg.get("name") or task_id

        # Prefer the agent phase timeout; fall back to verifier or a safe default.
        timeout_seconds = int(
            agent_cfg.get("timeout_sec")
            or verifier_cfg.get("timeout_sec")
            or 1200
        )

        frontmatter = self._build_frontmatter(metadata, raw)

        return HarborV2Task(
            task_id=task_id,
            name=name,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            task_dir=task_dir,
            metadata=metadata,
            raw_config=raw,
            frontmatter=frontmatter,
        )

    # ── label mapping ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_frontmatter(
        metadata: Dict[str, Any],
        raw: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Map Harbor ``[metadata]`` onto pawbench label dimensions.

        pawbench's ``runner._build_label_summary`` buckets results by
        ``scenario`` / ``capabilities`` / ``complexity`` / ``modality`` /
        ``environment``.  Harbor v2 metadata does not carry those keys verbatim,
        so we derive the closest equivalents (best-effort, non-fatal):

        * ``scenario``     ← ``metadata.category``
        * ``capabilities`` ← ``metadata.tags`` (list)
        * ``environment``  ← ``open`` when the task allows internet / public
                             network, else ``closed``
        * ``complexity``   ← ``metadata.complexity`` if present (else omitted)
        * ``modality``     ← ``metadata.modality`` if present (else omitted)

        Any keys already present verbatim in ``metadata`` win over the derived
        values so hand-authored labels are respected.
        """
        fm: Dict[str, Any] = {}

        category = metadata.get("category")
        if category is not None:
            fm["scenario"] = category

        tags = metadata.get("tags")
        if isinstance(tags, list) and tags:
            fm["capabilities"] = list(tags)

        env_cfg = raw.get("environment", {}) or {}
        allow_internet = env_cfg.get("allow_internet")
        network_mode = env_cfg.get("network_mode")
        if allow_internet is not None or network_mode is not None:
            is_open = bool(allow_internet) or network_mode == "public"
            fm["environment"] = "open" if is_open else "closed"

        # Respect explicit metadata labels when authored.
        for key in ("scenario", "capabilities", "complexity", "modality", "environment"):
            if metadata.get(key) is not None:
                fm[key] = metadata[key]

        return fm
