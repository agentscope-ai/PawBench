from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agentscope.message import TextBlock, ToolCallBlock  # noqa: E402
from agentscope.model import ChatModelBase, ChatResponse  # noqa: E402

from pawbench_agentscope.features import FEATURE_IDS, FeatureConfig, TAXONOMY_VERSION  # noqa: E402
from pawbench_agentscope.models import TaskSpec  # noqa: E402
from pawbench_agentscope.runtime.agentscope_runner import run_task_sync  # noqa: E402


OUT = PROJECT_ROOT / "harness_ablation_runs" / "agentscope" / "v2_ablation_matrix"
EXPECTED = "v2 ablation expected"


def has_tool_result(messages: list) -> bool:
    return any(
        any(getattr(block, "type", "") == "tool_result" for block in message.content)
        for message in messages
    )


class MatrixModel(ChatModelBase):
    class Parameters(ChatModelBase.Parameters):
        pass

    def __init__(self, mode: str) -> None:
        super().__init__(
            credential=None,
            model=f"v2-ablation-{mode}",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )
        self.mode = mode

    async def _call_api(self, model: str, messages: list, tools: list[dict] | None = None, tool_choice=None, **kwargs):
        prompt = "\n".join(str(message) for message in messages)
        if has_tool_result(messages):
            return ChatResponse(content=[TextBlock(text='{"summary":"done"}')], is_last=True)
        if self.mode == "feedback_error":
            command = f"printf {json.dumps(EXPECTED)} > ../answer.txt"
        elif self.mode == "alias":
            command = f"printf {json.dumps(EXPECTED)} > /workspace/answer.txt"
        elif self.mode == "wrong":
            command = "printf wrong > answer.txt"
        elif self.mode == "context":
            value = EXPECTED if "V2_CONTEXT_MARKER" in prompt else "wrong"
            command = f"printf {json.dumps(value)} > answer.txt"
        else:
            command = f"printf {json.dumps(EXPECTED)} > answer.txt"
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id=f"call-{self.mode}",
                    name="Bash",
                    input=json.dumps({"command": command, "description": self.mode}),
                )
            ],
            is_last=True,
        )


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def payload_for(events: list[dict], event_type: str) -> dict | None:
    return next((event["payload"] for event in events if event["type"] == event_type), None)


def scenario(feature_id: str) -> tuple[str, dict[str, str]]:
    if feature_id == "F1.3":
        return "alias", {}
    if feature_id == "F2.3":
        return "feedback_error", {}
    if feature_id in {"F3.3", "F4.3"}:
        return "wrong", {}
    if feature_id == "F5.1":
        return "context", {"SKILL.md": "V2_CONTEXT_MARKER\n"}
    if feature_id == "F5.3":
        return "default", {"SKILL.md": ("MUST preserve V2_CONTEXT_MARKER and artifact constraints.\n" * 120)}
    return "default", {}


def feature_specific_check(feature_id: str, events: list[dict], result) -> tuple[bool, str]:
    event_types = [event["type"] for event in events]
    if feature_id == "F1.1":
        ok = "workspace_binding" not in event_types
    elif feature_id == "F1.2":
        ok = "preflight_skipped" in event_types
    elif feature_id == "F1.3":
        ok = payload_for(events, "isolation_policy")["mode"] == "minimal_safety_floor"
    elif feature_id == "F2.1":
        ok = payload_for(events, "action_contract")["pawbench_validation_enabled"] is False
    elif feature_id == "F2.2":
        availability = payload_for(events, "tool_availability")
        ok = availability["selected_hidden_tool"] == "run_shell" and "run_shell" not in availability["enabled_tools"]
    elif feature_id == "F2.3":
        ok = "raw_tool_error" in event_types and "normalized_tool_error" not in event_types
    elif feature_id == "F3.1":
        ok = payload_for(events, "completion_decision")["stop_reason"] == "framework_baseline"
    elif feature_id == "F3.2":
        ok = payload_for(events, "budget_policy")["mode"] == "enlarged_with_absolute_cap"
    elif feature_id == "F3.3":
        ok = "retry_start" not in event_types and result.verifier.ok is False
    elif feature_id == "F4.1":
        ok = "agentscope_event" not in event_types and "run_start" in event_types
    elif feature_id == "F4.2":
        ok = "state_artifact_delta" not in event_types
    elif feature_id == "F4.3":
        ok = result.verifier.ok is False and result.accepted is True
    elif feature_id == "F5.1":
        sources = payload_for(events, "context_assembly")["sources"]
        ok = not any(source.get("path") == "SKILL.md" for source in sources)
    elif feature_id == "F5.2":
        ok = "memory_disabled" in event_types and "memory_query" not in event_types
    elif feature_id == "F5.3":
        ok = payload_for(events, "compaction_result")["mode"] == "truncated"
    else:
        ok = False
    return ok, "feature-specific OFF behavior observed" if ok else "feature-specific OFF behavior missing"


def run_case(feature_id: str) -> dict:
    case_root = OUT / f"without_{feature_id.replace('.', '_')}"
    workspace = case_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    mode, fixtures = scenario(feature_id)
    for rel_path, content in fixtures.items():
        (workspace / rel_path).write_text(content, encoding="utf-8")
    task = TaskSpec(
        task_id=f"ablate_{feature_id}",
        instruction="Create answer.txt with the exact expected content.",
        task_dir=workspace,
        required_artifacts=["answer.txt"],
        required_tools=["run_shell"],
        hidden_contract={"artifact_text": {"answer.txt": EXPECTED}},
    )
    target = "run_shell" if feature_id == "F2.2" else None
    config = FeatureConfig.controlled_off(feature_id, target=target).model_copy(
        update={"compaction_limit_chars": 1_000, "runtime_timeout_seconds": 30.0}
    )
    trace_path = case_root / "trace.jsonl"
    result = run_task_sync(
        task,
        workspace_root=workspace,
        trace_path=trace_path,
        feature_config=config,
        model=MatrixModel(mode),
        max_iters=4,
    )
    events = rows(trace_path)
    enabled_ids = [event["payload"]["id"] for event in events if event["type"] == "feature_enabled"]
    off_events = [event for event in events if event["type"] == "feature_controlled_off" and event["payload"]["id"] == feature_id]
    specific_ok, detail = feature_specific_check(feature_id, events, result)
    checks = {
        "taxonomy_version": result.taxonomy_version == TAXONOMY_VERSION,
        "controlled_off_recorded": len(off_events) == 1,
        "disabled_feature_not_enabled": feature_id not in enabled_ids,
        "other_fourteen_enabled": len(enabled_ids) == 14,
        "feature_specific_behavior": specific_ok,
    }
    return {
        "feature_id": feature_id,
        "ok": all(checks.values()),
        "checks": checks,
        "detail": detail,
        "accepted": result.accepted,
        "verifier_ok": result.verifier.ok,
        "trace_path": str(trace_path),
    }


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    cases = [run_case(feature_id) for feature_id in FEATURE_IDS]
    summary = {
        "taxonomy_version": TAXONOMY_VERSION,
        "candidate": "AgentScope",
        "case_count": len(cases),
        "passed": sum(case["ok"] for case in cases),
        "failed": [case["feature_id"] for case in cases if not case["ok"]],
        "cases": cases,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("taxonomy_version", "case_count", "passed", "failed")}, indent=2))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
