from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from pawbench.agents.delegation import (
    detect_delegation,
    evaluate_multi_agent_run,
)
from pawbench.agents.multi_agent import (
    FORCED_DELEGATION_INSTRUCTION,
    MultiAgentConfig,
    augment_prompt_for_mode,
    build_harbor_kwargs,
    resolve_for_harness,
)
from pawbench.backend import TaskResult
from pawbench.harbor_v2.backend import HarborV2Backend
from pawbench.runner import _write_checkpoint
from run_bench import _build_multi_agent_config


def _config(mode: str) -> MultiAgentConfig:
    return MultiAgentConfig(
        enabled=mode != "single",
        mode=mode,
        run_mode=mode,
        requested_mode=mode,
    )


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ("disabled", "single"),
        ("auto", "adaptive"),
        ("subagents", "adaptive"),
        ("teams", "forced"),
        ("delegation", "forced"),
    ],
)
def test_legacy_modes_are_normalized(legacy: str, expected: str) -> None:
    cfg = MultiAgentConfig.from_dict({"enabled": True, "mode": legacy})
    assert cfg.run_mode == expected


def test_empty_config_is_single_and_limits_are_validated() -> None:
    assert MultiAgentConfig.from_dict({}).effective_mode == "single"
    disabled = MultiAgentConfig.from_dict({"enabled": False, "mode": "auto"})
    assert disabled.effective_mode == "single"
    with pytest.raises(ValueError, match="max_agents"):
        MultiAgentConfig.from_dict(
            {"enabled": True, "run_mode": "adaptive", "max_agents": 0}
        )


def test_cli_defaults_and_legacy_flag() -> None:
    base = dict(
        multi_agent_config=None,
        multi_agent_mode=None,
        multi_agent_max_agents=4,
        multi_agent_max_depth=2,
    )
    assert _build_multi_agent_config(
        Namespace(**base, multi_agent=False)
    ).run_mode == "single"
    assert _build_multi_agent_config(
        Namespace(**base, multi_agent=True)
    ).run_mode == "adaptive"


def test_harness_mode_mapping() -> None:
    forced = _config("forced")
    adaptive = _config("adaptive")

    claude_forced, _ = build_harbor_kwargs("claude-code", forced)
    claude_adaptive, _ = build_harbor_kwargs("claude-code", adaptive)
    assert claude_forced["agent_teams"] is True
    assert "agent_teams" not in claude_adaptive

    codex_forced, _ = build_harbor_kwargs("codex", forced)
    codex_adaptive, _ = build_harbor_kwargs("codex", adaptive)
    assert codex_forced["multi_agent"] is True
    assert codex_forced["multi_agent_force_delegation"] is True
    assert "multi_agent_force_delegation" not in codex_adaptive

    openclaw_forced, _ = build_harbor_kwargs("openclaw", forced)
    openclaw_adaptive, _ = build_harbor_kwargs("openclaw", adaptive)
    forced_subagents = openclaw_forced["openclaw_config"]["agents"]["defaults"]["subagents"]
    adaptive_subagents = openclaw_adaptive["openclaw_config"]["agents"]["defaults"]["subagents"]
    assert openclaw_forced["multi_agent"] is True
    assert openclaw_adaptive["multi_agent"] is True
    assert forced_subagents["delegationMode"] == "prefer"
    assert adaptive_subagents["delegationMode"] == "suggest"
    assert openclaw_forced["multi_agent_force_delegation"] is True
    assert "multi_agent_force_delegation" not in openclaw_adaptive


def test_unsupported_harness_falls_back_to_single() -> None:
    cfg = resolve_for_harness(_config("forced"), "harbor:qwenpaw")
    assert cfg.requested_mode == "forced"
    assert cfg.effective_mode == "single"
    assert cfg.enabled is False
    restored = MultiAgentConfig.from_dict(cfg.to_dict())
    assert restored.requested_mode == "forced"
    assert restored.effective_mode == "single"


def test_forced_prompt_instruction_is_only_added_for_forced() -> None:
    forced = augment_prompt_for_mode("Do the task.", _config("forced"))
    adaptive = augment_prompt_for_mode("Do the task.", _config("adaptive"))
    assert FORCED_DELEGATION_INSTRUCTION.strip() in forced
    assert adaptive == "Do the task."


