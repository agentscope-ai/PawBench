from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from pawbench.harbor_v2.generative_user import materialize_generative_task


def _make_task(tmp_path: Path, *, cowork: bool) -> SimpleNamespace:
    task = tmp_path / ("cowork-task" if cowork else "multi-turn-task")
    (task / "environment" / "assets" / "workspace").mkdir(parents=True)
    (task / "environment" / "assets" / "workspace" / "index.html").write_text(
        "<html>\n",
        encoding="utf-8",
    )
    (task / "environment" / "Dockerfile").write_text(
        "FROM scratch\n",
        encoding="utf-8",
    )
    (task / "user").mkdir()
    (task / "user" / "persona.md").write_text("persona", encoding="utf-8")
    if cowork:
        patches = task / "user" / "patches"
        patches.mkdir()
        (patches / "turn_01_hint.md").write_text(
            "---\nfiles: []\n---\n",
            encoding="utf-8",
        )
    (task / "messages.jsonl").write_text(
        "\n".join(
            json.dumps({"turn": turn, "role": "user", "content": f"turn {turn}"})
            for turn in (1, 2)
        ),
        encoding="utf-8",
    )
    (task / "task.toml").write_text(
        'schema_version = "1.0"\n',
        encoding="utf-8",
    )
    (task / "instruction.md").write_text("task", encoding="utf-8")
    return SimpleNamespace(task_dir=task, task_id=task.name)


def test_cowork_materialization_shares_seeded_workspace(tmp_path: Path):
    task = _make_task(tmp_path, cowork=True)
    runtime, server_dir = materialize_generative_task(
        task,
        tmp_path / "runtime-cowork",
    )

    compose = (runtime / "environment" / "docker-compose.yaml").read_text()
    assert "source: pawbench-workspace" in compose
    assert "target: /home/node/workspace" in compose
    assert "target: /workspace" in compose
    assert "USER_SIM_WORKSPACE_ROOT=/workspace" in compose
    instruction = (runtime / "instruction.md").read_text(encoding="utf-8")
    assert "FIRST task action" in instruction
    assert "<original-instruction>" not in instruction
    assert (
        server_dir / "workspace" / "workspace" / "index.html"
    ).read_text() == "<html>\n"
    assert (server_dir / "task" / ".patch" / "turn_01_hint.md").is_file()


def test_non_cowork_materialization_does_not_mount_workspace(tmp_path: Path):
    task = _make_task(tmp_path, cowork=False)
    runtime, server_dir = materialize_generative_task(
        task,
        tmp_path / "runtime-multi-turn",
    )

    compose = (runtime / "environment" / "docker-compose.yaml").read_text()
    assert "pawbench-workspace" not in compose
    assert list((server_dir / "workspace").iterdir()) == []
