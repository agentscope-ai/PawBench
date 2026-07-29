from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pawbench.agents.impl.harbor_reliability_agents import (
    PawBenchHermes,
    PawBenchQwenPaw,
    qwenpaw_session_to_atif,
)


def test_qwenpaw_native_session_converts_to_atif() -> None:
    payload = {
        "agent": {
            "id": "session-1",
            "state": {
                "context": [
                    {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                    {
                        "role": "assistant",
                        "usage": {"input_tokens": 10, "output_tokens": 4},
                        "content": [
                            {
                                "type": "tool_call",
                                "id": "call-1",
                                "name": "shell",
                                "input": '{"command":"ls"}',
                            },
                            {
                                "type": "tool_result",
                                "id": "call-1",
                                "output": [{"type": "text", "text": "README.md"}],
                            },
                        ],
                    },
                ]
            },
        }
    }

    trajectory = qwenpaw_session_to_atif(
        payload,
        agent_version="2.0.0.post3",
        model_name="openai/qwen3.6-plus",
    )

    assert trajectory is not None
    assert trajectory.schema_version == "ATIF-v1.7"
    assert trajectory.steps[1].tool_calls[0].tool_call_id == "call-1"
    assert trajectory.steps[1].observation.results[0].source_call_id == "call-1"
    assert trajectory.final_metrics.total_prompt_tokens == 10
    trajectory.model_validate(trajectory.model_dump())
    assert PawBenchQwenPaw.SUPPORTS_ATIF is True


def test_qwenpaw_v1_memory_converts_to_atif() -> None:
    trajectory = qwenpaw_session_to_atif(
        {
            "agent": {
                "memory": {
                    "content": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "hi"},
                    ]
                }
            }
        },
        agent_version="1.1.12.post3",
        model_name="openai/qwen3.6-plus",
    )

    assert trajectory is not None
    assert [step.source for step in trajectory.steps] == ["user", "agent"]


def test_hermes_skips_install_when_binary_exists(tmp_path) -> None:
    class Environment:
        calls: list[str] = []

        async def exec(self, *, command, **kwargs):
            self.calls.append(command)
            return SimpleNamespace(return_code=0, stdout="Hermes 0.1", stderr="")

    environment = Environment()
    agent = PawBenchHermes(logs_dir=tmp_path, model_name="openai/qwen3.6-plus")

    asyncio.run(agent.install(environment))

    assert len(environment.calls) == 1
    assert "hermes version" in environment.calls[0]
