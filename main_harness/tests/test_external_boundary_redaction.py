from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scripts import analyze_openclaw_v2 as openclaw
from scripts import bridge_attribution_to_harness_core as bridge
from scripts import run_real_api_feature_switch_matrix as matrix
from scripts import stress_test_real_v1_hf as real_v1
from scripts import stress_test_reasoning_v2 as reasoning
from scripts.feature_taxonomy import TAXONOMY_VERSION


SECRET = "sk-" + "A" * 32
HOME_PATH = str(Path.home() / "private" / "artifact.json")


def serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def assert_redacted(value: Any) -> None:
    text = value if isinstance(value, str) else serialized(value)
    assert SECRET not in text
    assert str(Path.home()) not in text


def test_bridge_redacts_evidence_prompt_and_structured_writes(tmp_path: Path) -> None:
    manifest = {
        "taxonomy_version": TAXONOMY_VERSION,
        "candidate": "TestHarness",
        "_candidate_dir": "test",
        "features": {
            "F3.1": {"name": "Completion"},
            "F3.2": {"name": "Budget"},
            "F3.3": {"name": "Recovery"},
        },
        "h_code_mapping": {"H3": ["F3.1", "F3.2", "F3.3"]},
    }
    row = {
        "run_group": "v1",
        "harness": "harness",
        "model": "model",
        "task_id": "task-1",
        "path": HOME_PATH,
        "codes": ["H3"],
        "evidence": f'api_key="{SECRET}"; timeout after max iterations; path={HOME_PATH}',
    }

    bridged = bridge.bridge_row(row, manifest)
    assert bridged["recommended_feature_ids"] == ["F3.2"]
    assert_redacted(bridged)

    summary = bridge.aggregate_bridged([bridged])
    payload = {
        "round": 1,
        "sample_size": 1,
        "candidate_summaries": {"test": summary},
        "bridge_rows": [bridged],
    }
    prompt = bridge.build_llm_prompt(payload)
    assert_redacted(prompt)
    json.loads(prompt.rsplit("Audit data:\n", 1)[1])

    json_path = tmp_path / "nested.json"
    jsonl_path = tmp_path / "nested.jsonl"
    raw = {"outer": {"api_key": SECRET, "path": HOME_PATH}}
    bridge.write_json(json_path, raw)
    bridge.write_jsonl(jsonl_path, [raw])
    assert_redacted(json_path.read_text(encoding="utf-8"))
    assert_redacted(jsonl_path.read_text(encoding="utf-8"))


