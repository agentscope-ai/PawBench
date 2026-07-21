# -*- coding: utf-8 -*-
"""Offline tests for the user simulator (no network, no real LLM).

Run with:  python -m pytest pawbench/user_sim/tests -q
or standalone:  python pawbench/user_sim/tests/test_user_sim.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pawbench.user_sim import (
    LLMConfig,
    UserAgent,
    UserContext,
    build_user_agent_system_prompt,
    load_user_context,
    make_chat_result,
)
from pawbench.user_sim.context import is_approval_request
from pawbench.user_sim.llm import LLMClient
from pawbench.user_sim.runtime import UserSimRuntime
from pawbench.user_sim.workspace_patch import PatchApplyError, WorkspacePatchApplier


class ScriptedLLM:
    """Fake LLM returning canned completions in order; records the calls."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(list(messages))
        text = self._responses.pop(0) if self._responses else ""
        return make_chat_result(text)


def _run(coro):
    return asyncio.run(coro)


def _agent(persona_responses, approval_responses=None, *, context=None):
    ctx = context or UserContext(
        persona="我是一名数据分析师",
        task_metadata={"messages": [{"role": "user", "content": "帮我分析销售数据"}]},
    )
    return UserAgent(
        context=ctx,
        persona_llm=ScriptedLLM(persona_responses),
        approval_llm=ScriptedLLM(approval_responses or []),
    )


# ---------------------------------------------------------------------------
# UserAgent
# ---------------------------------------------------------------------------


def test_opening_uses_builder_first_query_as_seed():
    agent = _agent(["你好，我想看看这个月的销售情况"])
    opening = _run(agent.opening())
    assert opening == "你好，我想看看这个月的销售情况"
    # seed message should reference the builder-provided first user query
    persona_llm = agent._persona_llm
    seed_msg = persona_llm.calls[0][-1]["content"]
    assert "帮我分析销售数据" in seed_msg
    # opening recorded into history
    assert agent.history[-1] == {"role": "assistant", "content": opening}


def test_persona_multi_turn_and_done_detection():
    agent = _agent(["第一句", "还有一个问题", "谢谢，就到这里\n[DONE]"])
    first = _run(agent.opening())
    assert first == "第一句"
    assert agent.done is False

    second = _run(agent.respond_or_approve("这是助手的回复"))
    assert second == "还有一个问题"
    assert agent.done is False

    third = _run(agent.respond_or_approve("助手又回复了"))
    assert "[DONE]" in third
    assert agent.done is True


def test_approval_marker_routes_to_approval_llm():
    agent = _agent(
        persona_responses=["不应被调用"],
        approval_responses=["/approve"],
    )
    marker = "⚠️ Risk Detected / 检测到风险\nType `/approve` to approve"
    assert is_approval_request(marker)
    decision = _run(agent.respond_or_approve(marker))
    assert decision == "/approve"
    # approval path must not touch persona history / persona LLM
    assert agent._approval_llm.calls, "approval LLM should be called"
    assert agent._persona_llm.calls == []


def test_approval_llm_failure_fails_closed():
    class BoomLLM:
        async def chat(self, *a, **k):
            raise RuntimeError("provider down")

    agent = UserAgent(
        context=UserContext(persona="p"),
        persona_llm=ScriptedLLM([]),
        approval_llm=BoomLLM(),
    )
    decision = _run(agent.approve_tool_request("Waiting for approval: rm -rf /"))
    assert "不批准" in decision


# ---------------------------------------------------------------------------
# LLMClient credential fail-fast
# ---------------------------------------------------------------------------


def test_llmclient_requires_model_and_key():
    for cfg, needle in [
        (LLMConfig(model="", api_key="k"), "model"),
        (LLMConfig(model="m", api_key=""), "api_key"),
    ]:
        try:
            LLMClient(cfg)
        except ValueError as exc:
            assert needle in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"expected ValueError mentioning {needle}")


# ---------------------------------------------------------------------------
# prompt + context
# ---------------------------------------------------------------------------


def test_system_prompt_renders_placeholders_and_defaults():
    ctx = UserContext(persona="爱猫的程序员", profile={"age": 30})
    prompt = build_user_agent_system_prompt(ctx.as_dict())
    assert "爱猫的程序员" in prompt
    assert '"age": 30' in prompt
    # unset fields fall back to (无) rather than leaving raw placeholders
    assert "{persona}" not in prompt
    assert "(无)" in prompt


def test_load_user_context_reads_user_dir(tmp_path: Path):
    user_dir = tmp_path / ".user"
    (user_dir).mkdir()
    (user_dir / "persona.md").write_text("我是测试用户", encoding="utf-8")
    (user_dir / "profile.json").write_text(json.dumps({"role": "pm"}), encoding="utf-8")
    (user_dir / "memory").mkdir()
    (user_dir / "memory" / "m1.md").write_text("长期记忆一", encoding="utf-8")

    ctx = load_user_context(tmp_path)
    assert ctx.persona == "我是测试用户"
    assert ctx.profile == {"role": "pm"}
    assert "长期记忆一" in ctx.long_term_memory


