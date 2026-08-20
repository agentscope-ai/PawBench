from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pawbench_agentscope.features import FeatureConfig
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import dashscope_model_from_env, run_task_sync


ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = ROOT.parents[1]
PROJECT_ROOT = HARNESS_ROOT.parent
RUN_ROOT = PROJECT_ROOT / "harness_ablation_runs" / "agentscope" / "api_smoke"


def main() -> int:
    workspace = RUN_ROOT / "workspace_root"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    task = TaskSpec(
        task_id="api_smoke",
        instruction=(
            "Use tools to create answer.txt with exactly this single line: agentscope-ok. "
            "Do not write outside the workspace root."
        ),
        task_dir=workspace,
        required_artifacts=["answer.txt"],
        hidden_contract={"artifact_text": {"answer.txt": "agentscope-ok"}},
    )
    result = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=RUN_ROOT / "trace.jsonl",
        feature_config=FeatureConfig.all_enabled(),
        model=dashscope_model_from_env(),
        max_iters=8,
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
