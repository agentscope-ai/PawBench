from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit


ProviderName = Literal["dashscope", "openai", "generic"]


@dataclass(frozen=True)
class ProviderSettings:
    provider: ProviderName
    api_key: str
    base_url: str


_PROVIDER_ENV: dict[ProviderName, tuple[tuple[str, ...], tuple[str, ...], str | None]] = {
    "dashscope": (
        (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "ALIYUN_API_KEY",
            "ALIBABA_CLOUD_API_KEY",
        ),
        (
            "DASHSCOPE_BASE_URL",
            "BAILIAN_BASE_URL",
            "ALIYUN_BASE_URL",
            "ALIBABA_CLOUD_BASE_URL",
        ),
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "openai": (
        ("OPENAI_API_KEY",),
        ("OPENAI_BASE_URL",),
        "https://api.openai.com/v1",
    ),
    "generic": (
        ("LLM_API_KEY",),
        ("LLM_BASE_URL",),
        None,
    ),
}

_SENSITIVE_NAME = (
    r"(?:api[_-]?key|[a-z0-9_]+_api_key|token|[a-z0-9_]+_token|"
    r"password|passwd|[a-z0-9_]+_password|secret|[a-z0-9_]+_secret|"
    r"authorization|proxy_authorization|private[_-]?key|[a-z0-9_]+_private_key|"
    r"aws_access_key_id|aws_secret_access_key|aws_session_token|github_token|"
    r"judge_api_key)"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_ESCAPED_QUOTED_ASSIGNMENT = re.compile(
    rf"(?i)(\\[\"']{_SENSITIVE_NAME}\\[\"']\s*[:=]\s*\\[\"'])(.*?)(\\[\"'])"
)
_ESCAPED_VALUE_ASSIGNMENT = re.compile(
    rf"(?i)([\"']?{_SENSITIVE_NAME}[\"']?\s*[:=]\s*\\[\"'])(.*?)(\\[\"'])"
)
_QUOTED_ASSIGNMENT = re.compile(
    rf"(?i)([\"']?{_SENSITIVE_NAME}[\"']?\s*[:=]\s*)([\"'])([^\"'\r\n]*)([\"'])"
)
_UNQUOTED_ASSIGNMENT = re.compile(
    rf"(?i)([\"']?{_SENSITIVE_NAME}[\"']?\s*[:=]\s*)([^\s,;\}}\]\[\\\"']+)"
)
_SENSITIVE_ASSIGNMENT_HINT = re.compile(
    r"(?i)(?:api[_-]?key|token|password|passwd|secret|authorization|private[_-]?key)"
)
_TOKEN_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bnvapi-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bLTAI[A-Za-z0-9]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)

_SENSITIVE_MAPPING_NAMES = {
    "api_key",
    "authorization",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "password",
    "passwd",
    "private_key",
    "privatekey",
    "proxy_authorization",
    "secret",
    "token",
}
_CREDENTIAL_ENV_NAMES = {
    "aws_default_profile",
    "aws_profile",
    "azure_config_dir",
    "docker_config",
    "google_application_credentials",
    "gpg_agent_info",
    "kubeconfig",
    "netrc",
    "ssh_auth_sock",
}
_SAFE_SUBPROCESS_ENV_NAMES = {
    "COLORTERM",
    "COMSPEC",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TERM",
    "TZ",
    "WINDIR",
}


def _first_nonempty(env: Mapping[str, str], names: Sequence[str]) -> str | None:
    for name in names:
        value = env.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _normalized_name(value: Any) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value))
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def _is_sensitive_mapping_key(key: Any) -> bool:
    name = _normalized_name(key)
    return (
        name in _SENSITIVE_MAPPING_NAMES
        or name.endswith(("_api_key", "_token", "_secret", "_password", "_private_key"))
        or name.startswith("aws_")
        and name.endswith(("_key", "_key_id", "_token"))
    )


def _is_credential_env_name(name: str) -> bool:
    normalized = _normalized_name(name)
    return (
        _is_sensitive_mapping_key(normalized)
        or normalized in _CREDENTIAL_ENV_NAMES
        or normalized.endswith(("_credential", "_credentials", "_jwt"))
    )


