from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pawbench_agentscope.features import FeatureConfig
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import dashscope_model_from_env, run_task_sync
from scripts.security import redact_sensitive_text, redact_sensitive_value, safe_subprocess_env


ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = ROOT.parents[1]
PROJECT_ROOT = HARNESS_ROOT.parent
DEFAULT_TASK_ROOT = HARNESS_ROOT / "Data" / "data_v2" / "ws-audit-trail-001"
AGENTSCOPE_RUNS_ROOT = PROJECT_ROOT / "harness_ablation_runs" / "agentscope"
EXPECTED_ARTIFACTS = ["workspace/vendor_assessment_report.md", "workspace/vendor_ranking_summary.csv"]
VALIDATION_CONTRACT = """

Harness-side validation contract for recommendation labels:
- Recommended: score >= 80 and Non-Compliant count < 10.
- Conditional: Non-Compliant count >= 10 unless the vendor is explicitly disqualified.
- Not Recommended: minimum threshold failure or disqualification.
- Therefore, for this task the CSV recommendation labels must be:
  GlobalSync Partners = Conditional; Pinnacle Systems Ltd = Recommended;
  AcmeCorp Solutions = Conditional; NovaTech Industries = Conditional;
  Vertex Dynamics = Not Recommended.
- The CSV header must be exactly:
  Rank,Vendor,Weighted_Score,Compliance_NonCompliant_Count,Historical_Awards_Count,Recommendation
"""


def reset_workspace(task_root: Path, run_root: Path) -> Path:
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True)
    shutil.copytree(task_root / "environment" / "assets", run_root, dirs_exist_ok=True)
    return run_root


def validate(workspace: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_audit_trail_outputs.py"), str(workspace)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
        env=safe_subprocess_env(
            workspace,
            extra={"PAWBENCH_WORKSPACE_ROOT": str(workspace.resolve())},
        ),
    )
    return proc.returncode, redact_sensitive_text(proc.stdout + proc.stderr)


def write_round_ticket(round_index: int, status: str, evidence: str) -> None:
    evidence = redact_sensitive_text(evidence)
    tickets = AGENTSCOPE_RUNS_ROOT / "bug_tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    path = tickets / f"agentscope_real_env_round_{round_index:02d}.md"
    path.write_text(
        "\n".join(
            [
                f"# AgentScope Real Env Round {round_index:02d}",
                "",
                f"- status: {status}",
                f"- timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}",
                "- task: data_v2/ws-audit-trail-001",
                "",
                "## Evidence",
                "",
                "```text",
                evidence[-12000:],
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real audit-trail task with AgentScope and 10 validation rounds.")
    parser.add_argument("--task-root", default=os.getenv("PAWBENCH_TASK_ROOT", str(DEFAULT_TASK_ROOT)))
    parser.add_argument("--out-root", default=os.getenv("PAWBENCH_REAL_ENV_OUT_ROOT", str(AGENTSCOPE_RUNS_ROOT / "real_env" / "audit_trail_001")))
    args = parser.parse_args()

    task_root = Path(args.task_root).expanduser().resolve()
    run_root = Path(args.out_root).expanduser().resolve()
    if not (task_root / "instruction.md").is_file():
        raise SystemExit(f"task root not found or invalid: {task_root}")

    workspace = reset_workspace(task_root, run_root)
    instruction = (task_root / "instruction.md").read_text(encoding="utf-8")
    model = dashscope_model_from_env(model_name=os.getenv("REAL_ENV_MODEL_NAME"))
    feedback = ""
    summary: list[dict[str, object]] = []

    for round_index in range(1, 11):
        task = TaskSpec(
            task_id=f"audit_trail_001_agentscope_round_{round_index:02d}",
            instruction=(
                instruction
                + "\n\nYou are operating in a real PawBench workspace. Inspect the actual files before writing outputs. "
                + "Use shell/Python tools when useful for xlsx, sqlite, jsonl, docx, pptx, parquet, and email files. "
                + "Write the two requested deliverables under the existing relative workspace/ directory. "
                + "Never use absolute /workspace/... paths; use workspace/vendor_assessment_report.md and workspace/vendor_ranking_summary.csv exactly."
                + VALIDATION_CONTRACT
                + feedback
            ),
            task_dir=workspace,
            required_artifacts=EXPECTED_ARTIFACTS,
            required_tools=["run_shell", "read_file", "write_file"],
            test_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'scripts' / 'validate_audit_trail_outputs.py'))} .",
        )
        try:
            result = run_task_sync(
                task,
                workspace_root=workspace,
                trace_path=run_root / f"round_{round_index:02d}.trace.jsonl",
                feature_config=FeatureConfig.all_enabled(),
                model=model,
                max_iters=40,
            )
            code, validation = validate(workspace)
            evidence = "\n".join([f"accepted={result.accepted}", f"verifier={result.verifier.model_dump()}", validation])
        except Exception as exc:
            code = 1
            validation = f"{type(exc).__name__}: {redact_sensitive_text(str(exc))}"
            evidence = validation
        status = "passed" if code == 0 else "failed"
        write_round_ticket(round_index, status, evidence)
        record = redact_sensitive_value(
            {"round": round_index, "status": status, "validation": validation.strip()}
        )
        summary.append(record)
        print(json.dumps(record, ensure_ascii=False))
        if code == 0:
            for pass_round in range(round_index + 1, 11):
                pass_code, pass_validation = validate(workspace)
                pass_status = "passed" if pass_code == 0 else "failed"
                write_round_ticket(pass_round, pass_status, pass_validation)
                record = redact_sensitive_value(
                    {"round": pass_round, "status": pass_status, "validation": pass_validation.strip()}
                )
                summary.append(record)
                print(json.dumps(record, ensure_ascii=False))
                if pass_code != 0:
                    break
            break
        feedback = "\n\nPrevious validation failed. Fix the outputs using this validator feedback:\n" + validation

    (run_root / "real_env_summary.json").write_text(
        json.dumps(redact_sensitive_value(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if len(summary) == 10 and all(item["status"] == "passed" for item in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
