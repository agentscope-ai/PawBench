"""Secret-safe fixed route for Qwen3.8-Max through Claude Code."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


DEFAULT_CODING_MODEL = "qwen3.8-max"
DEFAULT_CODING_HARNESS = "claude-code"
DEFAULT_TIMEOUT_SECONDS = 600.0


class ClaudeCodeRouteError(RuntimeError):
    """The local Claude Code / DashScope route is not safely configured."""


def _base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ClaudeCodeRouteError("model gateway URL must be one absolute HTTPS URL")
    return normalized


def _explicit_anthropic_base_url(value: str) -> str:
    normalized = _base_url(value)
    if not urlsplit(normalized).path.rstrip("/").endswith("/apps/anthropic"):
        raise ClaudeCodeRouteError(
            "Anthropic gateway URL must end with /apps/anthropic"
        )
    return normalized


def _derive_dashscope_anthropic_base_url(value: str) -> str:
    """Map DashScope's OpenAI-compatible endpoint to its Anthropic peer."""

    normalized = _base_url(value)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname or ""
    if not hostname.endswith(".aliyuncs.com") and hostname != "aliyuncs.com":
        raise ClaudeCodeRouteError(
            "OpenAI fallback URL is not a recognized DashScope host"
        )
    path = parsed.path.rstrip("/")
    for suffix in ("/compatible-mode/v1", "/v1"):
        if path.endswith(suffix):
            prefix = path[: -len(suffix)]
            return urlunsplit(
                (parsed.scheme, parsed.netloc, f"{prefix}/apps/anthropic", "", "")
            )
    raise ClaudeCodeRouteError(
        "OpenAI fallback URL cannot be mapped to an Anthropic gateway"
    )


def resolve_anthropic_base_url(environ: dict[str, str] | None = None) -> str:
    """Resolve the configured DashScope Anthropic gateway without logging it."""

    values = os.environ if environ is None else environ
    explicit = values.get("DASHSCOPE_ANTHROPIC_BASE_URL")
    if explicit:
        return _explicit_anthropic_base_url(explicit)
    fallback = values.get("DASHSCOPE_BASE_URL")
    if fallback:
        return _derive_dashscope_anthropic_base_url(fallback)
    raise ClaudeCodeRouteError(
        "DASHSCOPE_ANTHROPIC_BASE_URL or DASHSCOPE_BASE_URL is required"
    )


def resolve_claude_executable() -> str:
    executable = shutil.which("claude")
    if executable is None:
        raise ClaudeCodeRouteError("Claude Code CLI is not installed or not on PATH")
    return executable


def build_child_environment(
    *,
    home_dir: Path,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build an isolated, gateway-only child environment.

    Only the DashScope key is mapped to Claude's Anthropic environment. Other
    machine credentials (including code-hosting tokens) are intentionally not
    inherited by the coding agent.
    """

    values = os.environ if environ is None else environ
    api_key = values.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise ClaudeCodeRouteError("DASHSCOPE_API_KEY is required for qwen3.8-max")
    base_url = resolve_anthropic_base_url(values)
    resolved_home = home_dir.expanduser().resolve()
    resolved_home.mkdir(parents=True, exist_ok=True)
    child = {
        key: values[key]
        for key in ("PATH", "LANG", "LC_ALL", "TERM", "TZ")
        if values.get(key)
    }
    child["HOME"] = str(resolved_home)
    child["XDG_CONFIG_HOME"] = str(resolved_home / ".config")
    child["XDG_CACHE_HOME"] = str(resolved_home / ".cache")
    child["ANTHROPIC_BASE_URL"] = base_url
    child["ANTHROPIC_API_KEY"] = api_key
    child["ANTHROPIC_CUSTOM_MODEL_OPTION"] = DEFAULT_CODING_MODEL
    child["ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"] = DEFAULT_CODING_MODEL
    child["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "8000"
    child["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "0"
    child["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    child["DISABLE_AUTOUPDATER"] = "1"
    child["DISABLE_TELEMETRY"] = "1"
    child["DISABLE_ERROR_REPORTING"] = "1"
    return child


__all__ = [
    "ClaudeCodeRouteError",
    "DEFAULT_CODING_HARNESS",
    "DEFAULT_CODING_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "build_child_environment",
    "resolve_anthropic_base_url",
    "resolve_claude_executable",
]
