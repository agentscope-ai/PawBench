from __future__ import annotations

from scripts.stress_test_reasoning_v2 import build_request_payload
from scripts.stress_test_real_v1_hf import render_report, summarize


def test_kimi_k2_7_code_uses_thinking_model_payload() -> None:
    payload = build_request_payload("kimi-k2.7-code", "classify this")

    assert payload["max_completion_tokens"] == 32_768
    assert "max_tokens" not in payload
    assert "thinking" not in payload
    assert "enable_thinking" not in payload
    assert "response_format" not in payload


def test_existing_models_keep_structured_non_thinking_payload() -> None:
    payload = build_request_payload("deepseek-v4-pro", "classify this")

    assert payload["max_tokens"] == 8_000
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert "max_completion_tokens" not in payload


def test_report_separates_all_codes_from_h_only_stability() -> None:
    results = []
    for repeat_no, model_code in enumerate(("M1", "M2", "M1"), start=1):
        results.append(
            {
                "job_id": f"job-{repeat_no}",
                "row_key": "row-1",
                "repeat_no": repeat_no,
                "response": {"ok": True},
                "validation": {
                    "ok": True,
                    "structural_valid": True,
                    "grounded": True,
                    "codes": ["H2", model_code],
                    "feature_ids": ["F2.1"],
                    "invalid_pairs": 0,
                    "ungrounded_quotes": 0,
                    "local_evidence_misses": 0,
                    "errors": [],
                },
            }
        )

    summary = summarize(
        results,
        sample=[{"run_group": "test", "harness": "test", "model": "test"}],
        prior_codes={},
        model="kimi-k2.7-code",
        repeat_size=1,
        repeat_rounds=3,
        workers=64,
    )
    report = render_report(summary)

    assert summary["code_stable_repeated_rows"] == 0
    assert summary["h_stable_repeated_rows"] == 1
    assert summary["hf_stable_repeated_rows"] == 1
    assert "# kimi-k2.7-code 真实 V1 H/F 超高压测试" in report
    assert "M1-Hallucination" in report
    assert "figures/attribution_summary.png" in report
    assert "全部 code 集稳定：`0/1`" in report
    assert "仅 H-code 集稳定：`1/1`" in report
