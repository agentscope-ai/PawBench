from __future__ import annotations

import json
import runpy
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

from pawbench.harbor_v2.backend import HarborV2Backend
from pawbench.harbor_v2.scripted_user import (
    is_scripted_multi_turn,
    load_authored_messages,
    materialize_scripted_task,
)


def _write_task(root: Path, message_count: int = 3) -> SimpleNamespace:
    (root / "environment").mkdir(parents=True)
    (root / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (root / "task.toml").write_text(
        """
schema_version = "1.0"
[metadata]
category = "user_agent"
[agent]
timeout_sec = 60
[environment]
cpus = 1
""".lstrip(),
        encoding="utf-8",
    )
    (root / "instruction.md").write_text("original first turn\n", encoding="utf-8")
    lines = [
        f'{{"turn": {turn}, "role": "user", "content": "message {turn}"}}'
        for turn in range(1, message_count + 1)
    ]
    (root / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return SimpleNamespace(task_id="ua-test", task_dir=root)


def test_detects_authored_multi_turn(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task")
    assert [item["content"] for item in load_authored_messages(task.task_dir)] == [
        "message 1",
        "message 2",
        "message 3",
    ]
    assert is_scripted_multi_turn(task) is True


def test_single_authored_turn_is_not_wrapped(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task", message_count=1)
    assert is_scripted_multi_turn(task) is False


def test_materialization_preserves_source_and_adds_runtime_sidecar(
    tmp_path: Path,
) -> None:
    task = _write_task(tmp_path / "source")
    original_toml = (task.task_dir / "task.toml").read_text(encoding="utf-8")
    original_instruction = (task.task_dir / "instruction.md").read_text(encoding="utf-8")

    runtime = materialize_scripted_task(task, tmp_path / "runtime" / "ua-test")

    assert (task.task_dir / "task.toml").read_text(encoding="utf-8") == original_toml
    assert (task.task_dir / "instruction.md").read_text(encoding="utf-8") == original_instruction

    config = tomllib.loads((runtime / "task.toml").read_text(encoding="utf-8"))
    assert config["environment"]["mcp_servers"][0]["name"] == "user-sim"
    wrapped_instruction = (runtime / "instruction.md").read_text(encoding="utf-8")
    assert "start_conversation()" in wrapped_instruction
    assert "FIRST task action" in wrapped_instruction
    assert "normal assistant response is NOT delivered" in wrapped_instruction
    assert original_instruction.strip() not in wrapped_instruction
    compose = (runtime / "environment" / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "type: bind" in compose
    assert "source: ${HOST_AGENT_LOGS_PATH}" in compose
    assert (runtime / "environment" / ".pawbench-scripted-user" / "server.py").is_file()


def test_scripted_tasks_need_no_user_llm_credentials(tmp_path: Path, monkeypatch) -> None:
    task = _write_task(tmp_path / "task")
    task.metadata = {"category": "user_agent"}
    task.raw_config = {"environment": {}}
    monkeypatch.delenv("USER_SIM_MAX_TURNS", raising=False)

    backend = object.__new__(HarborV2Backend)
    assert backend._requires_user_sim(task) is True
    assert backend._build_environment_env(task, {}) == {}


def test_metadata_only_multi_turn_fails_before_trial_setup(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task", message_count=1)
    task.metadata = {"mode": "multi-turn"}
    task.raw_config = {"environment": {}}

    try:
        HarborV2Backend._validate_user_sim_wiring(task)
    except ValueError as exc:
        assert "has no user-sim sidecar wiring" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected metadata-only multi-turn task to fail fast")


def test_task_authored_user_sim_mcp_is_valid_wiring(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "task", message_count=1)
    task.metadata = {"mode": "multi-turn"}
    task.raw_config = {
        "environment": {"mcp_servers": [{"name": "user-sim", "url": "http://user-sim:8000/mcp"}]}
    }

    HarborV2Backend._validate_user_sim_wiring(task)


def test_scripted_server_uses_json_start_and_rejects_turn_zero_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    messages = tmp_path / "messages.jsonl"
    messages.write_text(
        '{"role": "user", "content": "first"}\n{"role": "user", "content": "second"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SCRIPTED_MESSAGES_PATH", str(messages))
    monkeypatch.setenv("USER_SIM_STATE_PATH", str(tmp_path / "state.json"))

    class FakeFastMCP:
        def __init__(self, _name: str) -> None:
            pass

        def tool(self):
            return lambda function: function

    monkeypatch.setitem(
        sys.modules,
        "fastmcp",
        SimpleNamespace(FastMCP=FakeFastMCP),
    )
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripted_user_server.py"))
    conversation = namespace["ScriptedConversation"](["first", "second"])

    started = json.loads(conversation.start())
    assert started["user_message"] == "first"
    assert started["conversation_over"] is False
    rejected = json.loads(conversation.end())
    assert rejected["conversation_over"] is False
    assert "error" in rejected

    sent = json.loads(conversation.send("reply"))
    assert sent["user_message"] == "second"
    ended = json.loads(conversation.end())
    assert ended["conversation_over"] is True


def test_qwenpaw_gets_cold_install_setup_budget() -> None:
    assert HarborV2Backend._agent_setup_timeout_seconds("qwenpaw") == 1500.0
    assert HarborV2Backend._agent_setup_timeout_seconds("hermes") == 1200.0
    assert HarborV2Backend._agent_setup_timeout_seconds("codex") is None


def test_reward_spec_recovers_missing_threshold_aggregate(tmp_path: Path) -> None:
    reward_toml = tmp_path / "reward.toml"
    reward_toml.write_text(
        '[[reward]]\nname = "reward"\naggregation = "threshold"\nthreshold = 0.8\n',
        encoding="utf-8",
    )

    rewards = HarborV2Backend._apply_reward_spec({"quality": 0.91}, reward_toml)
    assert rewards == {"quality": 0.91, "reward": 1.0}
    assert HarborV2Backend._score_from_rewards(rewards) == 1.0


def test_reward_spec_recovers_missing_all_pass_aggregate(tmp_path: Path) -> None:
    reward_toml = tmp_path / "reward.toml"
    reward_toml.write_text(
        '[[reward]]\nname = "reward"\naggregation = "all_pass"\n',
        encoding="utf-8",
    )

    rewards = HarborV2Backend._apply_reward_spec(
        {"quality": 0.0, "structure": 1.0},
        reward_toml,
    )
    assert rewards["reward"] == 0.0
    assert HarborV2Backend._score_from_rewards(rewards) == 0.0


def test_explicit_rewardkit_aggregate_is_not_overwritten(tmp_path: Path) -> None:
    reward_toml = tmp_path / "reward.toml"
    reward_toml.write_text(
        '[[reward]]\nname = "reward"\naggregation = "threshold"\nthreshold = 0.8\n',
        encoding="utf-8",
    )

    rewards = HarborV2Backend._apply_reward_spec(
        {"quality": 0.91, "reward": 0.25},
        reward_toml,
    )
    assert rewards["reward"] == 0.25


def test_multi_turn_protocol_completion_requires_send_and_done(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    state_path = trial / "agent" / "user_sim_state.json"
    state_path.parent.mkdir(parents=True)

    state_path.write_text(
        '{"started": true, "done": false, "termination_reason": null, '
        '"transcript": [{"source": "user", "text": "hello"}]}',
        encoding="utf-8",
    )
    complete, reason = HarborV2Backend._multi_turn_protocol_complete(trial)
    assert complete is False
    assert reason == "send_message_to_user was never called"

    state_path.write_text(
        '{"started": true, "done": true, "termination_reason": "user_done", '
        '"transcript": ['
        '{"source": "user", "text": "hello"}, '
        '{"source": "agent", "text": "reply"}]}',
        encoding="utf-8",
    )
    assert HarborV2Backend._multi_turn_protocol_complete(trial) == (True, "")
