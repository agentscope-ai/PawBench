from __future__ import annotations

from scripts.feature_taxonomy import display_code
from scripts.reporting import enrich_attribution, write_attribution_overview_chart


def test_full_code_labels_keep_raw_machine_codes() -> None:
    parsed = {
        "codes": [
            {"code": "M1", "evidence_quote": "unsupported claim"},
            {"code": "H2", "evidence_quote": "tool returned malformed output"},
        ]
    }

    enriched = enrich_attribution(parsed)

    assert display_code("M1") == "M1-Hallucination"
    assert enriched["codes"][0]["code"] == "M1"
    assert enriched["codes"][0]["code_label"] == "M1-Hallucination"
    assert enriched["code_labels"] == ["M1-Hallucination", "H2-ToolContract"]


def test_attribution_overview_is_created_even_when_all_counts_are_zero(tmp_path) -> None:
    output = tmp_path / "figures" / "attribution_summary.png"

    write_attribution_overview_chart({}, {}, output)

    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert output.stat().st_size > 30_000
