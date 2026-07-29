"""PawBench-owned reliability wrappers for vendored Harbor agents."""

from __future__ import annotations

import json
from typing import Any

from harbor.agents.installed.hermes import Hermes
from harbor.agents.installed.qwenpaw import QwenPaw
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from harbor.utils.trajectory_utils import format_trajectory_json


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            (
                str(item.get("text") or "")
                if isinstance(item, dict) and item.get("type") == "text"
                else json.dumps(item, ensure_ascii=False, sort_keys=True)
            )
            for item in value
        )
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def qwenpaw_session_to_atif(
    payload: dict[str, Any],
    *,
    agent_version: str,
    model_name: str | None,
) -> Trajectory | None:
    """Convert QwenPaw 1.x/2.x native session state into ATIF-v1.7."""
    native_agent = payload.get("agent")
    if not isinstance(native_agent, dict):
        return None
    state = native_agent.get("state")
    context = state.get("context") if isinstance(state, dict) else None
    if not isinstance(context, list):
        memory = native_agent.get("memory")
        context = memory.get("content") if isinstance(memory, dict) else None
    if not isinstance(context, list):
        return None

    steps: list[Step] = []
    totals = {"prompt": 0, "completion": 0, "cached": 0}
    for turn in context:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or turn.get("name") or "").lower()
        source = "agent" if role in {"assistant", "agent"} else role
        if source not in {"system", "user", "agent"}:
            continue
        content = turn.get("content")
        timestamp = str(turn["created_at"]) if turn.get("created_at") else None
        if source != "agent":
            message = _text(content)
            if message:
                steps.append(
                    Step(
                        step_id=len(steps) + 1,
                        source=source,
                        message=message,
                        timestamp=timestamp,
                    )
                )
            continue

        blocks = content if isinstance(content, list) else []
        texts = [_text(content)] if not isinstance(content, list) else []
        reasoning: list[str] = []
        calls: list[ToolCall] = []
        results: list[ObservationResult] = []
        call_ids: set[str] = set()
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                texts.append(_text(block))
                continue
            kind = str(block.get("type") or "")
            if kind == "thinking":
                reasoning.append(str(block.get("thinking") or block.get("text") or ""))
            elif kind == "text":
                texts.append(str(block.get("text") or ""))
            elif kind in {"tool_call", "tool_use"}:
                call_id = str(block.get("id") or f"qwenpaw-{len(steps) + 1}-{index}")
                arguments = block.get("input", block.get("arguments", {}))
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {"raw": arguments}
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
                calls.append(
                    ToolCall(
                        tool_call_id=call_id,
                        function_name=str(block.get("name") or "unknown_tool"),
                        arguments=arguments,
                    )
                )
                call_ids.add(call_id)
            elif kind in {"tool_result", "tool_response"}:
                call_id = str(block.get("tool_call_id") or block.get("id") or "")
                results.append(
                    ObservationResult(
                        source_call_id=call_id if call_id in call_ids else None,
                        content=_text(block.get("output", block.get("content"))),
                    )
                )
            else:
                texts.append(_text(block))

        usage = turn.get("usage") if isinstance(turn.get("usage"), dict) else {}
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        cached = int(
            usage.get("cached_tokens")
            or usage.get("cacheRead")
            or usage.get("cache_read")
            or 0
        )
        totals["prompt"] += prompt
        totals["completion"] += completion
        totals["cached"] += cached
        metrics = (
            Metrics(
                prompt_tokens=prompt or None,
                completion_tokens=completion or None,
                cached_tokens=cached or None,
            )
            if prompt or completion or cached
            else None
        )
        steps.append(
            Step(
                step_id=len(steps) + 1,
                source="agent",
                message="\n".join(filter(None, texts)) or "(tool use)",
                timestamp=timestamp,
                model_name=model_name,
                reasoning_content="\n\n".join(filter(None, reasoning)) or None,
                tool_calls=calls or None,
                observation=Observation(results=results) if results else None,
                metrics=metrics,
                llm_call_count=1,
            )
        )
    if not steps:
        return None
    return Trajectory(
        schema_version="ATIF-v1.7",
        session_id=str(
            native_agent.get("id")
            or (state.get("id") if isinstance(state, dict) else "")
            or "qwenpaw-session"
        ),
        agent=Agent(
            name="qwenpaw",
            version=agent_version,
            model_name=model_name,
        ),
        steps=steps,
        final_metrics=FinalMetrics(
            total_prompt_tokens=totals["prompt"] or None,
            total_completion_tokens=totals["completion"] or None,
            total_cached_tokens=totals["cached"] or None,
            total_steps=len(steps),
        ),
    )


