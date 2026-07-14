from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_three_judge_v1_showcase import EXAMPLES, job_identity
from scripts.feature_taxonomy import H_TO_FEATURES


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = (
    PROJECT_ROOT
    / "backup"
    / "engineering_records"
    / "main_harness"
    / "three_judge_real_v1_showcase_20260713"
)


def csv_values(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def test_showcase_uses_three_unique_fixed_inputs() -> None:
    assert len(EXAMPLES) == 3
    assert len({item["row_key"] for item in EXAMPLES}) == 3
    assert len({item["input_hash"] for item in EXAMPLES}) == 3
    assert all(len(item["input_hash"]) == 64 for item in EXAMPLES)


def test_final_harness_attributions_end_at_valid_features() -> None:
    for item in EXAMPLES:
        codes = csv_values(item["final_codes"])
        features = csv_values(item["final_features"])
        h_codes = {code for code in codes if code.startswith("H")}
        if h_codes:
            assert features
            assert all(
                any(feature in H_TO_FEATURES[h_code] for h_code in h_codes)
                for feature in features
            )
        else:
            assert not features


def test_cached_qwen_results_match_the_fixed_inputs() -> None:
    results_path = RUN_DIR / "qwen_results.jsonl"
    if not results_path.is_file():
        pytest.skip("requires local cached Qwen API results")
    results = {
        item["row_key"]: item
        for item in map(json.loads, results_path.read_text().splitlines())
    }
    assert set(results) == {item["row_key"] for item in EXAMPLES}
    for example in EXAMPLES:
        result = results[example["row_key"]]
        assert result["input_hash"] == example["input_hash"]
        assert result["job_id"] == job_identity(example["row_key"])[0]
        assert result["definition_seed"] == job_identity(example["row_key"])[1]
        assert result["response"]["ok"] is True
        assert result["validation"]["ok"] is True


def test_desktop_markdown_has_one_section_and_final_attribution_per_example() -> None:
    report_path = Path.home() / "Desktop" / "PawBench_V1_三模型归因_3例.md"
    if not report_path.is_file():
        pytest.skip("requires the local three-model showcase report")
    markdown = report_path.read_text()
    assert markdown.count("\n## 示例 ") == 3
    assert markdown.count("\n### 最终工程归因\n") == 3
    assert "置信度" not in markdown
    assert "1. **证据焦点**" not in markdown
    assert "4. **本地校验**" not in markdown