@pytest.mark.parametrize(
    ("harness", "tool"),
    [
        ("claude-code", "Task"),
        ("claude-code", "Agent"),
        ("codex", "spawn_agent"),
        ("openclaw", "sessions_spawn"),
    ],
)
def test_detects_real_delegation_tool_calls(harness: str, tool: str) -> None:
    transcript = [{
        "type": "message",
        "message": {
            "role": "assistant",
            "content": [{"type": "toolCall", "name": tool, "arguments": {}}],
        },
    }]
    result = detect_delegation(transcript, harness)
    assert result["delegation_count"] == 1
    assert result["delegation_tools"] == [tool]


def test_detects_completed_codex_collab_spawn_once() -> None:
    transcript = [
        {
            "type": "item.started",
            "item": {
                "type": "collab_tool_call",
                "tool": "spawn_agent",
                "receiver_thread_ids": [],
                "status": "in_progress",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "collab_tool_call",
                "tool": "spawn_agent",
                "receiver_thread_ids": ["child-1"],
                "status": "completed",
            },
        },
    ]
    result = detect_delegation(transcript, "codex")
    assert result["delegation_count"] == 1
    assert result["delegation_tools"] == ["spawn_agent"]


def test_detects_flattened_codex_namespace_tool() -> None:
    transcript = [{
        "type": "function_call",
        "name": "multi_agent_v1__spawn_agent",
        "arguments": {},
    }]
    assert detect_delegation(transcript, "codex")["delegation_count"] == 1


def test_deduplicates_replayed_openclaw_tool_call_id() -> None:
    call = {
        "type": "toolCall",
        "id": "call_spawn_1",
        "name": "sessions_spawn",
        "arguments": {"task": "analyze"},
    }
    transcript = [
        {"message": {"content": [call]}},
        {"messagesSnapshot": [{"content": [call]}]},
    ]

    result = detect_delegation(transcript, "openclaw")

    assert result["delegation_count"] == 1


def test_tool_schema_is_not_counted_as_delegation() -> None:
    transcript = [{
        "type": "system",
        "tools": [{"name": "sessions_spawn"}],
    }]
    assert detect_delegation(
        transcript, "openclaw"
    )["delegation_count"] == 0


def test_forced_violation_requires_supported_harness() -> None:
    supported = evaluate_multi_agent_run(
        _config("forced"), "codex", [], None
    )
    unsupported = evaluate_multi_agent_run(
        _config("forced"), "qwenpaw", [], None
    )
    assert supported["forced_violation"] is True
    assert unsupported["effective_mode"] == "single"
    assert unsupported["forced_violation"] is False


def test_checkpoint_persists_multi_agent_metadata(tmp_path: Path) -> None:
    metadata = {
        "requested_mode": "forced",
        "effective_mode": "forced",
        "delegation_count": 0,
        "forced_violation": True,
    }
    result = TaskResult(
        task_id="T001",
        task_name="task",
        score=0.0,
        max_score=1.0,
        passed=False,
        grading_type="automated",
        breakdown={"multi_agent_forced_compliance": 0.0},
        notes="",
        execution_time=1.0,
        status="success",
        usage={},
        transcript_length=0,
        timed_out=False,
        multi_agent=metadata,
    )
    out = tmp_path / "result.json"
    _write_checkpoint(
        [result],
        {
            "model": "test/model",
            "agent_type": "harbor:codex",
            "multi_agent": _config("forced").to_dict(),
        },
        "pawbench",
        out,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["run_config"]["multi_agent"]["requested_mode"] == "forced"
    assert payload["summary"]["multi_agent"]["forced_violations"] == 1
    assert payload["results"][0]["multi_agent"] == metadata


def test_harbor_v2_forced_violation_zeroes_score(tmp_path: Path) -> None:
    backend = object.__new__(HarborV2Backend)
    backend._requires_user_sim = lambda _task: False
    backend._load_trajectory = lambda _trial_dir: []
    result = SimpleNamespace(
        verifier_result=SimpleNamespace(rewards={"reward": 1.0}),
        exception_info=None,
        compute_token_cost_totals=lambda: (0, 0, 0, 0),
    )
    task = SimpleNamespace(
        task_id="T001",
        name="task",
        frontmatter={},
    )

    mapped = backend._map_trial_result(
        task=task,
        result=result,
        trials_dir=tmp_path,
        trial_name="trial",
        agent_config={
            "agent_type": "harbor:codex",
            "multi_agent": _config("forced").to_dict(),
        },
        elapsed=1.0,
        verbose=False,
    )

    assert mapped.score == 0.0
    assert mapped.passed is False
    assert mapped.multi_agent["forced_violation"] is True
