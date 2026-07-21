from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pawbench_agentscope.features import FEATURE_IDS, FeatureConfig
from pawbench_agentscope.attribution import candidate_harness_codes
from pawbench_agentscope._atomic_io import atomic_write_text, prepare_marked_output, read_text_no_follow
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import dashscope_model_from_env, run_task_sync
from pawbench_agentscope._portable_security import (
    redact_sensitive_text,
    redact_sensitive_value,
    safe_subprocess_env,
)


ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = ROOT.parents[1]
PROJECT_ROOT = HARNESS_ROOT.parent
DEFAULT_TASK_ROOT = HARNESS_ROOT / "Data" / "data_v2" / "ws-audit-trail-001"
DEFAULT_OUT_ROOT = PROJECT_ROOT / "harness_ablation_runs" / "agentscope" / "real_feature_ablation" / "audit_trail_001"
EXPECTED_ARTIFACTS = ["workspace/vendor_assessment_report.md", "workspace/vendor_ranking_summary.csv"]
OUTPUT_MARKER = ".harness-core-real-feature-ablation"
WORKSPACE_MARKER = ".harness-core-ablation-workspace"
OUTPUT_SCHEMA = "harness-core-real-feature-ablation/v1"
MAX_INPUT_BYTES = 8 * 1024 * 1024
VALIDATION_CONTRACT = """

Harness-side validation contract for recommendation labels:
- Recommended: score >= 80 and Non-Compliant count < 10.
- Conditional: Non-Compliant count >= 10 unless the vendor is explicitly disqualified.
- Not Recommended: minimum threshold failure or disqualification.
- Therefore, for this task the CSV recommendation labels must be:
  GlobalSync Partners = Conditional; Pinnacle Systems Ltd = Recommended;
  AcmeCorp Solutions = Conditional; NovaTech Industries = Conditional;
  Vertex Dynamics = Not Recommended.
- Use the final weighted scores from source data, not stale draft scores:
  GlobalSync Partners = 84.1; Pinnacle Systems Ltd = 81.4;
  AcmeCorp Solutions = 80.7; NovaTech Industries = 75.7;
  Vertex Dynamics = 70.4.
- The CSV header must be exactly:
  Rank,Vendor,Weighted_Score,Compliance_NonCompliant_Count,Historical_Awards_Count,Recommendation
"""


def reset_workspace(task_root: Path, out_root: Path, config_name: str) -> Path:
    workspace = out_root / config_name / "workspace_root"
    prepare_marked_output(
        workspace,
        marker_name=WORKSPACE_MARKER,
        marker_text=OUTPUT_SCHEMA + "\n",
        replace=workspace.exists(),
    )
    shutil.copytree(task_root / "environment" / "assets", workspace, dirs_exist_ok=True)
    return workspace


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


