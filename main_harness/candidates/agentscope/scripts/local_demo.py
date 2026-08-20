from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope.model import ChatResponse, ChatModelBase
from agentscope.message import TextBlock, ToolCallBlock

from pawbench_agentscope.features import FeatureConfig
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import run_task_sync


ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = ROOT.parents[1]
PROJECT_ROOT = HARNESS_ROOT.parent
RUN_ROOT = PROJECT_ROOT / "harness_ablation_runs" / "agentscope" / "local_demo"


class ScriptedToolModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(credential=None, model="scripted-tool-model", parameters=self.Parameters(), stream=False, max_retries=0)

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        has_result = any(any(getattr(block, "type", "") == "tool_result" for block in msg.content) for msg in messages)
        if not has_result:
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="write-answer",
                        name="Bash",
                        input='{"command": "mkdir -p workspace && printf expected > workspace/answer.txt", "description": "write expected answer"}',
                    )
                ],
                is_last=True,
            )
        return ChatResponse(content=[TextBlock(text="Created workspace/answer.txt")], is_last=True)


def main() -> int:
    workspace = RUN_ROOT / "workspace_root"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    task = TaskSpec(
        task_id="local_demo",
        instruction="Create workspace/answer.txt containing exactly expected.",
        task_dir=workspace,
        required_artifacts=["workspace/answer.txt"],
        hidden_contract={"artifact_text": {"workspace/answer.txt": "expected"}},
    )
    result = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=RUN_ROOT / "trace.jsonl",
        feature_config=FeatureConfig.all_enabled(),
        model=ScriptedToolModel(),
        max_iters=5,
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
