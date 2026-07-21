from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import re
import signal
import shlex
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from agentscope.agent import Agent
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.credential import DashScopeCredential
from agentscope.message import TextBlock, ToolResultState, UserMsg
from agentscope.model import DashScopeChatModel
from agentscope.permission import AdditionalWorkingDirectory, PermissionBehavior, PermissionContext, PermissionMode, PermissionRule
from agentscope.state import AgentState
from agentscope.tool import (
    Bash,
    Edit,
    ExecResult,
    Glob,
    Grep,
    LocalBackend,
    Read,
    ToolBase,
    ToolChunk,
    ToolMiddlewareBase,
    Toolkit,
    Write,
)
from pydantic import SecretStr
from pawbench_agentscope._portable_security import (
    redact_sensitive_text,
    redact_sensitive_value,
    resolve_openai_compatible_provider,
    safe_subprocess_env,
)

from pawbench_agentscope.features import (
    TAXONOMY_VERSION,
    FeatureConfig,
    PersistentMemoryStore,
    budget_policy,
    completion_decision,
    diff_workspace_snapshots,
    emit_feature_events,
    normalize_tool_feedback,
    preflight_workspace,
    prepare_prompt,
    snapshot_workspace,
    trace_runtime_contracts,
)
from pawbench_agentscope.models import RunResult, TaskSpec
from pawbench_agentscope.tracing import TraceWriter
from pawbench_agentscope.verifier import verify_artifacts