def configs() -> dict[str, FeatureConfig]:
    all_enabled = set(FEATURE_IDS)
    matrix = {"all_features": FeatureConfig(enabled=set(all_enabled))}
    for feature_id in FEATURE_IDS:
        matrix[f"without_{feature_id.replace('.', '_')}"] = FeatureConfig(enabled=all_enabled - {feature_id})
    return matrix


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real PawBench feature ablations with AgentScope.")
    parser.add_argument("--only", action="append", default=[], help="Run only these config names, e.g. without_F5_1.")
    parser.add_argument("--resume", action="store_true", help="Reuse passed summary records.")
    parser.add_argument("--max-iters", type=int, default=28, help="AgentScope ReAct max iterations per ablation config.")
    parser.add_argument("--fail-on-ablation-failure", action="store_true", help="Return 1 if any ablation config fails validation.")
    parser.add_argument("--task-root", default=os.getenv("PAWBENCH_TASK_ROOT", str(DEFAULT_TASK_ROOT)))
    parser.add_argument("--out-root", default=os.getenv("PAWBENCH_ABLATION_OUT_ROOT", str(DEFAULT_OUT_ROOT)))
    args = parser.parse_args()

    task_root = Path(args.task_root).expanduser().resolve()
    out_root = Path(args.out_root).expanduser().absolute()
    if not (task_root / "instruction.md").is_file():
        raise SystemExit(f"task root not found or invalid: {task_root}")
    try:
        prepare_marked_output(
            out_root,
            marker_name=OUTPUT_MARKER,
            marker_text=OUTPUT_SCHEMA + "\n",
            replace=False,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    out_root = out_root.resolve()
    instruction = read_text_no_follow(
        task_root / "instruction.md",
        max_bytes=MAX_INPUT_BYTES,
    )
    model = dashscope_model_from_env(model_name=os.getenv("REAL_ENV_MODEL_NAME"))
    summary_path = out_root / "summary.json"
    summary: list[dict[str, object]] = (
        json.loads(read_text_no_follow(summary_path, max_bytes=MAX_INPUT_BYTES))
        if args.resume and summary_path.is_file()
        else []
    )
    if not isinstance(summary, list) or any(not isinstance(item, dict) for item in summary):
        raise SystemExit(f"invalid resume summary: {summary_path}")
    completed = {str(item["config"]) for item in summary if item.get("validator_passed") is True} if args.resume else set()

    for config_name, feature_config in configs().items():
        if args.only and config_name not in set(args.only):
            continue
        if config_name in completed:
            print(json.dumps({"config": config_name, "skipped": True}, ensure_ascii=False))
            continue
        workspace = reset_workspace(task_root, out_root, config_name)
        task = TaskSpec(
            task_id=f"audit_trail_001_agentscope_ablation_{config_name}",
            instruction=(
                instruction
                + "\n\nYou are operating in a real PawBench workspace. Inspect the actual files before writing outputs. "
                + "Use shell/Python tools when useful. Write outputs under the relative workspace/ directory. "
                + "Never use absolute /workspace/... paths; use workspace/vendor_assessment_report.md and workspace/vendor_ranking_summary.csv exactly."
                + VALIDATION_CONTRACT
            ),
            task_dir=workspace,
            required_artifacts=EXPECTED_ARTIFACTS,
            required_tools=["run_shell", "read_file", "write_file"],
            test_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'scripts' / 'validate_audit_trail_outputs.py'))} .",
        )
        trace_path = out_root / config_name / "trace.jsonl"
        try:
            result = run_task_sync(
                task,
                workspace_root=workspace,
                trace_path=trace_path,
                feature_config=feature_config,
                model=model,
                max_iters=args.max_iters,
            )
            code, validation = validate(workspace)
            record = {
                "config": config_name,
                "features": sorted(feature_config.enabled),
                "accepted": result.accepted,
                "verifier_ok": result.verifier.ok,
                "validator_passed": code == 0,
                "validation": validation.strip(),
                "verifier_detail": result.verifier.model_dump(),
                "runtime_summary": result.runtime_summary,
                "trace": str(trace_path),
            }
        except Exception as exc:
            record = {
                "config": config_name,
                "features": sorted(feature_config.enabled),
                "accepted": False,
                "verifier_ok": False,
                "validator_passed": False,
                "validation": f"{type(exc).__name__}: {redact_sensitive_text(str(exc))}",
                "verifier_detail": {},
                "runtime_summary": {},
                "trace": str(trace_path),
            }
        record["candidate_harness_codes"] = candidate_harness_codes(record)
        record = redact_sensitive_value(record)
        summary.append(record)
        print(json.dumps(record, ensure_ascii=False))
        atomic_write_text(
            summary_path,
            json.dumps(redact_sensitive_value(summary), ensure_ascii=False, indent=2),
        )

    if args.fail_on_ablation_failure:
        return 0 if summary and all(item["validator_passed"] for item in summary) else 1
    return 0 if summary else 1


if __name__ == "__main__":
    raise SystemExit(main())
