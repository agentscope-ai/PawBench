from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.security import (
    redact_sensitive_text,
    redact_sensitive_value,
    resolve_openai_compatible_provider,
    safe_provider_error,
    safe_subprocess_env,
)


def test_redacts_structured_and_token_secrets() -> None:
    aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
    github_token = "ghp_" + "abcdefghijklmnopqrstuvwxyz"
    openai_key = "sk-" + "example123456789"
    aliyun_key = "LTAI" + "5tExampleCredential"
    source = "\n".join(
        (
            f'{{"password": "secret-value", "AWS_ACCESS_KEY_ID": "{aws_key}"}}',
            rf'{{\"github_token\": \"{github_token}\"}}',
            "Authorization: Bearer header.payload.signature-value",
            f"api_key={openai_key}",
            f"aliyun={aliyun_key}",
        )
    )

    redacted = redact_sensitive_text(source)

    for secret in (
        "secret-value",
        aws_key,
        github_token,
        "header.payload.signature-value",
        openai_key,
        aliyun_key,
    ):
        assert secret not in redacted


def test_redacts_home_directory_and_private_key() -> None:
    home = str(Path.home())
    marker = "-" * 5
    begin = f"{marker}BEGIN PRIVATE KEY{marker}"
    end = f"{marker}END PRIVATE KEY{marker}"
    source = (
        f"{home}/project\n"
        f"{begin}\nabc123\n{end}"
    )

    redacted = redact_sensitive_text(source)

    assert home not in redacted
    assert "$HOME/project" in redacted
    assert "abc123" not in redacted


def test_redacts_extended_token_formats_and_aws_session_assignment() -> None:
    basic = "dXNlcjpwYXNzd29yZA=="
    hf_token = "hf_" + "abcdefghijklmnopqrstuvwxyz"
    gitlab_token = "glpat-" + "abcdefghijklmnopqrstuvwxyz"
    stripe_token = "sk_live_" + "abcdefghijklmnopqrstuvwxyz"
    nvidia_token = "nvapi-" + "abcdefghijklmnopqrstuvwxyz"
    aws_session = "session-token-value-123456"
    source = "\n".join(
        (
            f"Authorization: Basic {basic}",
            hf_token,
            gitlab_token,
            stripe_token,
            nvidia_token,
            f'AWS_SESSION_TOKEN="{aws_session}"',
        )
    )

    redacted = redact_sensitive_text(source)

    for secret in (basic, hf_token, gitlab_token, stripe_token, nvidia_token, aws_session):
        assert secret not in redacted


def test_redaction_preserves_serialized_json_with_escaped_assignments() -> None:
    secret = "opaque-credential-value"
    source = {
        "evidence": f'api_key=\"{secret}\"',
        "nested_json": json.dumps({"api_key": secret}),
    }

    serialized = json.dumps(redact_sensitive_value(source))
    redacted = redact_sensitive_text(serialized)
    parsed = json.loads(redacted)

    assert secret not in redacted
    assert "[REDACTED]" in parsed["evidence"]
    assert "[REDACTED]" in parsed["nested_json"]


def test_redacts_path_and_bytes_values() -> None:
    redacted = redact_sensitive_value(
        {
            "path": Path.home() / "private" / "result.json",
            "raw": b"sk-example123456789",
        }
    )

    assert str(Path.home()) not in redacted["path"]
    assert "sk-example" not in redacted["raw"]


def test_redacts_unknown_objects_before_json_stringification() -> None:
    class LeakyObject:
        def __str__(self) -> str:
            return "OPENAI_API_KEY=sk-example123456789"

    redacted = redact_sensitive_value({"value": LeakyObject()})

    assert "sk-example" not in redacted["value"]