class SanitizedLocalBackend(LocalBackend):
    """AgentScope local backend with a minimal, workspace-bound environment."""

    MAX_CAPTURE_BYTES = 8 * 1024 * 1024

    def __init__(self, workspace_root: Path) -> None:
        super().__init__()
        self.workspace_root = workspace_root.resolve()
        self.env = safe_subprocess_env(
            self.workspace_root,
            extra={"PAWBENCH_WORKSPACE_ROOT": str(self.workspace_root)},
        )

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        if not command:
            return ExecResult(exit_code=127, stdout=b"", stderr=b"empty command")

        requested_cwd = Path(cwd) if cwd is not None else self.workspace_root
        if not requested_cwd.is_absolute():
            requested_cwd = self.workspace_root / requested_cwd
        try:
            resolved_cwd = requested_cwd.resolve()
        except (OSError, RuntimeError):
            return ExecResult(
                exit_code=126,
                stdout=b"",
                stderr=b"working directory could not be resolved safely",
            )
        if resolved_cwd != self.workspace_root and self.workspace_root not in resolved_cwd.parents:
            return ExecResult(
                exit_code=126,
                stdout=b"",
                stderr=b"working directory outside workspace",
            )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(resolved_cwd),
                env=self.env,
                start_new_session=True,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, NotADirectoryError, OSError, TypeError, ValueError) as exc:
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=f"{type(exc).__name__}: command unavailable".encode(),
            )

        def kill_process_group() -> None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (AttributeError, ProcessLookupError, PermissionError):
                if process.returncode is None:
                    process.kill()

        async def terminate_process_group() -> None:
            kill_process_group()
            # Do not call communicate() here. A detached descendant can retain
            # inherited pipes after the direct child exits and make cleanup
            # wait forever. The bounded readers are cancelled by wait_for;
            # waiting for the direct child is sufficient after group cleanup.
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            # asyncio.Process has no public pipe-close API. Explicitly close
            # its transport during exceptional cleanup so pipe objects are not
            # finalized after the event loop has already closed.
            transport = getattr(process, "_transport", None)
            if transport is not None:
                transport.close()
                await asyncio.sleep(0)

        captured_total = 0
        output_exceeded = False
        limit_reached = asyncio.Event()
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()

        async def communicate_limited() -> tuple[bytes, bytes]:
            async def read_stream(
                stream: asyncio.StreamReader | None,
                captured: bytearray,
            ) -> None:
                nonlocal captured_total, output_exceeded
                if stream is None:
                    return
                while chunk := await stream.read(64 * 1024):
                    remaining = max(0, self.MAX_CAPTURE_BYTES - captured_total)
                    if remaining:
                        kept = chunk[:remaining]
                        captured.extend(kept)
                        captured_total += len(kept)
                    if len(chunk) > remaining and not output_exceeded:
                        output_exceeded = True
                        kill_process_group()
                        limit_reached.set()
                        return

            await asyncio.gather(
                read_stream(process.stdout, stdout_buffer),
                read_stream(process.stderr, stderr_buffer),
                process.wait(),
            )
            return bytes(stdout_buffer), bytes(stderr_buffer)

        communication_task = asyncio.create_task(communicate_limited())
        limit_task = asyncio.create_task(limit_reached.wait())

        async def cancel_io_tasks() -> None:
            for task in (communication_task, limit_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(communication_task, limit_task, return_exceptions=True)

        try:
            done, _ = await asyncio.wait(
                {communication_task, limit_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await terminate_process_group()
                await cancel_io_tasks()
                return ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")
            if limit_reached.is_set():
                await terminate_process_group()
                await cancel_io_tasks()
                stdout = bytes(stdout_buffer)
                stderr = bytes(stderr_buffer)
                marker = (
                    f"output limit exceeded ({self.MAX_CAPTURE_BYTES} bytes); "
                    "process terminated"
                ).encode()
                stderr = stderr + (b"\n" if stderr else b"") + marker
                return ExecResult(exit_code=-1, stdout=stdout, stderr=stderr)
            stdout, stderr = communication_task.result()
        except asyncio.CancelledError:
            await terminate_process_group()
            await cancel_io_tasks()
            raise
        except Exception as exc:
            await terminate_process_group()
            await cancel_io_tasks()
            return ExecResult(
                exit_code=-1,
                stdout=bytes(stdout_buffer),
                stderr=f"{type(exc).__name__}: subprocess I/O failed".encode(),
            )
        finally:
            if not limit_task.done():
                limit_task.cancel()
                await asyncio.gather(limit_task, return_exceptions=True)
        return ExecResult(
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
        )


class SecretRedactionMiddleware(ToolMiddlewareBase):
    """Remove credentials from tool results before AgentScope sees them."""

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Any],
    ):
        async for chunk in next_handler(**input_kwargs):
            safe_chunk = redact_sensitive_value(chunk.model_dump(mode="python"))
            yield ToolChunk.model_validate(safe_chunk)


class WorkspaceGuardMiddleware(ToolMiddlewareBase):
    def __init__(
        self,
        workspace_root: Path,
        trace: TraceWriter | None = None,
        *,
        enhanced_policy: bool = True,
        structured_feedback: bool = True,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.trace = trace
        self.enhanced_policy = enhanced_policy
        self.structured_feedback = structured_feedback

    def _alias_workspace_path(self, value: str) -> str:
        if value == "/workspace":
            return "workspace"
        if value.startswith("/workspace/"):
            return "workspace/" + value[len("/workspace/") :]
        return value

    def _normalize_workspace_aliases(self, tool_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if not self.enhanced_policy:
            return dict(kwargs)
        normalized = dict(kwargs)
        rewrites: list[dict[str, str]] = []
        for key in ("file_path", "path"):
            value = normalized.get(key)
            if isinstance(value, str):
                aliased = self._alias_workspace_path(value)
                if aliased != value:
                    normalized[key] = aliased
                    rewrites.append({"field": key, "from": value, "to": aliased})
        if tool_name == "Bash" and isinstance(normalized.get("command"), str):
            command = normalized["command"]
            aliased = re.sub(
                r"(?<![A-Za-z0-9_.-])/workspace(?=(?:/|$|[\s'\";|&<>]))",
                "workspace",
                command,
            )
            if aliased != command:
                normalized["command"] = aliased
                rewrites.append({"field": "command", "from": command, "to": aliased})
        if rewrites and self.trace is not None:
            self.trace.append("workspace_alias_rewrite", {"tool": tool_name, "rewrites": rewrites})
        return normalized

    def _bind_relative_tool_paths(self, tool_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Adapt workspace-relative paths to AgentScope's absolute-path contract."""
        path_fields = {
            "Read": ("file_path",),
            "Write": ("file_path",),
            "Edit": ("file_path",),
            "Grep": ("path",),
            "Glob": ("path",),
        }
        normalized = dict(kwargs)
        rewrites: list[dict[str, str]] = []
        if tool_name in {"Grep", "Glob"} and not normalized.get("path"):
            normalized["path"] = str(self.workspace_root)
            rewrites.append(
                {
                    "field": "path",
                    "from": "",
                    "to": str(self.workspace_root),
                    "reason": "default_workspace_root",
                }
            )
        for key in path_fields.get(tool_name, ()):
            value = normalized.get(key)
            if not isinstance(value, str) or not value:
                continue
            display_root = redact_sensitive_text(str(self.workspace_root))
            if display_root != str(self.workspace_root) and (
                value == display_root or value.startswith(display_root + "/")
            ):
                suffix = value[len(display_root) :].lstrip("/")
                restored = str((self.workspace_root / suffix).resolve())
                normalized[key] = restored
                rewrites.append(
                    {
                        "field": key,
                        "from": value,
                        "to": restored,
                        "reason": "redacted_workspace_alias",
                    }
                )
                continue
            if value.startswith(("~", "$")):
                continue
            path = Path(value)
            if path.is_absolute():
                continue
            bound = str((self.workspace_root / path).resolve())
            normalized[key] = bound
            rewrites.append({"field": key, "from": value, "to": bound})
        if rewrites and self.trace is not None:
            self.trace.append("workspace_relative_path_bound", {"tool": tool_name, "rewrites": rewrites})
        return normalized

    def _inside(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            return False
        return resolved == self.workspace_root or self.workspace_root in resolved.parents

    def _candidate_paths(self, tool_name: str, kwargs: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("file_path", "path"):
            value = kwargs.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
        if tool_name == "Glob":
            pattern = kwargs.get("pattern")
            if isinstance(pattern, str) and pattern and (
                pattern.startswith(("/", "~")) or any(part == ".." for part in Path(pattern).parts)
            ):
                candidates.append(pattern)
        if tool_name == "Bash":
            command = str(kwargs.get("command", ""))
            try:
                tokens = shlex.split(command)
            except ValueError:
                tokens = re.findall(r"[^\s'\";|&]+", command)
            shell_operators = {"&&", "||", "|", ";", "&", ">", ">>", "<", "<<"}
            workspace_aliases = (
                "$HOME",
                "${HOME}",
                "$PWD",
                "${PWD}",
                "$PAWBENCH_WORKSPACE_ROOT",
                "${PAWBENCH_WORKSPACE_ROOT}",
                "$(pwd)",
            )
            for token in tokens:
                if token in shell_operators:
                    continue
                cleaned = re.sub(r"^(?:\d*(?:>>?|<<?|<>|>&|<&)|&>)", "", token)
                values = [cleaned]
                if "=" in cleaned:
                    values.append(cleaned.split("=", 1)[1])
                for value in values:
                    if not value:
                        continue
                    normalized = value
                    for alias in workspace_aliases:
                        if value == alias or value.startswith(alias + "/"):
                            normalized = str(self.workspace_root) + value[len(alias) :]
                            break
                    relative_candidate = self.workspace_root / normalized
                    has_parent_segment = any(part == ".." for part in Path(normalized).parts)
                    looks_pathlike = (
                        normalized.startswith(("/", "~", "."))
                        or "/" in normalized
                        or has_parent_segment
                        or relative_candidate.is_symlink()
                        or relative_candidate.exists()
                    )
                    if looks_pathlike:
                        candidates.append(normalized)
            candidates.extend(
                re.findall(
                    r"(?<![A-Za-z0-9_.-])((?:[A-Za-z0-9_.$}{:-]+/)*\.\.(?:/[A-Za-z0-9_.$}{:-]+)*)",
                    command,
                )
            )
            candidates.extend(
                re.findall(
                    r"(?<![A-Za-z0-9_.:/-])(/(?!/)[^\s'\";|&<>()\[\]{},]+)",
                    command,
                )
            )
        return candidates

    def _blocked_path(self, tool_name: str, kwargs: dict[str, Any]) -> str | None:
        for raw in self._candidate_paths(tool_name, kwargs):
            if raw.startswith("~"):
                return raw
            if tool_name == "Bash" and "$" in raw and any(part == ".." for part in Path(raw).parts):
                return raw
            path = Path(raw)
            candidate = path if path.is_absolute() else self.workspace_root / path
            if not self._inside(candidate):
                return raw
        return None

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Any],
    ):
        input_kwargs = self._normalize_workspace_aliases(tool.name, input_kwargs)
        input_kwargs = self._bind_relative_tool_paths(tool.name, input_kwargs)
        blocked = self._blocked_path(tool.name, input_kwargs)
        if blocked is not None:
            payload = {"tool": tool.name, "blocked_path": blocked, "workspace_root": str(self.workspace_root)}
            if self.trace is not None:
                self.trace.append("workspace_guard_denied", payload)
            message = f"Workspace guard denied path outside workspace: {blocked}"
            if self.structured_feedback:
                message = normalize_tool_feedback(
                    {
                        "tool": tool.name,
                        "state": "permission_denied",
                        "message": message,
                        "metadata": payload,
                    },
                    enabled=True,
                )
                message = json.dumps(message, ensure_ascii=False)
            yield ToolChunk(
                content=[TextBlock(text=message)],
                state=ToolResultState.ERROR,
                is_last=True,
                metadata=payload,
            )
            return
        async for chunk in next_handler(**input_kwargs):
            yield chunk


def validate_action_arguments(tool_name: str, kwargs: dict[str, Any]) -> list[str]:
    required_fields = {
        "Bash": (("command",),),
        "Read": (("file_path", "path"),),
        "Write": (("file_path", "path"), ("content",)),
        "Edit": (("file_path", "path"), ("old_string",), ("new_string",)),
        "Grep": (("pattern",),),
        "Glob": (("pattern",),),
    }
    errors: list[str] = []
    for alternatives in required_fields.get(tool_name, ()):
        if not any(isinstance(kwargs.get(field), str) and kwargs[field] for field in alternatives):
            errors.append("missing non-empty " + " or ".join(alternatives))
    return errors


class ActionContractMiddleware(ToolMiddlewareBase):
    def __init__(self, *, enabled: bool, structured_feedback: bool, trace: TraceWriter | None = None) -> None:
        self.enabled = enabled
        self.structured_feedback = structured_feedback
        self.trace = trace

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Any],
    ):
        errors = validate_action_arguments(tool.name, input_kwargs) if self.enabled else []
        if errors:
            payload = {"tool": tool.name, "errors": errors, "arguments": sorted(input_kwargs)}
            if self.trace is not None:
                self.trace.append("action_validation_failed", payload)
            message: Any = f"Action contract rejected {tool.name}: {'; '.join(errors)}"
            if self.structured_feedback:
                message = normalize_tool_feedback(
                    {"tool": tool.name, "state": "invalid_arguments", "message": message, "metadata": payload},
                    enabled=True,
                )
                message = json.dumps(message, ensure_ascii=False)
            yield ToolChunk(
                content=[TextBlock(text=str(message))],
                state=ToolResultState.ERROR,
                is_last=True,
                metadata=payload,
            )
            return
        if self.enabled and self.trace is not None:
            self.trace.append("action_validation_passed", {"tool": tool.name, "arguments": sorted(input_kwargs)})
        async for chunk in next_handler(**input_kwargs):
            yield chunk


def dashscope_model_from_env(*, model_name: str | None = None) -> DashScopeChatModel:
    settings = resolve_openai_compatible_provider(
        os.environ,
        allowed_providers=("dashscope",),
    )
    if urlsplit(settings.base_url).scheme != "https":
        raise RuntimeError("DashScope base URL must use HTTPS")
    model = (
        model_name
        or os.getenv("AGENTSCOPE_MODEL_NAME")
        or os.getenv("HARNESS_MODEL_NAME")
        or os.getenv("REAL_ENV_MODEL_NAME")
        or "deepseek-v4-pro"
    )
    if (
        not isinstance(model, str)
        or not model.strip()
        or len(model.strip().encode("utf-8")) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in model)
    ):
        raise RuntimeError(
            "AgentScope model name must be a non-empty control-free string of at most 256 UTF-8 bytes"
        )
    model = model.strip()
    raw_temperature = os.getenv("AGENTSCOPE_TEMPERATURE", "0.2").strip()
    try:
        temperature = float(raw_temperature)
    except ValueError as exc:
        raise RuntimeError("AGENTSCOPE_TEMPERATURE must be a finite non-negative number") from exc
    if not math.isfinite(temperature) or temperature < 0:
        raise RuntimeError("AGENTSCOPE_TEMPERATURE must be a finite non-negative number")
    raw_thinking = os.getenv("DASHSCOPE_THINKING_ENABLE", "0").strip()
    if raw_thinking not in {"0", "1"}:
        raise RuntimeError("DASHSCOPE_THINKING_ENABLE must be 0 or 1")
    params = DashScopeChatModel.Parameters(
        thinking_enable=raw_thinking == "1",
        temperature=temperature,
        parallel_tool_calls=False,
    )
    credential = DashScopeCredential(
        api_key=SecretStr(settings.api_key),
        base_url=settings.base_url,
    )
    return DashScopeChatModel(credential=credential, model=model, parameters=params, stream=True, max_retries=1)


TOOL_ALIASES = {
    "Bash": "run_shell",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Grep": "grep",
    "Glob": "glob",
}


def enabled_tool_aliases(config: FeatureConfig) -> list[str]:
    aliases = list(TOOL_ALIASES.values())
    if config.on("F2.2"):
        return aliases
    hidden = str(config.target("F2.2", "write_file"))
    hidden_alias = TOOL_ALIASES.get(hidden, hidden)
    return [alias for alias in aliases if alias != hidden_alias]


def build_toolkit(workspace_root: Path, config: FeatureConfig, trace: TraceWriter | None = None) -> Toolkit | None:
    aliases = set(enabled_tool_aliases(config))
    guard = WorkspaceGuardMiddleware(
        workspace_root,
        trace,
        enhanced_policy=config.on("F1.3"),
        structured_feedback=config.on("F2.3"),
    )
    action_contract = ActionContractMiddleware(
        enabled=config.on("F2.1"),
        structured_feedback=config.on("F2.3"),
        trace=trace,
    )
    middlewares = [SecretRedactionMiddleware(), action_contract, guard]
    shell_backend = SanitizedLocalBackend(workspace_root)
    factories = {
        "run_shell": lambda: Bash(
            cwd=str(workspace_root),
            middlewares=middlewares,
            backend=shell_backend,
        ),
        "read_file": lambda: Read(middlewares=middlewares),
        "write_file": lambda: Write(middlewares=middlewares),
        "edit_file": lambda: Edit(middlewares=middlewares),
        "grep": lambda: Grep(middlewares=middlewares),
        "glob": lambda: Glob(middlewares=middlewares),
    }
    tools = [factories[alias]() for alias in TOOL_ALIASES.values() if alias in aliases]
    return Toolkit(tools=tools) if tools else None


def build_agent(
    *,
    model,
    toolkit: Toolkit | None,
    workspace_root: Path,
    max_iters: int,
    tool_result_limit: int,
) -> Agent:
    permission_context = PermissionContext(
        mode=PermissionMode.ACCEPT_EDITS,
        working_directories={
            str(workspace_root): AdditionalWorkingDirectory(path=str(workspace_root), source="pawbench_run"),
        },
        allow_rules={
            name: [
                PermissionRule(
                    tool_name=name,
                    rule_content=None,
                    behavior=PermissionBehavior.ALLOW,
                    source="pawbench_workspace_guard",
                )
            ]
            for name in ("Bash", "Read", "Write", "Edit", "Grep", "Glob")
        },
    )
    state = AgentState(permission_context=permission_context)
    return Agent(
        name="pawbench_agentscope",
        system_prompt=(
            "You are a PawBench task-solving agent. Use the available tools when needed. "
            "Write requested artifacts exactly, then provide a concise final summary. "
            "Every operation must stay inside the provided workspace root. For Bash, rely on its workspace cwd and "
            "use relative paths. Use task-relative paths for dedicated file tools too; the harness safely binds them "
            "to the absolute workspace required by AgentScope. Never use a path outside the workspace root. "
            "This is an unattended run: never bypass safety checks or issue a command that needs interactive "
            "permission. Prefer Glob, Grep, Read, Write, and Edit over Bash. If Bash is unavoidable, use a simple "
            "static command without command substitution, process substitution, backticks, or shell loops."
        ),
        model=model,
        toolkit=toolkit,
        state=state,
        react_config=ReActConfig(max_iters=max_iters),
        context_config=ContextConfig(tool_result_limit=tool_result_limit),
    )


def serialize_event(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        data = event.model_dump(mode="json")
    elif hasattr(event, "to_dict"):
        data = event.to_dict()
    else:
        data = dict(getattr(event, "__dict__", {}))
    data["event_class"] = type(event).__name__
    safe_data = redact_sensitive_value(data)
    if not isinstance(safe_data, dict):
        raise TypeError("serialized AgentScope event must be a mapping")
    return safe_data


async def toolkit_schema_summary(toolkit: Toolkit | None) -> list[dict[str, Any]]:
    if toolkit is None:
        return []
    schemas = await toolkit.get_tool_schemas()
    return [
        {
            "name": schema.get("function", {}).get("name"),
            "required": schema.get("function", {}).get("parameters", {}).get("required", []),
            "properties": sorted(schema.get("function", {}).get("parameters", {}).get("properties", {})),
        }
        for schema in schemas
    ]


async def _run_agent_once(
    agent: Agent,
    prompt: str,
    trace: TraceWriter,
    *,
    timeout_seconds: float,
    structured_feedback: bool,
) -> tuple[str, int, dict[str, Any]]:
    final_text: list[str] = []
    event_count = 0
    summary: dict[str, Any] = {
        "finished_reason": None,
        "exceed_max_iters": False,
        "permission_required": False,
        "tool_calls": [],
        "tool_errors": [],
        "model_calls": 0,
        "timed_out": False,
    }
    tool_names_by_id: dict[str, str] = {}
    tool_result_text_by_id: dict[str, list[str]] = {}
    timeout_context = asyncio.timeout(timeout_seconds)
    try:
        async with timeout_context:
            safe_prompt = redact_sensitive_text(prompt)
            async for event in agent.reply_stream(UserMsg(name="user", content=safe_prompt)):
                event_count += 1
                payload = serialize_event(event)
                trace.append("agentscope_event", payload, diagnostic=True)
                event_type = payload.get("type")
                if event_type == "MODEL_CALL_START":
                    summary["model_calls"] += 1
                if event_type == "EXCEED_MAX_ITERS":
                    summary["exceed_max_iters"] = True
                if event_type == "REPLY_END":
                    summary["finished_reason"] = payload.get("finished_reason")
                if event_type == "TOOL_CALL_START":
                    call_id = str(payload.get("tool_call_id"))
                    tool_names_by_id[call_id] = str(payload.get("tool_call_name"))
                    tool_result_text_by_id[call_id] = []
                    summary["tool_calls"].append(
                        {"id": payload.get("tool_call_id"), "name": payload.get("tool_call_name")}
                    )
                if event_type == "TOOL_RESULT_TEXT_DELTA" and payload.get("delta"):
                    call_id = str(payload.get("tool_call_id"))
                    parts = tool_result_text_by_id.setdefault(call_id, [])
                    if sum(len(part) for part in parts) < 20_000:
                        parts.append(str(payload["delta"]))
                if event_type == "TOOL_RESULT_END" and payload.get("state") not in (None, "success"):
                    call_id = str(payload.get("tool_call_id"))
                    message = "".join(tool_result_text_by_id.get(call_id, [])).strip()
                    error = {
                        "tool_call_id": payload.get("tool_call_id"),
                        "tool": tool_names_by_id.get(call_id),
                        "state": payload.get("state"),
                        "message": message,
                        "metadata": payload.get("metadata", {}),
                    }
                    feedback = normalize_tool_feedback(error, enabled=structured_feedback)
                    summary["tool_errors"].append(feedback)
                    trace.append("normalized_tool_error" if structured_feedback else "raw_tool_error", feedback)
                if payload.get("type") == "TEXT_BLOCK_DELTA" and payload.get("delta"):
                    final_text.append(str(payload["delta"]))
                if payload.get("type") == "REQUIRE_USER_CONFIRM":
                    summary["permission_required"] = True
                    trace.append("permission_pause", {"event": payload})
                    break
        # AgentScope can convert task cancellation into a normal-looking
        # ``interrupted`` reply.  The timeout object remains the authoritative
        # deadline signal even when the inner generator consumes cancellation.
        if timeout_context.expired():
            summary["timed_out"] = True
            summary["finished_reason"] = "runtime_timeout"
            trace.append("runtime_timeout", {"timeout_seconds": timeout_seconds, "status": "failed"})
    except TimeoutError:
        summary["timed_out"] = True
        summary["finished_reason"] = "runtime_timeout"
        trace.append("runtime_timeout", {"timeout_seconds": timeout_seconds, "status": "failed"})
    except Exception as exc:
        # A secondary trace/storage failure must not replace the causal model,
        # stream, or tool exception used by bridge error classification.
        try:
            trace.append(
                "runtime_error",
                {
                    "error_type": type(exc).__name__,
                    "error": redact_sensitive_text(str(exc)),
                    "status": "failed",
                },
            )
        except Exception:
            pass
        raise RuntimeError(f"AgentScope runtime failed: {type(exc).__name__}") from None
    safe_summary = redact_sensitive_value(summary)
    trace.append("agentscope_run_summary", safe_summary)
    return redact_sensitive_text("".join(final_text)), event_count, safe_summary


async def run_task(
    task: TaskSpec,
    *,
    workspace_root: Path,
    trace_path: Path,
    feature_config: FeatureConfig,
    model,
    max_iters: int = 20,
    append_trace: bool = False,
    memory_path: Path | None = None,
) -> RunResult:
    feature_config.validate_known()
    trace = TraceWriter(
        trace_path,
        task_id=task.task_id,
        append=append_trace,
        diagnostic_enabled=feature_config.on("F4.1"),
    )
    emit_feature_events(feature_config, trace)

    if feature_config.on("F1.2"):
        preflight = preflight_workspace(task, workspace_root, apply_reset=True)
        trace.append("preflight_result", preflight)
        if not preflight["ready"]:
            trace.append("run_abort", {"reason": "preflight_failed", "status": "failed"})
            raise RuntimeError(f"Harness preflight failed: {preflight}")
    else:
        trace.append("preflight_skipped", {"reason": "F1.2_controlled_off"})

    before_snapshot = snapshot_workspace(workspace_root) if feature_config.on("F4.2") else {}
    memory_records: list[dict[str, Any]] = []
    memory_store: PersistentMemoryStore | None = None
    if feature_config.on("F5.2"):
        memory_store = PersistentMemoryStore(memory_path or trace_path.with_suffix(".memory.json"))
        memory_records = redact_sensitive_value(memory_store.query(task.instruction))
        trace.append(
            "memory_query",
            {
                "path": str(memory_store.path),
                "query": task.instruction,
                "returned_records": memory_records,
            },
        )
    else:
        trace.append("memory_disabled", {"fresh_empty_store": True})

    aliases = enabled_tool_aliases(feature_config)
    toolkit = build_toolkit(workspace_root, feature_config, trace)
    budget = budget_policy(feature_config, max_iters)
    trace_runtime_contracts(task, aliases, feature_config, budget, trace)
    schemas = await toolkit_schema_summary(toolkit)
    trace.append(
        "action_contract",
        {
            "pawbench_validation_enabled": feature_config.on("F2.1"),
            "framework_native_schema_retained": True,
            "tools": schemas,
            "pawbench_aliases": aliases,
        },
    )
    prompt = prepare_prompt(
        task,
        workspace_root,
        feature_config,
        trace,
        memory_records=memory_records,
    )
    agent = build_agent(
        model=model,
        toolkit=toolkit,
        workspace_root=workspace_root,
        max_iters=budget["max_iters"],
        tool_result_limit=budget["tool_result_limit"],
    )
    trace.append(
        "run_start",
        {
            "workspace_root": str(workspace_root),
            "features": sorted(feature_config.enabled),
            "taxonomy_version": TAXONOMY_VERSION,
        },
    )
    final_text, event_count, runtime_summary = await _run_agent_once(
        agent,
        prompt,
        trace,
        timeout_seconds=budget["timeout_seconds"],
        structured_feedback=feature_config.on("F2.3"),
    )
    verifier = verify_artifacts(task, workspace_root, run_semantic=True)
    trace.append(
        "verifier_result",
        {
            **verifier.model_dump(),
            "verification_feature_enabled": feature_config.on("F4.3"),
        },
    )

    if not verifier.ok and feature_config.on("F3.3"):
        artifact_contract = "\n".join(f"- {path}" for path in task.required_artifacts) or "- none"
        workspace_recovery = ""
        if feature_config.on("F1.1"):
            workspace_recovery = (
                "\n\nWorkspace root alias: .\n"
                "Use task-relative paths; the harness binds them under this workspace root."
            )
        permission_recovery = ""
        if runtime_summary.get("permission_required"):
            permission_recovery = (
                "\n\nThe previous attempt paused because a tool requested interactive permission. "
                "Do not repeat that command and do not bypass the safety check. Use the dedicated Glob, Grep, "
                "Read, Write, or Edit tools instead. If Bash is strictly necessary, use one simple static command "
                "with relative workspace paths and without command substitution, process substitution, backticks, "
                "or shell loops. Keep every operation inside the workspace root."
            )
        feedback = (
            "Repair the original PawBench task after validation failed.\n\n"
            f"Original task:\n{task.instruction}\n\n"
            f"Required artifacts:\n{artifact_contract}\n\n"
            f"Validator result: {verifier.model_dump_json()}\n\n"
            "Make only the smallest required repair, verify it once, then immediately report completion."
            f"{workspace_recovery}"
            f"{permission_recovery}"
        )
        trace.append("retry_start", {"reason": "validator_failed"})
        retry_agent = build_agent(
            model=model,
            toolkit=toolkit,
            workspace_root=workspace_root,
            max_iters=budget["max_iters"],
            tool_result_limit=budget["tool_result_limit"],
        )
        retry_text, retry_events, retry_summary = await _run_agent_once(
            retry_agent,
            feedback,
            trace,
            timeout_seconds=budget["timeout_seconds"],
            structured_feedback=feature_config.on("F2.3"),
        )
        final_text += retry_text
        event_count += retry_events
        runtime_summary = {"initial": runtime_summary, "retry": retry_summary}
        verifier = verify_artifacts(task, workspace_root, run_semantic=True)
        trace.append("retry_verifier_result", verifier.model_dump())

    completion_ok, stop_reason = completion_decision(runtime_summary, enabled=feature_config.on("F3.1"))
    verification_gated = feature_config.on("F4.3")
    accepted = completion_ok and (verifier.ok if verification_gated else True)
    trace.append(
        "completion_decision",
        {
            "completion_ok": completion_ok,
            "stop_reason": stop_reason,
            "verification_gated": verification_gated,
            "verifier_ok": verifier.ok,
            "accepted": accepted,
        },
    )

    if feature_config.on("F4.2"):
        after_snapshot = snapshot_workspace(workspace_root)
        trace.append("state_artifact_delta", diff_workspace_snapshots(before_snapshot, after_snapshot))

    if memory_store is not None:
        safe_final_text = redact_sensitive_text(final_text)
        record = memory_store.upsert(
            f"task:{redact_sensitive_text(task.task_id)}",
            safe_final_text[-2_000:] or f"accepted={accepted}",
            metadata={"accepted": accepted, "verifier_ok": verifier.ok},
        )
        trace.append("memory_write", {"path": str(memory_store.path), "record": record})

    safe_final_text = redact_sensitive_text(final_text)
    safe_runtime_summary = redact_sensitive_value(runtime_summary)
    return RunResult(
        task_id=redact_sensitive_text(task.task_id),
        run_id=trace.run_id,
        accepted=accepted,
        completion_ok=completion_ok,
        verification_gated=verification_gated,
        verifier=verifier,
        trace_path=trace.path,
        workspace_root=workspace_root,
        final_text=safe_final_text,
        event_count=event_count,
        runtime_summary=safe_runtime_summary,
        taxonomy_version=TAXONOMY_VERSION,
        enabled_features=sorted(feature_config.enabled),
    )


def run_task_sync(*args: Any, **kwargs: Any) -> RunResult:
    return asyncio.run(run_task(*args, **kwargs))


def is_agentscope_model_like(obj: object) -> bool:
    return inspect.iscoroutinefunction(getattr(obj, "_call_api", None)) or callable(obj)
