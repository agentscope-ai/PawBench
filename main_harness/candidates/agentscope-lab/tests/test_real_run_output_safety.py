from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str) -> ModuleType:
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(f"output_safety_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def task_root(tmp_path: Path) -> Path:
    root = tmp_path / "task"
    assets = root / "environment" / "assets"
    assets.mkdir(parents=True)
    (assets / "input.txt").write_text("fixture", encoding="utf-8")
    return root


def test_real_audit_reset_refuses_output_symlink(task_root: Path, tmp_path: Path) -> None:
    module = _load("real_audit_trail_loop.py")
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "run"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        module.reset_workspace(task_root, link)

    assert not any(target.iterdir())


def test_feature_ablation_replaces_only_owned_workspace(task_root: Path, tmp_path: Path) -> None:
    module = _load("real_feature_ablation.py")
    output = tmp_path / "output"
    workspace = output / "all_features" / "workspace_root"
    workspace.mkdir(parents=True)
    victim = workspace / "keep.txt"
    victim.write_text("user-owned", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to replace unmarked"):
        module.reset_workspace(task_root, output, "all_features")

    assert victim.read_text(encoding="utf-8") == "user-owned"


def test_feature_ablation_owned_workspace_can_be_refreshed(task_root: Path, tmp_path: Path) -> None:
    module = _load("real_feature_ablation.py")
    output = tmp_path / "output"
    workspace = module.reset_workspace(task_root, output, "all_features")
    (workspace / "stale.txt").write_text("stale", encoding="utf-8")

    refreshed = module.reset_workspace(task_root, output, "all_features")

    assert refreshed == workspace
    assert not (workspace / "stale.txt").exists()
    assert (workspace / "input.txt").read_text(encoding="utf-8") == "fixture"
