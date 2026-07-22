"""Runtime verifier adapters for Harbor-v2 tasks."""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

OPENJUDGE_FRAMEWORK = "openjudge"

_PACKAGE_DIR = Path(__file__).parent
_RUNNER_SOURCE = _PACKAGE_DIR / "run_openjudge.py"
_DISPATCHER_SOURCE = _PACKAGE_DIR / "test.sh"


def load_agent_judge_framework(task_dir: Path) -> str:
    """Return the declared agent-judge framework, defaulting to RewardKit."""
    config_path = Path(task_dir) / "tests" / "quality" / "agent_judge.toml"
    if not config_path.is_file():
        return "rewardkit"
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "rewardkit"
    framework = str((payload.get("judge") or {}).get("framework") or "rewardkit")
    return framework.strip().lower()


def uses_openjudge(task_dir: Path) -> bool:
    """Whether a task explicitly opts into PawBench's OpenJudge adapter."""
    return load_agent_judge_framework(task_dir) == OPENJUDGE_FRAMEWORK


def materialize_openjudge_task(task_dir: Path, destination: Path) -> Path:
    """Copy an opted-in task and inject the centrally maintained verifier.

    Source datasets stay immutable. Only the runtime copy receives the runner
    and dispatcher, so existing RewardKit tasks and their custom ``test.sh``
    files are never changed.
    """
    source = Path(task_dir)
    target = Path(destination)
    if not uses_openjudge(source):
        raise ValueError(f"Task {source} does not declare judge.framework='openjudge'")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True)

    quality_dir = target / "tests" / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_RUNNER_SOURCE, quality_dir / "run_openjudge.py")
    shutil.copy2(_DISPATCHER_SOURCE, target / "tests" / "test.sh")
    (target / "tests" / "test.sh").chmod(0o755)
    return target


__all__ = [
    "OPENJUDGE_FRAMEWORK",
    "load_agent_judge_framework",
    "materialize_openjudge_task",
    "uses_openjudge",
]
