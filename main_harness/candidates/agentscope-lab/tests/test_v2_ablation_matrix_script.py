from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "v2_ablation_matrix.py"
SPEC = importlib.util.spec_from_file_location("pawbench_v2_ablation_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_matrix_covers_all_features_and_marks_owned_output(tmp_path: Path) -> None:
    output = tmp_path / "matrix"

    summary = MODULE.run_matrix(output)

    assert summary["case_count"] == 15
    assert summary["passed"] == 15
    assert summary["failed"] == []
    assert (output / MODULE.OUTPUT_MARKER).read_text(encoding="utf-8") == (
        MODULE.OUTPUT_SCHEMA + "\n"
    )


def test_matrix_refuses_to_replace_unmarked_output(tmp_path: Path) -> None:
    output = tmp_path / "matrix"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("user-owned\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unmarked output"):
        MODULE.run_matrix(output)

    assert sentinel.read_text(encoding="utf-8") == "user-owned\n"


def test_help_does_not_execute_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "DEFAULT_OUT", tmp_path / "must-not-exist")

    with pytest.raises(SystemExit) as exc_info:
        MODULE.main(["--help"])

    assert exc_info.value.code == 0
    assert not (tmp_path / "must-not-exist").exists()
