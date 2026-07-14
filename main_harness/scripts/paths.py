from __future__ import annotations

from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = HARNESS_ROOT.parent

BACKUP_ROOT = PROJECT_ROOT / "backup"
ARTIFACTS_ROOT = BACKUP_ROOT / "project_history" / "artifacts"
LEGACY_ARTIFACTS_ROOT = ARTIFACTS_ROOT / "legacy_generated"

RUN_RECORDS_ROOT = PROJECT_ROOT / "run_records"
HARNESS_ABLATION_RUNS_ROOT = PROJECT_ROOT / "harness_ablation_runs"
ENGINEERING_RECORDS_ROOT = BACKUP_ROOT / "engineering_records"

# Intermediate work stays under backup so it does not clutter the project root.
HARNESS_WORK_ROOT = ENGINEERING_RECORDS_ROOT / "main_harness"
REASONING_WORK_ROOT = ENGINEERING_RECORDS_ROOT / "main_reasoning"
AGENTSCOPE_RUNS_ROOT = HARNESS_ABLATION_RUNS_ROOT / "agentscope"
BUG_TICKETS_ROOT = BACKUP_ROOT / "project_history" / "bug_tickets"

TASK_DATA_ROOT = HARNESS_ROOT / "Data" / "data_v2"
ATTRIBUTION_ROOT = PROJECT_ROOT / "main_reasoning"

OUTPUTS_ROOT = HARNESS_ABLATION_RUNS_ROOT
ABLATION_RUNS_ROOT = HARNESS_ABLATION_RUNS_ROOT

# Stable deliverables and live ablation traces stay outside the source tree.
SCRIPT_OUTPUTS_ROOT = HARNESS_WORK_ROOT / "script_outputs"
OUTPUT_RESULTS = SCRIPT_OUTPUTS_ROOT / "results"
OUTPUT_REPORTS = SCRIPT_OUTPUTS_ROOT / "reports"
OUTPUT_FIGURES = SCRIPT_OUTPUTS_ROOT / "figures"
