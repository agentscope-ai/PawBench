from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "summarize_repeated_ablation.py"
SPEC = importlib.util.spec_from_file_location("pawbench_repeated_summary", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_comparison_reader_rejects_nonobject_rows_and_ambiguous_json(tmp_path: Path) -> None:
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    path = recordings / "one.json"
    path.write_text(json.dumps({"comparisons": [None]}), encoding="utf-8")
    with pytest.raises(ValueError, match="comparison 0 must be an object"):
        MODULE._read_comparisons(tmp_path)

    path.write_text('{"comparisons":[],"comparisons":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        MODULE._read_comparisons(tmp_path)


def test_comparison_reader_refuses_symlinked_recordings_directory(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    root = tmp_path / "run"
    root.mkdir()
    (root / "recordings").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="recordings directory is missing"):
        MODULE._read_comparisons(root)


def test_summary_writes_owned_output_and_fresh_replaces_it(tmp_path: Path) -> None:
    source = tmp_path / "source"
    recordings = source / "recordings"
    recordings.mkdir(parents=True)
    (recordings / "one.json").write_text(
        json.dumps({"comparisons": []}),
        encoding="utf-8",
    )
    output = tmp_path / "summary"
    argv = [
        "--closed-loop-root",
        str(source),
        "--output",
        str(output),
    ]

    assert MODULE.main(argv) == 0
    assert (output / MODULE.OUTPUT_MARKER).read_text(encoding="utf-8") == (
        MODULE.RELIABILITY_SCHEMA_VERSION + "\n"
    )
    stale = output / "stale.txt"
    stale.write_text("old", encoding="utf-8")

    assert MODULE.main([*argv, "--fresh"]) == 0
    assert not stale.exists()


def test_summary_fresh_refuses_unmarked_output(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "recordings").mkdir(parents=True)
    output = tmp_path / "summary"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("user-owned", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        MODULE.main(
            [
                "--closed-loop-root",
                str(source),
                "--output",
                str(output),
                "--fresh",
            ]
        )

    assert exc_info.value.code == 2
    assert sentinel.read_text(encoding="utf-8") == "user-owned"