# ---------------------------------------------------------------------------
# UserSimRuntime (MCP-facing lifecycle)
# ---------------------------------------------------------------------------


def test_runtime_full_conversation_and_persistence(tmp_path: Path):
    state_path = tmp_path / "state.json"
    agent = _agent(["开场白", "继续聊", "好的，再见\n[DONE]"])
    rt = UserSimRuntime(
        tmp_path, max_turns=10, agent=agent, state_path=state_path
    )

    opening = _run(rt.start_conversation())
    assert opening == "开场白"

    r1 = json.loads(_run(rt.send_message_to_user("助手回复1")))
    assert r1["user_message"] == "继续聊"
    assert r1["conversation_over"] is False
    assert r1["turn"] == 1

    r2 = json.loads(_run(rt.send_message_to_user("助手回复2")))
    assert r2["conversation_over"] is True
    assert r2["termination_reason"] == "user_done"

    # transcript persisted with alternating sources
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    sources = [m["source"] for m in persisted["transcript"]]
    assert sources == ["user", "agent", "user", "agent", "user"]
    assert persisted["done"] is True


def test_runtime_max_turns_forces_stop(tmp_path: Path):
    agent = _agent(["开场", "a", "b", "c", "d"])
    rt = UserSimRuntime(
        tmp_path, max_turns=2, agent=agent, state_path=tmp_path / "s.json"
    )
    _run(rt.start_conversation())
    _run(rt.send_message_to_user("m1"))
    res = json.loads(_run(rt.send_message_to_user("m2")))
    assert res["conversation_over"] is True
    assert res["termination_reason"] == "max_turns"
    # once over, further sends short-circuit without advancing turns
    res3 = json.loads(_run(rt.send_message_to_user("m3")))
    assert res3["turn"] == 2


def test_runtime_send_before_start_raises(tmp_path: Path):
    agent = _agent(["x"])
    rt = UserSimRuntime(tmp_path, agent=agent, state_path=tmp_path / "s.json")
    try:
        _run(rt.send_message_to_user("hi"))
    except RuntimeError as exc:
        assert "start_conversation" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_cowork_patches_apply_before_corresponding_user_turn(tmp_path: Path):
    task_dir = tmp_path / "task"
    patch_dir = task_dir / ".user" / "patches"
    workspace = tmp_path / "workspace"
    patch_dir.mkdir(parents=True)
    (workspace / "workspace").mkdir(parents=True)
    target = workspace / "workspace" / "index.html"
    target.write_text("<html>\n", encoding="utf-8")
    (patch_dir / "turn_01_hint.md").write_text(
        "---\nfiles: []\n---\nopening\n",
        encoding="utf-8",
    )
    (patch_dir / "turn_02_hint.md").write_text(
        "---\n"
        "files:\n"
        "  - path: workspace/index.html\n"
        "    action: edit\n"
        "    old: \"<html>\"\n"
        "    new: '<html lang=\"zh-CN\">'\n"
        "---\nsecond turn\n",
        encoding="utf-8",
    )
    (patch_dir / "turn_03_hint.md").write_text(
        "---\n"
        "files:\n"
        "  - path: workspace/draft.md\n"
        "    action: create\n"
        "    content: \"draft\\n\"\n"
        "---\nthird turn\n",
        encoding="utf-8",
    )

    agent = _agent(["opening", "turn two", "turn three"])
    rt = UserSimRuntime(
        task_dir,
        agent=agent,
        workspace_root=workspace,
        state_path=tmp_path / "state.json",
    )
    _run(rt.start_conversation())
    assert target.read_text(encoding="utf-8") == "<html>\n"

    _run(rt.send_message_to_user("assistant one"))
    assert target.read_text(encoding="utf-8") == '<html lang="zh-CN">\n'
    assert not (workspace / "workspace" / "draft.md").exists()

    _run(rt.send_message_to_user("assistant two"))
    assert (workspace / "workspace" / "draft.md").read_text() == "draft\n"
    assert [event["turn"] for event in rt.workspace_events] == [2, 3]


def test_workspace_patch_rejects_path_escape(tmp_path: Path):
    task_dir = tmp_path / "task"
    patch_dir = task_dir / ".patch"
    patch_dir.mkdir(parents=True)
    (patch_dir / "turn_01.md").write_text(
        "---\n"
        "files:\n"
        "  - path: ../escape.txt\n"
        "    action: create\n"
        "    content: nope\n"
        "---\n",
        encoding="utf-8",
    )
    applier = WorkspacePatchApplier(task_dir, tmp_path / "workspace")
    try:
        applier.apply_turn(1)
    except PatchApplyError as exc:
        assert "escapes workspace" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected workspace escape to be rejected")


if __name__ == "__main__":
    import sys

    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        import inspect

        kwargs = {}
        if "tmp_path" in inspect.signature(fn).parameters:
            import tempfile

            kwargs["tmp_path"] = Path(tempfile.mkdtemp())
        try:
            fn(**kwargs)
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failures else 0)