class PawBenchHermes(Hermes):
    """Hermes with a fast path and resilient Debian dependency installation."""

    async def install(self, environment: BaseEnvironment) -> None:
        if self._version is None:
            probe = await environment.exec(
                command=self.get_version_command() or "false",
                timeout_sec=30,
            )
            if probe.return_code == 0 and (probe.stdout or "").strip():
                self.logger.info("Hermes is already installed; skipping setup")
                return
        await self.exec_as_root(
            environment,
            command=(
                "set -euo pipefail; missing=0; "
                "command -v curl >/dev/null || missing=1; "
                "command -v git >/dev/null || missing=1; "
                "command -v rg >/dev/null || missing=1; "
                "command -v xz >/dev/null || missing=1; "
                "if [ \"$missing\" -eq 0 ]; then exit 0; fi; "
                "packages='curl git ripgrep xz-utils'; "
                "if apt-get install -y --no-install-recommends $packages; then exit 0; fi; "
                "tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT; mkdir -p \"$tmp/parts\"; "
                "for source in /etc/apt/sources.list /etc/apt/sources.list.d/*; do "
                " [ -f \"$source\" ] || continue; "
                " if grep -Eq 'deb\\.debian\\.org|security\\.debian\\.org|"
                "archive\\.ubuntu\\.com|security\\.ubuntu\\.com' \"$source\"; then "
                " cp \"$source\" \"$tmp/parts/$(basename \"$source\")\"; fi; done; "
                "test -n \"$(ls -A \"$tmp/parts\")\"; "
                "opts=\"-o Dir::Etc::SourceList=/dev/null "
                "-o Dir::Etc::SourceParts=$tmp/parts -o Acquire::PDiffs=false\"; "
                "apt-get $opts update; "
                "apt-get $opts install -y --no-install-recommends $packages"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
            timeout_sec=300,
        )
        branch = f" --branch {self._version}" if self._version else ""
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "curl -fsSL https://raw.githubusercontent.com/NousResearch/"
                f"hermes-agent/main/scripts/install.sh | bash -s -- --skip-setup{branch} && "
                'export PATH="$HOME/.local/bin:$PATH" && '
                'export HERMES_HOME="${HERMES_HOME:-/tmp/hermes}" && '
                'mkdir -p "$HERMES_HOME" "$HERMES_HOME/sessions" '
                '"$HERMES_HOME/skills" "$HERMES_HOME/memories" && hermes version'
            ),
            timeout_sec=900,
        )


class PawBenchQwenPaw(QwenPaw):
    """QwenPaw adapter that guarantees ATIF before the verifier runs."""

    SUPPORTS_ATIF = True

    def populate_context_post_run(self, context: AgentContext) -> None:
        session_path = self.logs_dir / "qwenpaw.session.json"
        if not session_path.is_file():
            return
        try:
            payload = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid QwenPaw session: {session_path}") from exc
        trajectory = qwenpaw_session_to_atif(
            payload,
            agent_version=str(self._version or "unknown"),
            model_name=self.model_name,
        )
        if trajectory is None:
            raise RuntimeError("QwenPaw session has no convertible native trajectory")
        (self.logs_dir / "trajectory.json").write_text(
            format_trajectory_json(trajectory.to_json_dict()),
            encoding="utf-8",
        )
        super().populate_context_post_run(context)
