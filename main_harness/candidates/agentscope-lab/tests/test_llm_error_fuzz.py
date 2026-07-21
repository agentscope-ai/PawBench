from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "llm_error_fuzz.py"
SPEC = importlib.util.spec_from_file_location("llm_error_fuzz", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


FIXTURES = {
    "HC_CONFIG_INVALID_FEATURE": ("ValueError", "unknown Feature ID F9.9"),
    "HC_INPUT_CONTRACT_INVALID": ("FileNotFoundError", "instruction file is absent"),
    "HC_PREFLIGHT_FAILED": ("RuntimeError", "preflight failed: workspace not writable"),
    "HC_PROVIDER_MODEL_NOT_FOUND": ("NotFoundError", "model not found"),
    "HC_PROVIDER_AUTH": ("AuthenticationError", "HTTP 401 unauthorized"),
    "HC_PROVIDER_RATE_LIMIT": ("RateLimitError", "HTTP 429 rate limit"),
    "HC_PROVIDER_UNAVAILABLE": ("APIConnectionError", "connection reset"),
    "HC_RUNTIME_TIMEOUT": ("TimeoutError", "runtime timeout"),
    "HC_RUNTIME_ERROR": ("RuntimeError", "unexpected scheduler state"),
}


def _payload() -> str:
    return json.dumps(
        {
            "cases": [
                {
                    "case_id": f"case-{index}",
                    "expected_code": code,
                    "error_type": values[0],
                    "error": values[1],
                    "runtime_context": {},
                }
                for index, (code, values) in enumerate(FIXTURES.items(), start=1)
            ]
        }
    )


def test_fixture_corpus_is_balanced_and_routes_all_codes() -> None:
    cases = MODULE.parse_cases(_payload(), cases_per_code=1)
    summary = MODULE.classify_cases(cases, model="fixture")
    assert summary["case_count"] == 9
    assert summary["matched_count"] == 9
    assert summary["all_matched"] is True
    assert summary["runtime_design_modified"] is False


def test_generator_target_code_cannot_leak_into_error_text() -> None:
    payload = json.loads(_payload())
    payload["cases"][0]["error"] = "HC_CONFIG_INVALID_FEATURE"
    with pytest.raises(ValueError, match="leaks the target code"):
        MODULE.parse_cases(json.dumps(payload), cases_per_code=1)


def test_generator_response_must_be_balanced() -> None:
    payload = json.loads(_payload())
    payload["cases"][0]["expected_code"] = "HC_RUNTIME_ERROR"
    with pytest.raises(ValueError, match="balance"):
        MODULE.parse_cases(json.dumps(payload), cases_per_code=1)


def test_generator_response_rejects_duplicate_json_keys() -> None:
    payload = _payload().replace(
        '"case_id": "case-1"',
        '"case_id": "shadow", "case_id": "case-1"',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON key: case_id"):
        MODULE.parse_cases(payload, cases_per_code=1)


def test_generator_response_rejects_nonfinite_json() -> None:
    payload = _payload().replace(
        '"runtime_context": {}',
        '"runtime_context": {"error": NaN}',
        1,
    )
    with pytest.raises(ValueError, match="non-finite JSON value"):
        MODULE.parse_cases(payload, cases_per_code=1)


def test_generator_unsafe_or_duplicate_case_ids_are_stably_normalized() -> None:
    payload = json.loads(_payload())
    payload["cases"][0]["case_id"] = "UPPER CASE/unsafe"
    payload["cases"][1]["case_id"] = "generated-0001"

    cases = MODULE.parse_cases(json.dumps(payload), cases_per_code=1)
    replayed = MODULE.parse_cases(json.dumps({"cases": cases}), cases_per_code=1)

    assert cases[0]["case_id"] == "generated-0001"
    assert cases[1]["case_id"] == "generated-0002"
    assert cases == replayed


def test_generation_splits_large_balanced_corpus_into_stable_batches() -> None:
    calls: list[tuple[int, int]] = []

    def caller(prompt: str, *, stage: str, iteration: int) -> str:
        assert stage == "fuzz"
        marker = "with exactly "
        size = int(prompt.split(marker, 1)[1].split(" ", 1)[0])
        calls.append((iteration, size))
        return json.dumps(
            {
                "cases": [
                    {
                        "case_id": f"case-{code_index}-{sample_index}",
                        "expected_code": code,
                        "error_type": "SyntheticError",
                        "error": f"synthetic {code_index}-{sample_index}",
                        "runtime_context": {},
                    }
                    for code_index, code in enumerate(MODULE.ERROR_CODES, start=1)
                    for sample_index in range(1, size + 1)
                ]
            }
        )

    cases = MODULE.generate_cases(caller, cases_per_code=6)

    assert calls == [(1, 4), (2, 2)]
    assert len(cases) == len(MODULE.ERROR_CODES) * 6
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert all(MODULE.SAFE_ID.fullmatch(case["case_id"]) for case in cases)


def test_generation_preserves_smaller_balanced_response_and_fills_remainder() -> None:
    calls: list[tuple[int, int]] = []

    def caller(prompt: str, *, stage: str, iteration: int) -> str:
        assert stage == "fuzz"
        requested = int(prompt.split("with exactly ", 1)[1].split(" ", 1)[0])
        returned = 2 if iteration == 1 else requested
        calls.append((iteration, requested))
        return json.dumps(
            {
                "cases": [
                    {
                        "case_id": f"case-{code_index}-{sample_index}",
                        "expected_code": code,
                        "error_type": "SyntheticError",
                        "error": f"synthetic {code_index}-{sample_index}",
                        "runtime_context": {},
                    }
                    for code_index, code in enumerate(MODULE.ERROR_CODES, start=1)
                    for sample_index in range(1, returned + 1)
                ]
            }
        )

    cases = MODULE.generate_cases(caller, cases_per_code=4)

    assert calls == [(1, 4), (2, 2)]
    assert len(cases) == len(MODULE.ERROR_CODES) * 4
    assert len({case["case_id"] for case in cases}) == len(cases)


def test_generation_splits_malformed_batch_before_failing() -> None:
    calls: list[tuple[int, int]] = []

    def caller(prompt: str, *, stage: str, iteration: int) -> str:
        assert stage == "fuzz"
        requested = int(prompt.split("with exactly ", 1)[1].split(" ", 1)[0])
        calls.append((iteration, requested))
        if iteration == 1:
            return "not-json"
        return json.dumps(
            {
                "cases": [
                    {
                        "case_id": f"case-{code_index}-{sample_index}",
                        "expected_code": code,
                        "error_type": "SyntheticError",
                        "error": f"synthetic {code_index}-{sample_index}",
                        "runtime_context": {},
                    }
                    for code_index, code in enumerate(MODULE.ERROR_CODES, start=1)
                    for sample_index in range(1, requested + 1)
                ]
            }
        )

    cases = MODULE.generate_cases(caller, cases_per_code=4)

    assert calls == [(1, 4), (2, 2), (3, 2)]
    assert len(cases) == len(MODULE.ERROR_CODES) * 4


def test_human_review_preserves_raw_result_and_explains_exclusion() -> None:
    cases = MODULE.parse_cases(_payload(), cases_per_code=1)
    cases[0]["error"] = "arbitrary SDK feature flag is unsupported"
    summary = MODULE.classify_cases(cases, model="fixture")
    assert summary["matched_count"] == 8
    review = MODULE.apply_human_review(
        summary,
        {
            "schema_version": MODULE.REVIEW_SCHEMA_VERSION,
            "exclusions": [
                {
                    "case_id": cases[0]["case_id"],
                    "reason": "Arbitrary SDK flags are not canonical Harness F identifiers.",
                }
            ],
        },
    )
    assert summary["matched_count"] == 8
    assert review["excluded_count"] == 1
    assert review["adjudicated_matched_count"] == 8
    assert review["all_adjudicated_matched"] is True


def test_human_review_cannot_hide_an_already_matched_case() -> None:
    summary = MODULE.classify_cases(
        MODULE.parse_cases(_payload(), cases_per_code=1),
        model="fixture",
    )
    with pytest.raises(ValueError, match="already matched"):
        MODULE.apply_human_review(
            summary,
            {
                "schema_version": MODULE.REVIEW_SCHEMA_VERSION,
                "exclusions": [{"case_id": "case-1", "reason": "not allowed"}],
            },
        )


def test_cli_refuses_output_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    link = tmp_path / "output"
    target.mkdir()
    link.symlink_to(target, target_is_directory=True)
    source = tmp_path / "cases.json"
    source.write_text(_payload(), encoding="utf-8")

    with pytest.raises(SystemExit):
        MODULE.main(
            [
                "--output",
                str(link),
                "--input",
                str(source),
                "--cases-per-code",
                "1",
            ]
        )

    assert not any(target.iterdir())


def test_cli_bounds_reused_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "too-large.json"
    source.write_text("x" * 16, encoding="utf-8")
    monkeypatch.setattr(MODULE, "MAX_INPUT_BYTES", 8)

    with pytest.raises(SystemExit):
        MODULE.main(
            [
                "--output",
                str(tmp_path / "output"),
                "--input",
                str(source),
                "--cases-per-code",
                "1",
            ]
        )