def test_openclaw_redacts_package_context_prompt_and_jsonl(tmp_path: Path) -> None:
    task_id = "task-1"
    result_dir = tmp_path / "bundle" / task_id
    task_dir = tmp_path / "tasks" / task_id
    result_dir.mkdir(parents=True)
    task_dir.mkdir(parents=True)
    (task_dir / "instruction.md").write_text(
        f'Inspect api_key="{SECRET}" at {HOME_PATH}', encoding="utf-8"
    )
    (result_dir / "trajectory.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "steps": [
                    {
                        "step_id": 1,
                        "source": "agent",
                        "message": f'api_key="{SECRET}" {HOME_PATH}',
                        "tool_calls": [],
                        "observation": {"results": []},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (result_dir / "trial.result.json").write_text(
        json.dumps(
            {
                "agent_info": {"version": HOME_PATH},
                "agent_result": {"n_input_tokens": 1, "n_output_tokens": 1},
                "exception_info": {"api_key": SECRET},
            }
        ),
        encoding="utf-8",
    )
    (result_dir / "verifier.reward.json").write_text(
        json.dumps({"details": {"password": SECRET}}), encoding="utf-8"
    )
    bundle_manifest = {
        "dataset": f"dataset-{SECRET}",
        "agent": "openclaw",
        "model": "model",
        "judge": "judge",
        "tasks": {task_id: {"status": "failed", "score": 0, "passed": False}},
    }

    package, ground_text = openclaw.evidence_package(
        task_id,
        bundle_dir=tmp_path / "bundle",
        tasks_dir=tmp_path / "tasks",
        bundle_manifest=bundle_manifest,
    )
    assert_redacted(package)
    assert_redacted(ground_text)

    prompt = openclaw.build_prompt(
        package,
        definition_seed=1,
        candidate_context=[{"api_key": SECRET, "path": HOME_PATH}],
        repair_errors=[f'password="{SECRET}" at {HOME_PATH}'],
    )
    assert_redacted(prompt)
    json.loads(prompt.rsplit("evidence_package:\n", 1)[1])

    output = tmp_path / "openclaw_inputs.jsonl"
    openclaw.write_jsonl(
        output,
        [{"package": {"api_key": SECRET}, "ground_text": HOME_PATH}],
    )
    assert_redacted(output.read_text(encoding="utf-8"))


def test_real_v1_redacts_package_prompt_and_jsonl(tmp_path: Path) -> None:
    trajectory = tmp_path / "trajectory.jsonl"
    trajectory.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": f'api_key="{SECRET}" path={HOME_PATH}',
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    row = {
        "run_group": "v1",
        "model": SECRET,
        "harness": "harness",
        "task_id": "task-1",
        "notes": f'password="{SECRET}"',
        "anomaly": {"path": HOME_PATH},
        "breakdown": {"api_key": SECRET},
    }

    package, ground_text = real_v1.evidence_package(row, trajectory)
    assert_redacted(package)
    assert_redacted(ground_text)
    prompt = real_v1.build_prompt(package, definition_seed=1)
    assert_redacted(prompt)
    json.loads(prompt.rsplit("evidence_package:\n", 1)[1])

    output = tmp_path / "v1_inputs.jsonl"
    real_v1.write_jsonl(output, [{"package": row, "ground_text": HOME_PATH}])
    assert_redacted(output.read_text(encoding="utf-8"))


def test_call_model_redacts_prompt_and_raw_content_before_parse(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "id": "response-1",
                "model": "fake-model",
                "usage": {},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "taxonomy_version": TAXONOMY_VERSION,
                                    "results": [],
                                    "reason": f'api_key="{SECRET}" {HOME_PATH}',
                                }
                            )
                        }
                    }
                ],
            }

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(reasoning, "api_settings", lambda: ("header-secret", "https://example.com/v1"))
    monkeypatch.setattr(reasoning.requests, "post", fake_post)

    response = reasoning.call_model(
        "deepseek-v4-pro",
        f'api_key="{SECRET}" path={HOME_PATH}',
        timeout=1,
        retries=0,
    )

    assert response["ok"] is True
    assert_redacted(response["raw_content"])
    assert_redacted(response["parsed"])
    assert_redacted(captured["json"]["messages"][1]["content"])


def test_reasoning_prompt_remains_parseable_after_redaction() -> None:
    prompt = reasoning.taxonomy_prompt(
        [{"id": "task-1", "evidence": f'api_key="{SECRET}" at {HOME_PATH}'}]
    )

    assert_redacted(prompt)
    json.loads(prompt.rsplit("Cases:\n", 1)[1])


def test_bridge_api_boundary_redacts_direct_prompt(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self) -> dict[str, Any]:
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {},
            }

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(bridge, "api_settings", lambda: ("header-secret", "https://example.com/v1"))
    monkeypatch.setattr(bridge.requests, "post", fake_post)

    bridge.call_qwen_audit(
        f'api_key="{SECRET}" path={HOME_PATH}',
        model="qwen-test",
        timeout=1,
        retries=0,
    )

    assert_redacted(captured["json"]["messages"][1]["content"])


def test_matrix_removes_absolute_paths_and_redacts_trace_preview_and_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    out_root = tmp_path / "runs"
    workspace = out_root / "candidate" / "case" / "workspace_root"
    workspace.mkdir(parents=True)
    trace_path = workspace.parent / "trace.jsonl"
    monkeypatch.setattr(matrix, "OUT_ROOT", out_root)

    prompt = matrix.build_prompt(
        {"candidate": f'api_key="{SECRET}"'},
        "case",
        {"F1.1"},
        workspace,
        trace_path,
    )
    assert str(workspace) not in prompt
    assert "Workspace root alias: workspace_root" in prompt
    assert_redacted(prompt)

    matrix.trace_append(
        trace_path,
        "llm_api_result",
        {"content_preview": f'password="{SECRET}"', "path": HOME_PATH},
    )
    assert_redacted(trace_path.read_text(encoding="utf-8"))

    captured: dict[str, Any] = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "path": "workspace/answer.txt",
                                    "content": f'api_key="{SECRET}" {HOME_PATH}',
                                }
                            )
                        )
                    )
                ],
                usage=None,
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    result = matrix.call_llm(
        client,
        {"models": ["fake-model"], "base_url": "https://example.com/v1"},
        f'api_key="{SECRET}" path={HOME_PATH}',
    )
    assert_redacted(result)
    assert_redacted(captured["messages"][1]["content"])
