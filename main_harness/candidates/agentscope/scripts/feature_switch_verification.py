from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse

from pawbench_agentscope.features import FEATURE_IDS, FeatureConfig
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import run_task_sync


ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = ROOT.parents[1]
PROJECT_ROOT = HARNESS_ROOT.parent
OUT = PROJECT_ROOT / "harness_ablation_runs" / "agentscope" / "feature_switch_verification"
ESCAPE_FILE = Path("/tmp/pawbench_agentscope_escape_probe.txt")


class OneToolCallModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self, command: str, final: str = "done") -> None:
        super().__init__(credential=None, model="one-tool-call-model", parameters=self.Parameters(), stream=False, max_retries=0)
        self.command = command
        self.final = final

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        has_result = any(any(getattr(block, "type", "") == "tool_result" for block in msg.content) for msg in messages)
        if not has_result:
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="call-bash",
                        name="Bash",
                        input=json.dumps({"command": self.command, "description": "feature switch probe"}),
                    )
                ],
                is_last=True,
            )
        return ChatResponse(content=[TextBlock(text=self.final)], is_last=True)


def reset_case(name: str) -> Path:
    path = OUT / name / "workspace_root"
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def trace_types(path: Path) -> list[str]:
    return [json.loads(line)["type"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def has_tool_error(summary: dict) -> bool:
    if summary.get("tool_errors"):
        return True
    return any(isinstance(value, dict) and bool(value.get("tool_errors")) for value in summary.values())


def run_case(name: str, config: FeatureConfig, command: str, *, task: TaskSpec | None = None) -> dict:
    workspace = reset_case(name)
    task = task or TaskSpec(
        task_id=name,
        instruction="Use tools if available.",
        task_dir=workspace,
        required_artifacts=["workspace/answer.txt"],
        hidden_contract={"artifact_text": {"workspace/answer.txt": "expected"}},
    )
    task.task_dir = workspace
    trace = OUT / name / "trace.jsonl"
    result = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=trace,
        feature_config=config,
        model=OneToolCallModel(command),
        max_iters=5,
    )
    return {
        "case": name,
        "accepted": result.accepted,
        "verifier_ok": result.verifier.ok,
        "missing": result.verifier.missing_artifacts,
        "failed_tests": result.verifier.failed_tests,
        "artifact_exists": (workspace / "workspace" / "answer.txt").exists(),
        "trace": str(trace),
        "events": trace_types(trace),
        "runtime_summary": result.runtime_summary,
    }


def semantic_task(name: str, workspace: Path) -> TaskSpec:
    validator = OUT / name / "validator.py"
    validator.parent.mkdir(parents=True, exist_ok=True)
    validator.write_text("raise SystemExit(7)\n", encoding="utf-8")
    return TaskSpec(
        task_id=name,
        instruction="Create workspace/answer.txt.",
        task_dir=workspace,
        required_artifacts=["workspace/answer.txt"],
        test_command=f"{sys.executable} {validator}",
    )


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    if ESCAPE_FILE.exists():
        ESCAPE_FILE.unlink()

    all_features = FeatureConfig.all_enabled()
    without_shell = FeatureConfig.controlled_off("F2.2", target="run_shell")
    without_verification_gate = FeatureConfig(enabled=set(FEATURE_IDS) - {"F4.3"})

    cases = []
    cases.append(run_case("all_features_tool_executes", all_features, "mkdir -p workspace && printf expected > workspace/answer.txt"))
    cases.append(run_case("without_F2_2_shell_hidden", without_shell, "mkdir -p workspace && printf expected > workspace/answer.txt"))
    cases.append(run_case("workspace_guard_blocks_escape", all_features, f"printf escaped > {ESCAPE_FILE}"))

    semantic_workspace = reset_case("without_F4_3_verification_gate")
    semantic = semantic_task("without_F4_3_verification_gate", semantic_workspace)
    cases.append(
        run_case(
            "without_F4_3_verification_gate",
            without_verification_gate,
            "mkdir -p workspace && printf expected > workspace/answer.txt",
            task=semantic,
        )
    )

    summary = {
        "tool_call_executes": cases[0]["accepted"] and cases[0]["artifact_exists"],
        "selected_tool_can_be_hidden": (not cases[1]["accepted"]) and (not cases[1]["artifact_exists"]),
        "workspace_guard_blocks_escape": (not ESCAPE_FILE.exists()) and has_tool_error(cases[2]["runtime_summary"]),
        "verification_reports_but_does_not_gate": cases[3]["accepted"] and not cases[3]["verifier_ok"],
        "cases": cases,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(value for key, value in summary.items() if key != "cases") else 1


if __name__ == "__main__":
    raise SystemExit(main())