def test_redacts_sensitive_values_by_mapping_key() -> None:
    source = {
        "password": "plain-password",
        "DASHSCOPE_API_KEY": "dash-secret",
        "github_token": "github-secret",
        "client_secret": "client-secret",
        "Authorization": "Basic credential",
        "private key": "private-material",
        "AWS_ACCESS_KEY_ID": "aws-access",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
        "AWS_SESSION_TOKEN": "aws-session",
        "nested": [{"HF_TOKEN": "hf-secret", "visible": "keep-me"}],
        "visible": "keep-me-too",
    }

    redacted = redact_sensitive_value(source)

    for key in (
        "password",
        "DASHSCOPE_API_KEY",
        "github_token",
        "client_secret",
        "Authorization",
        "private key",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        assert redacted[key] == "[REDACTED]"
    assert redacted["nested"] == [{"HF_TOKEN": "[REDACTED]", "visible": "keep-me"}]
    assert redacted["visible"] == "keep-me-too"


def test_provider_key_and_url_stay_in_the_same_namespace() -> None:
    settings = resolve_openai_compatible_provider(
        {
            "DASHSCOPE_API_KEY": "dash-key",
            "OPENAI_BASE_URL": "https://unrelated.example/v1",
        }
    )

    assert settings.provider == "dashscope"
    assert settings.api_key == "dash-key"
    assert settings.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_openai_key_uses_the_openai_default() -> None:
    settings = resolve_openai_compatible_provider({"OPENAI_API_KEY": "openai-key"})

    assert settings.provider == "openai"
    assert settings.base_url == "https://api.openai.com/v1"


def test_generic_key_requires_an_explicit_endpoint() -> None:
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        resolve_openai_compatible_provider({"LLM_API_KEY": "generic-key"})


@pytest.mark.parametrize(
    "base_url",
    (
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
        "https://provider.example/v1",
    ),
)
def test_accepts_https_and_loopback_http_provider_urls(base_url: str) -> None:
    settings = resolve_openai_compatible_provider(
        {"LLM_API_KEY": "generic-key", "LLM_BASE_URL": base_url}
    )

    assert settings.base_url == base_url


@pytest.mark.parametrize(
    "base_url",
    (
        "dashscope.example/v1",
        "http://provider.example/v1",
        "http://localhost.example/v1",
        "http://127.0.0.2:8000/v1",
        "https://user:" + "password@example.com/v1",
        "https://example.com/v1?" + "api_" + "key=secret",
        "https://example.com/v1#secret",
    ),
)
def test_rejects_unsafe_provider_urls(base_url: str) -> None:
    with pytest.raises(RuntimeError):
        resolve_openai_compatible_provider(
            {"LLM_API_KEY": "generic-key", "LLM_BASE_URL": base_url}
        )


def test_provider_error_never_contains_response_body_or_exception_text() -> None:
    http_error = safe_provider_error(
        status_code=401,
        headers={"x-request-id": "request-123"},
    )
    exception_error = safe_provider_error(exc=RuntimeError("secret response body"))

    assert http_error == "HTTP 401 (request_id=request-123)"
    assert exception_error == "RuntimeError"
    assert "secret response body" not in exception_error


def test_safe_subprocess_env_keeps_only_safe_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("PATH", "/safe/bin")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "zh_CN.UTF-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "do-not-inherit")
    monkeypatch.setenv("AWS_PROFILE", "do-not-inherit")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("UNRELATED_PARENT_VALUE", "do-not-inherit")

    env = safe_subprocess_env(
        workspace,
        extra={"HARNESS_MODEL_NAME": "test-model", "HOME": "/unsafe-home"},
    )

    assert env["PATH"] == "/safe/bin"
    assert env["LANG"] == "zh_CN.UTF-8"
    assert env["LC_MESSAGES"] == "zh_CN.UTF-8"
    assert env["HARNESS_MODEL_NAME"] == "test-model"
    assert env["HOME"] == str(workspace.resolve())
    assert env["TMPDIR"] == str(workspace.resolve())
    assert env["TMP"] == str(workspace.resolve())
    assert env["TEMP"] == str(workspace.resolve())
    for name in ("DASHSCOPE_API_KEY", "AWS_PROFILE", "SSH_AUTH_SOCK", "UNRELATED_PARENT_VALUE"):
        assert name not in env


@pytest.mark.parametrize(
    "name",
    (
        "OPENAI_API_KEY",
        "HF_TOKEN",
        "CLIENT_SECRET",
        "Authorization",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ),
)
def test_safe_subprocess_env_rejects_explicit_credentials(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="Credential-like environment variable"):
        safe_subprocess_env(tmp_path, extra={name: "secret"})
