from __future__ import annotations

import csv
import re
import sys
from pathlib import Path


EXPECTED_ROWS = [
    ("1", "GlobalSync Partners", "84.1", "11", "44", "Conditional"),
    ("2", "Pinnacle Systems Ltd", "81.4", "7", "46", "Recommended"),
    ("3", "AcmeCorp Solutions", "80.7", "10", "27", "Conditional"),
    ("4", "NovaTech Industries", "75.7", "13", "35", "Conditional"),
    ("5", "Vertex Dynamics", "70.4", "13", "40", "Not Recommended"),
]


def fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def main() -> int:
    if len(sys.argv) != 2:
        return fail("usage: validate_audit_trail_outputs.py <workspace-root>")
    root = Path(sys.argv[1])
    report = root / "workspace" / "vendor_assessment_report.md"
    csv_path = root / "workspace" / "vendor_ranking_summary.csv"
    if not report.is_file():
        return fail("missing workspace/vendor_assessment_report.md")
    if not csv_path.is_file():
        return fail("missing workspace/vendor_ranking_summary.csv")

    report_text = report.read_text(encoding="utf-8", errors="replace")
    required_report_patterns = [
        r"GlobalSync Partners[\s\S]{0,200}84\.1",
        r"Pinnacle Systems Ltd[\s\S]{0,200}81\.4",
        r"AcmeCorp Solutions[\s\S]{0,200}80\.7",
        r"NovaTech Industries[\s\S]{0,200}75\.7",
        r"Vertex Dynamics[\s\S]{0,200}70\.4",
        r"Non[- ]?Compliant",
        r"historical award",
        r"email_thread_procurement\.eml|email thread",
        r"meeting_notes_2026Q1\.db|meeting notes",
        r"vendor_proposals_v2\.xlsx|spreadsheet",
        r"83\.2|81\.5|79\.8",
        r"stale|vendor_comparison_draft\.rtf|archived",
    ]
    for pattern in required_report_patterns:
        if not re.search(pattern, report_text, re.IGNORECASE):
            return fail(f"report missing pattern: {pattern}")

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    expected_header = [
        "Rank",
        "Vendor",
        "Weighted_Score",
        "Compliance_NonCompliant_Count",
        "Historical_Awards_Count",
        "Recommendation",
    ]
    if not rows or rows[0] != expected_header:
        return fail(f"bad CSV header: {rows[0] if rows else '<empty>'}")
    if len(rows) != 6:
        return fail(f"CSV should have 6 lines, found {len(rows)}")
    normalized = [tuple(cell.strip() for cell in row) for row in rows[1:]]
    if normalized != EXPECTED_ROWS:
        return fail(f"CSV rows differ: {normalized}")

    print("PASS: audit_trail_001 deterministic validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