def _validated_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("Provider base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Provider base URL cannot contain credentials, query parameters, or fragments")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Provider base URL must use HTTPS unless it targets a local loopback host")
    return base_url


def resolve_openai_compatible_provider(
    env: Mapping[str, str],
    *,
    allowed_providers: Sequence[ProviderName] = ("dashscope", "openai", "generic"),
) -> ProviderSettings:
    """Resolve a key and endpoint from the same provider namespace."""

    for provider in allowed_providers:
        key_names, url_names, default_url = _PROVIDER_ENV[provider]
        api_key = _first_nonempty(env, key_names)
        if not api_key:
            continue
        base_url = _first_nonempty(env, url_names) or default_url
        if not base_url:
            raise RuntimeError("LLM_BASE_URL is required when LLM_API_KEY is used")
        return ProviderSettings(
            provider=provider,
            api_key=api_key,
            base_url=_validated_base_url(base_url),
        )

    expected = ", ".join(
        key_name
        for provider in allowed_providers
        for key_name in _PROVIDER_ENV[provider][0]
    )
    raise RuntimeError(f"Missing API key. Expected one of: {expected}")


def redact_sensitive_text(text: str) -> str:
    value = text.replace(str(Path.home()), "$HOME")
    value = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", value)
    for pattern in _TOKEN_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    # The assignment expressions intentionally support arbitrary prefixes such
    # as ``provider_api_key``. On long non-sensitive output, trying those
    # prefix alternatives at every character causes quadratic backtracking.
    # A linear hint scan preserves the same redaction behavior while skipping
    # the expensive expressions when no sensitive assignment name can occur.
    if _SENSITIVE_ASSIGNMENT_HINT.search(value):
        value = _ESCAPED_QUOTED_ASSIGNMENT.sub(r"\1[REDACTED]\3", value)
        value = _ESCAPED_VALUE_ASSIGNMENT.sub(r"\1[REDACTED]\3", value)
        value = _QUOTED_ASSIGNMENT.sub(r"\1\2[REDACTED]\4", value)
        value = _UNQUOTED_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    return value


def redact_sensitive_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Path):
        return redact_sensitive_text(str(value))
    if isinstance(value, (bytes, bytearray)):
        return redact_sensitive_text(bytes(value).decode("utf-8", errors="replace"))
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            safe_key = redact_sensitive_text(key) if isinstance(key, str) else key
            redacted[safe_key] = (
                "[REDACTED]"
                if _is_sensitive_mapping_key(key)
                else redact_sensitive_value(item)
            )
        return redacted
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_sensitive_text(str(value))


def safe_subprocess_env(
    workspace_root: str | Path,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal subprocess environment without inherited credentials."""

    workspace = Path(workspace_root).expanduser().resolve()
    env: dict[str, str] = {"PATH": os.environ.get("PATH", os.defpath)}
    for name, value in os.environ.items():
        if (name in _SAFE_SUBPROCESS_ENV_NAMES or name.startswith("LC_")) and not _is_credential_env_name(name):
            env[name] = value

    if extra:
        for name, value in extra.items():
            if _is_credential_env_name(name):
                raise ValueError(f"Credential-like environment variable is not allowed: {name}")
            env[str(name)] = str(value)

    workspace_value = str(workspace)
    env.update(
        {
            "HOME": workspace_value,
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": workspace_value,
            "TMP": workspace_value,
            "TEMP": workspace_value,
        }
    )
    return env


def safe_provider_error(
    *,
    status_code: int | None = None,
    headers: Mapping[str, Any] | None = None,
    exc: BaseException | None = None,
) -> str:
    """Return provider diagnostics without persisting response bodies or exception text."""

    if status_code is not None:
        request_id = None
        if headers:
            for name in ("x-request-id", "request-id", "x-dashscope-request-id"):
                value = headers.get(name)
                if value:
                    request_id = redact_sensitive_text(str(value))[:160]
                    break
        suffix = f" (request_id={request_id})" if request_id else ""
        return f"HTTP {status_code}{suffix}"
    if exc is not None:
        return type(exc).__name__
    return "ProviderError"
