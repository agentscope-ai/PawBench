from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse

from pawbench_agentscope.features import FeatureConfig
from pawbench_agentscope.models import TaskSpec
from pawbench_agentscope.runtime.agentscope_runner import run_task_sync


EXPECTED_SKILL_ANSWER = "PawBench skill impact ok"
EXPECTED_RECOVERY_ANSWER = "PawBench recovery impact ok"


def has_tool_result(messages: list) -> bool:
    return any(any(getattr(block, "type", "") == "tool_result" for block in msg.content) for msg in messages)


class SkillSensitiveModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="skill-sensitive-model",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        prompt_view = "\n".join(str(message) for message in messages)
        if has_tool_result(messages):
            return ChatResponse(content=[TextBlock(text='{"summary":"done","artifacts":["answer.txt"],"confidence":1.0}')], is_last=True)
        content = EXPECTED_SKILL_ANSWER if "EXPECTED_ANSWER=PawBench skill impact ok" in prompt_view else "wrong answer"
        command = f"printf {json.dumps(content)} > answer.txt"
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id="write-answer",
                    name="Bash",
                    input=json.dumps({"command": command, "description": "write skill-sensitive answer"}),
                )
            ],
            is_last=True,
        )


class RecoverySensitiveModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=None,
            model="recovery-sensitive-model",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        prompt_view = "\n".join(str(message) for message in messages)
        recovered = "retry-good-path" in prompt_view
        if not has_tool_result(messages):
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="bad-path",
                        name="Bash",
                        input=json.dumps({"command": f"printf {json.dumps(EXPECTED_RECOVERY_ANSWER)} > ../answer.txt", "description": "bad path"}),
                    )
                ],
                is_last=True,
            )
        if "When a tool call fails" in prompt_view and not recovered:
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="retry-good-path",
                        name="Bash",
                        input=json.dumps({"command": f"printf {json.dumps(EXPECTED_RECOVERY_ANSWER)} > answer.txt", "description": "retry-good-path"}),
                    )
                ],
                is_last=True,
            )
        return ChatResponse(content=[TextBlock(text='{"summary":"stopped","artifacts":[],"confidence":0.2}')], is_last=True)


def run_skill_case(root: Path, iteration: int) -> dict[str, object]:
    workspace = root / f"iter_{iteration:02d}" / "skill"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "SKILL.md").write_text("EXPECTED_ANSWER=PawBench skill impact ok", encoding="utf-8")
    task = TaskSpec(
        task_id=f"skill_impact_{iteration:02d}",
        instruction="Create answer.txt using the discovered skill answer.",
        task_dir=workspace,
        required_artifacts=["answer.txt"],
        required_tools=["run_shell"],
        hidden_contract={"artifact_text": {"answer.txt": EXPECTED_SKILL_ANSWER}},
    )
    off = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=workspace / "off.trace.jsonl",
        feature_config=FeatureConfig(enabled={"F1.3", "F2.2", "F3.1", "F4.3"}),
        model=SkillSensitiveModel(),
        max_iters=4,
    )
    on = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=workspace / "on.trace.jsonl",
        feature_config=FeatureConfig(enabled={"F1.3", "F2.2", "F3.1", "F4.3", "F5.1"}),
        model=SkillSensitiveModel(),
        max_iters=4,
    )
    return {
        "case": "F5.1_context_assembly",
        "off_accepted": bool(off.accepted),
        "on_accepted": bool(on.accepted),
        "answer": (workspace / "answer.txt").read_text(encoding="utf-8"),
        "off_trace": str(workspace / "off.trace.jsonl"),
        "on_trace": str(workspace / "on.trace.jsonl"),
    }


def run_recovery_case(root: Path, iteration: int) -> dict[str, object]:
    task = TaskSpec(
        task_id=f"recovery_impact_{iteration:02d}",
        instruction="Create answer.txt with the recovery answer.",
        task_dir=root,
        required_artifacts=["answer.txt"],
        required_tools=["run_shell"],
        hidden_contract={"artifact_text": {"answer.txt": EXPECTED_RECOVERY_ANSWER}},
    )
    off_workspace = root / f"iter_{iteration:02d}" / "recovery_off"
    on_workspace = root / f"iter_{iteration:02d}" / "recovery_on"
    off_workspace.mkdir(parents=True, exist_ok=True)
    on_workspace.mkdir(parents=True, exist_ok=True)
    off = run_task_sync(
        task,
        workspace_root=off_workspace,
        trace_path=off_workspace / "trace.jsonl",
        feature_config=FeatureConfig(enabled={"F1.3", "F2.2", "F3.1", "F4.3"}),
        model=RecoverySensitiveModel(),
        max_iters=4,
    )
    on = run_task_sync(
        task,
        workspace_root=on_workspace,
        trace_path=on_workspace / "trace.jsonl",
        feature_config=FeatureConfig(enabled={"F1.3", "F2.2", "F2.3", "F3.1", "F4.3"}),
        model=RecoverySensitiveModel(),
        max_iters=4,
    )
    answer_path = on_workspace / "answer.txt"
    return {
        "case": "F2.3_structured_error_feedback",
        "off_accepted": bool(off.accepted),
        "on_accepted": bool(on.accepted),
        "answer": answer_path.read_text(encoding="utf-8") if answer_path.exists() else "",
        "off_trace": str(off_workspace / "trace.jsonl"),
        "on_trace": str(on_workspace / "trace.jsonl"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic answer-producing AgentScope feature impact smoke tests.")
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    if args.iterations < 10:
        raise SystemExit("--iterations must be at least 10")

    repo = Path(__file__).resolve().parents[1]
    out = repo / "tmp" / "feature_impact"
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for iteration in range(1, args.iterations + 1):
        for case_result in (run_skill_case(out, iteration), run_recovery_case(out, iteration)):
            results.append({"iteration": iteration, **case_result})
            if case_result["off_accepted"] is not False or case_result["on_accepted"] is not True:
                failures.append({"iteration": iteration, **case_result})

    summary = {"iterations": args.iterations, "cases": len(results), "failures": failures, "results": results}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"iterations": args.iterations, "cases": len(results), "failure_count": len(failures)}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
