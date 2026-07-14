from __future__ import annotations

import csv

from scripts.analyze_openclaw_v2 import (
    CJK_PATTERN,
    build_prompt,
    render_report,
    render_step,
    select_step_indices,
    validate_response,
    write_attribution_csv,
    zh_prose,
)


def test_atif_step_keeps_tool_observation_and_write_content() -> None:
    rendered = render_step(
        {
            "step_id": 7,
            "source": "agent",
            "message": "writing the deliverable",
            "tool_calls": [
                {
                    "function_name": "write",
                    "arguments": {"path": "/app/report.md", "content": "final evidence"},
                }
            ],
            "observation": {
                "results": [
                    {"source_call_id": "call-1", "content": "Successfully wrote 14 bytes"}
                ]
            },
        }
    )

    assert rendered["tool_calls"][0]["arguments"]["content"] == "final evidence"
    assert rendered["tool_results"][0]["content"] == "Successfully wrote 14 bytes"


def test_long_trajectory_preserves_first_last_and_write_steps() -> None:
    steps = [
        {"step_id": index, "message": f"step {index}", "tool_calls": []}
        for index in range(40)
    ]
    steps[20]["tool_calls"] = [{"function_name": "write", "arguments": {"path": "x"}}]

    selected = select_step_indices(steps, limit=24)

    assert {0, 1, 2, 3, 20, 32, 33, 34, 35, 36, 37, 38, 39} <= set(selected)
    assert len(selected) <= 24


def test_prompt_requires_api_attribution_without_confidence() -> None:
    package = {
        "task": {"task_id": "task-1", "instruction": "do the task"},
        "run": {"score": 0},
        "trajectory": {"steps": []},
    }

    prompt = build_prompt(package, definition_seed=1)

    assert "不得把分数直接映射为代码" in prompt
    assert "deepseek-v4-pro" in prompt
    assert '"confidence"' not in prompt
    assert '"failure_summary_en"' in prompt
    assert '"evidence_quote_en"' in prompt


def test_prompt_names_the_selected_attribution_model() -> None:
    package = {
        "task": {"task_id": "task-1", "instruction": "do the task"},
        "run": {"score": 0},
        "trajectory": {"steps": []},
    }

    prompt = build_prompt(package, definition_seed=1, judge_model="qwen3.7-max")

    assert "归因 Judge（qwen3.7-max）" in prompt
    assert "归因 Judge（deepseek-v4-pro）" not in prompt


def test_zh_prose_uses_english_technical_terms() -> None:
    assert zh_prose("任务契约、评分器和验收真值") == "task contract、scorer 和 ground truth"


def test_validation_accepts_grounded_h_feature_pair() -> None:
    quote = "The verifier failed, yet the acceptance gate reported false success."
    response = {
        "ok": True,
        "parsed": {
            "task_id": "task-1",
            "codes": [
                {"code": "H4", "evidence_quote": quote, "evidence_quote_en": quote}
            ],
            "features": [
                {
                    "feature_id": "F4.3",
                    "h_code": "H4",
                    "evidence_quote": quote,
                    "evidence_quote_en": quote,
                }
            ],
            "failure_summary": "验收门控错误。",
            "failure_summary_en": "The acceptance gate is incorrect.",
            "causal_analysis": ["verifier 失败。", "门控却通过。"],
            "causal_analysis_en": ["The verifier failed.", "The gate still passed."],
            "ablation_test": "关闭 F4.3 后比较验收结果。",
            "ablation_test_en": "Disable F4.3 and compare the acceptance result.",
        },
    }

    validation = validate_response(response, task_id="task-1", ground_text=quote)

    assert validation["ok"] is True
    assert validation["codes"] == ["H4"]
    assert validation["feature_ids"] == ["F4.3"]


def test_validation_rejects_score_only_or_invalid_mapping_output() -> None:
    quote = "score is zero but no direct harness evidence appears"
    response = {
        "ok": True,
        "parsed": {
            "task_id": "task-1",
            "codes": [
                {"code": "H2", "evidence_quote": quote, "evidence_quote_en": quote}
            ],
            "features": [
                {
                    "feature_id": "F3.2",
                    "h_code": "H2",
                    "evidence_quote": quote,
                    "evidence_quote_en": quote,
                }
            ],
            "failure_summary": "错误映射。",
            "failure_summary_en": "The mapping is incorrect.",
            "causal_analysis": ["只有分数。", "没有工具契约证据。"],
            "causal_analysis_en": ["Only a score is present.", "No tool evidence exists."],
            "ablation_test": "不适用",
            "ablation_test_en": "Not applicable",
        },
    }

    validation = validate_response(response, task_id="task-1", ground_text=quote)

    assert validation["ok"] is False
    assert "invalid H/F pair H2+F3.2" in validation["errors"]


def test_report_summarizes_no_code_tasks_and_csv_keeps_them(tmp_path) -> None:
    attributed = {
        "task_id": "task-with-code",
        "score": 0.5,
        "outcome": "部分通过",
        "repeat_status": "两轮一致",
        "resolution": "双轮一致",
        "final_response": {
            "codes": [
                {
                    "code": "M1",
                    "code_label": "M1-Hallucination",
                    "evidence_quote": "unsupported value",
                    "evidence_quote_en": "unsupported value",
                }
            ],
            "features": [],
            "failure_summary": "模型编造了数值。",
            "failure_summary_en": "The model invented a value.",
            "ablation_test": "不适用",
            "ablation_test_en": "Not applicable",
        },
    }
    no_code = {
        "task_id": "task-without-code",
        "score": 1.0,
        "outcome": "通过",
        "repeat_status": "两轮一致",
        "resolution": "双轮一致",
        "final_response": {
            "codes": [],
            "features": [],
            "failure_summary": "无故障。",
            "failure_summary_en": "No attributable failure was found.",
            "ablation_test": "不适用",
            "ablation_test_en": "Not applicable",
        },
    }
    summary = {
        "attribution_model": "qwen3.7-max",
        "code_counts": {"M1": 1},
        "feature_counts": {},
        "independent_rounds": 2,
        "stable_tasks": 2,
        "task_count": 2,
        "adjudicated_tasks": 0,
        "successful_api_calls": 4,
        "api_calls": 4,
        "valid_api_outputs": 4,
        "response_ids_recorded": 4,
        "total_tokens": 100,
    }

    report_zh = render_report(summary, [attributed, no_code], language="zh")
    report_en = render_report(summary, [attributed, no_code], language="en")
    csv_path = tmp_path / "attributions.csv"
    write_attribution_csv([attributed, no_code], csv_path)
    with csv_path.open(encoding="utf-8", newline="") as source:
        csv_rows = list(csv.DictReader(source))

    assert "task-with-code" in report_zh
    assert "task-without-code" in report_zh
    assert "| `task-without-code` | 1.00 | 无 | 无 | 无 |" in report_zh
    assert "结论来源" not in report_zh
    assert "无错误码任务：`1`" in report_zh
    assert "| `task-without-code` | 1.00 | None | None | None |" in report_en
    assert CJK_PATTERN.search(report_en) is None
    assert [row["task_id"] for row in csv_rows] == ["task-with-code", "task-without-code"]
    assert csv_rows[1]["codes"] == ""
    assert csv_rows[0]["outcome"] == "Partially passed"
    assert csv_rows[1]["failure_summary"] == "None"
    assert CJK_PATTERN.search(csv_path.read_text(encoding="utf-8")) is None
    assert "resolution" not in csv_rows[0]
