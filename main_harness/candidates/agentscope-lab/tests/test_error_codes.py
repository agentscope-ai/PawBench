from __future__ import annotations

import json
from pathlib import Path

import pytest

from pawbench_agentscope.error_codes import classify_bridge_error
from pawbench_agentscope.harbor_bridge import main
from pawbench_agentscope.harbor_contract import validate_result_contract


def test_classifies_invalid_feature_as_nonretryable_configuration() -> None:
    value = classify_bridge_error(
        error_type="ValueError",
        error="unknown Feature IDs: ['F9.9']",
    )
    assert value.error_code == "HC_CONFIG_INVALID_FEATURE"
    assert value.failure_scope == "configuration"
    assert value.retryable is False


def test_classifies_provider_model_not_found_from_runtime_context() -> None:
    value = classify_bridge_error(
        error_type="RuntimeError",
        error="AgentScope runtime failed: NotFoundError",
        runtime_context={
            "error_type": "NotFoundError",
            "error": "Error code: 404; code=model_not_found",
        },
    )
    assert value.error_code == "HC_PROVIDER_MODEL_NOT_FOUND"
    assert value.failure_scope == "external_provider"
    assert value.retryable is False
    assert value.cause_type == "NotFoundError"


def test_classifies_rate_limit_and_unavailable_as_retryable() -> None:
    rate = classify_bridge_error(
        error_type="RateLimitError",
        error="Error code: 429",
    )
    unavailable = classify_bridge_error(
        error_type="InternalServerError",
        error="Error code: 503 service unavailable",
    )
    assert rate.error_code == "HC_PROVIDER_RATE_LIMIT"
    assert rate.retryable is True
    assert unavailable.error_code == "HC_PROVIDER_UNAVAILABLE"
    assert unavailable.retryable is True


@pytest.mark.parametrize(
    ("error_type", "error", "expected_code", "scope", "retryable"),
    [
        (
            "ValueError",
            "unknown Feature IDs: ['F9.9']",
            "HC_CONFIG_INVALID_FEATURE",
            "configuration",
            False,
        ),
        (
            "FileNotFoundError",
            "instruction file is absent",
            "HC_INPUT_CONTRACT_INVALID",
            "configuration",
            False,
        ),
        (
            "RuntimeError",
            "Preflight failed: workspace is not writable",
            "HC_PREFLIGHT_FAILED",
            "harness_runtime",
            False,
        ),
        (
            "NotFoundError",
            "code=model_not_found",
            "HC_PROVIDER_MODEL_NOT_FOUND",
            "external_provider",
            False,
        ),
        (
            "PermissionDeniedError",
            "Error code: 403 forbidden",
            "HC_PROVIDER_AUTH",
            "external_provider",
            False,
        ),
        (
            "RateLimitError",
            "Error code: 429 rate limit",
            "HC_PROVIDER_RATE_LIMIT",
            "external_provider",
            True,
        ),
        (
            "APIConnectionError",
            "connection reset",
            "HC_PROVIDER_UNAVAILABLE",
            "external_provider",
            True,
        ),
        (
            "TimeoutError",
            "runtime timeout",
            "HC_RUNTIME_TIMEOUT",
            "harness_runtime",
            True,
        ),
        (
            "RuntimeError",
            "unexpected scheduler state",
            "HC_RUNTIME_ERROR",
            "harness_runtime",
            False,
        ),
    ],
)
def test_complete_error_code_matrix(
    error_type: str,
    error: str,
    expected_code: str,
    scope: str,
    retryable: bool,
) -> None:
    value = classify_bridge_error(error_type=error_type, error=error)
    assert value.error_code == expected_code
    assert value.failure_scope == scope
    assert value.retryable is retryable


@pytest.mark.parametrize(
    ("error_type", "error", "expected_code"),
    [
        ("ContractValidationError", "Input schema mismatch: artifact_path is null", "HC_INPUT_CONTRACT_INVALID"),
        ("FileNotFoundException", "Local input file task.json does not exist", "HC_INPUT_CONTRACT_INVALID"),
        ("TypeCoercionError", "Task contract violation: retry_count must be integer", "HC_INPUT_CONTRACT_INVALID"),
        ("MalformedPayloadException", "Input YAML contains invalid indentation", "HC_INPUT_CONTRACT_INVALID"),
        ("WorkspaceInitError", "Failed to initialize workspace directory", "HC_PREFLIGHT_FAILED"),
        ("DependencyCheckFailure", "Required binary kubectl not found during preflight validation", "HC_PREFLIGHT_FAILED"),
        ("ReadinessProbeError", "Agent readiness check failed", "HC_PREFLIGHT_FAILED"),
        ("EnvironmentValidationError", "Preflight environment check failed", "HC_PREFLIGHT_FAILED"),
        ("TokenExpiredError", "OAuth token expired; refresh returned HTTP 401", "HC_PROVIDER_AUTH"),
        ("CredentialValidationError", "Provider rejected client certificate: CN mismatch", "HC_PROVIDER_AUTH"),
        ("ModelNotFoundException", "HTTP 404: model demo does not exist", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("ResourceAbsentError", "Requested embedding model is not available in this region", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("EndpointMissingException", "Model endpoint demo returned HTTP 404", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("InferenceTargetError", "Provider reports model demo as decommissioned", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("QuotaExhaustedError", "Monthly token quota exhausted", "HC_PROVIDER_RATE_LIMIT"),
        ("ThrottlingResponseException", "Provider returned HTTP 429 with Retry-After", "HC_PROVIDER_RATE_LIMIT"),
        ("ConcurrencyCapError", "HTTP 429: concurrent request limit reached", "HC_PROVIDER_RATE_LIMIT"),
        ("ExecutionDeadlineExceeded", "Task execution exceeded 300s budget", "HC_RUNTIME_TIMEOUT"),
        ("WatchdogTimeoutException", "Runtime watchdog killed unresponsive worker", "HC_RUNTIME_TIMEOUT"),
        ("BudgetExhaustedError", "Global runtime budget exhausted", "HC_RUNTIME_TIMEOUT"),
        ("LocalPathException", "Specified input path './data/batch_input.csv' does not exist or is not readable", "HC_INPUT_CONTRACT_INVALID"),
        ("TypeMismatchFault", "Task contract expects array of strings for tags but received integer", "HC_INPUT_CONTRACT_INVALID"),
        ("WorkspaceReadinessFault", "Preflight check failed: workspace has insufficient write permissions", "HC_PREFLIGHT_FAILED"),
        ("DependencyCheckError", "Required dependency libssl.so.3 not found during preflight", "HC_PREFLIGHT_FAILED"),
        ("ReadinessProbeFailure", "Agent readiness probe failed before task dispatch", "HC_PREFLIGHT_FAILED"),
        ("EnvironmentValidationFault", "Disk space preflight check failed: minimum capacity required", "HC_PREFLIGHT_FAILED"),
        ("ModelAccessError", "Model demo is deprecated and inaccessible for new requests", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("EndpointResolutionFault", "No serving endpoint registered for model demo", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("DeadlineExceededFault", "Global deadline passed before task completion signal", "HC_RUNTIME_TIMEOUT"),
        ("MalformedArtifactError", "Input file header indicates unsupported binary format v3; only v1 and v2 are accepted", "HC_INPUT_CONTRACT_INVALID"),
        ("ValidationError", "Input path '/tmp/payload.bin' does not match expected MIME type application/json for task contract v3", "HC_INPUT_CONTRACT_INVALID"),
        ("ContractMismatch", "Local file schema validation failed: missing required field 'correlation_id'", "HC_INPUT_CONTRACT_INVALID"),
        ("ReadinessCheckError", "Workspace initialization failed: unable to create staging directory due to insufficient disk quota", "HC_PREFLIGHT_FAILED"),
        ("DependencyResolutionFault", "Required system library 'libcrypto.so.1.1' missing during pre-execution validation", "HC_PREFLIGHT_FAILED"),
        ("HealthProbeTimeout", "Liveness endpoint returned HTTP 503; workspace not ready for task ingestion", "HC_PREFLIGHT_FAILED"),
        ("EnvironmentSetupError", "Preflight script exited: required environment variable AGENT_REGION is unset", "HC_PREFLIGHT_FAILED"),
        ("ReadinessCheckError", "Workspace dependency 'libssl.so.3' missing during preflight initialization", "HC_PREFLIGHT_FAILED"),
        ("EnvironmentFault", "Preflight health check returned HTTP 503 from local readiness endpoint", "HC_PREFLIGHT_FAILED"),
        ("CredentialValidationError", "Mutual TLS handshake failed: client certificate CN does not match authorized principal list", "HC_PROVIDER_AUTH"),
        ("AccessDeniedException", "Authorization rejected: API key lacks inference scope", "HC_PROVIDER_AUTH"),
        ("CatalogLookupFailure", "Provider registry indicates model ID demo has been deprecated and removed from API", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("IdleTimeoutFault", "No progress heartbeat received from worker; assumed hung and force-killed", "HC_RUNTIME_TIMEOUT"),
        ("GlobalTimerExpired", "End-to-end pipeline budget consumed before final aggregation", "HC_RUNTIME_TIMEOUT"),
        ("DeadlineExceeded", "Harness runtime deadline reached; pending operation cancelled", "HC_RUNTIME_TIMEOUT"),
        ("FileTypeError", "Unsupported file type '.parquet' for input artifact", "HC_INPUT_CONTRACT_INVALID"),
        ("InputSchemaError", "Input schema validation failed: missing required field 'prompt'", "HC_INPUT_CONTRACT_INVALID"),
        ("InvalidArtifactPathException", "Local path does not match declared input contract schema", "HC_INPUT_CONTRACT_INVALID"),
        ("CorruptInputDataException", "Input artifact validation failed: checksum mismatch", "HC_INPUT_CONTRACT_INVALID"),
        ("UnexpectedFieldsException", "Input JSON contains undeclared fields", "HC_INPUT_CONTRACT_INVALID"),
        ("PreflightCheckError", "Preflight check gpu_available returned false", "HC_PREFLIGHT_FAILED"),
        ("AuthorizationError", "Insufficient permissions to access requested model", "HC_PROVIDER_AUTH"),
        ("OAuthTokenError", "OAuth2 token refresh failed: grant is revoked", "HC_PROVIDER_AUTH"),
        ("CredentialError", "Service account not authorized for requested region", "HC_PROVIDER_AUTH"),
        ("SourceIpRejected", "Provider returned 403: client IP matched denylist", "HC_PROVIDER_AUTH"),
        ("DeploymentNotFoundError", "Deployment demo does not exist in project", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("EndpointNotFoundError", "Endpoint demo has been deleted or never created", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("ModelVersionError", "Model version demo is not accessible", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("ModelEndpointNotFoundException", "Provider responded 404: model demo unavailable in region", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("ModelRegionInaccessible", "Provider returned 404: model demo not deployed in requested region", "HC_PROVIDER_MODEL_NOT_FOUND"),
        ("ConcurrencyLimitError", "Concurrency limit of 5 reached for deployment", "HC_PROVIDER_RATE_LIMIT"),
        ("DailyQuotaExhausted", "Provider returned 429: daily quota consumed", "HC_PROVIDER_RATE_LIMIT"),
        ("ServiceOverloadedException", "Provider returned 503 Service Unavailable with x-retry-after header", "HC_PROVIDER_UNAVAILABLE"),
        ("InternalServerError", "Unhandled exception in pipeline executor: null pointer", "HC_RUNTIME_ERROR"),
        ("TaskTimeout", "Task inference-42 exceeded budget of 30s", "HC_RUNTIME_TIMEOUT"),
        ("StepTimeout", "Pipeline step embedding_generation timed out after 120s", "HC_RUNTIME_TIMEOUT"),
        ("ExecutionTimeout", "Overall execution exceeded max duration of 600s", "HC_RUNTIME_TIMEOUT"),
        ("HeartbeatLost", "Worker heartbeat lost; task assumed dead", "HC_RUNTIME_TIMEOUT"),
        ("QueueLatencyTimeout", "max_queue_time exceeded; task expired waiting for execution slot", "HC_RUNTIME_TIMEOUT"),
    ],
)
def test_llm_fuzz_clear_boundary_phrasings(
    error_type: str,
    error: str,
    expected_code: str,
) -> None:
    value = classify_bridge_error(
        error_type="RuntimeError",
        error="AgentScope runtime failed",
        runtime_context={"error_type": error_type, "error": error},
    )
    assert value.error_code == expected_code


def test_arbitrary_sdk_feature_flag_is_not_a_harness_feature_code() -> None:
    value = classify_bridge_error(
        error_type="ConfigurationError",
        error="Feature flag enable_experimental_cache_v2 is not recognized",
    )
    assert value.error_code == "HC_RUNTIME_ERROR"


def test_bridge_output_serialization_failure_is_harness_runtime_not_input() -> None:
    value = classify_bridge_error(
        error_type="_BridgeOutputError",
        error="could not serialize standard JSON output: result.json",
    )
    assert value.error_code == "HC_RUNTIME_ERROR"
    assert value.failure_scope == "harness_runtime"


def test_external_artifact_upload_timeout_is_not_harness_budget_timeout() -> None:
    value = classify_bridge_error(
        error_type="IODeadlineError",
        error="Artifact upload timed out waiting for S3 multipart completion",
    )
    assert value.error_code != "HC_RUNTIME_TIMEOUT"


def _read_error_result(logs: Path) -> dict:
    value = json.loads((logs / "result.json").read_text(encoding="utf-8"))
    assert validate_result_contract(value) == []
    return value


def test_missing_instruction_file_still_writes_coded_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logs = tmp_path / "logs" / "agent"
    exit_code = main(
        [
            "--task-id",
            "ua-missing-instruction",
            "--instruction-file",
            str(tmp_path / "missing.md"),
            "--workspace-root",
            str(workspace),
            "--logs-dir",
            str(logs),
        ]
    )
    result = _read_error_result(logs)
    assert exit_code == 1
    assert result["error_code"] == "HC_INPUT_CONTRACT_INVALID"
    assert result["task_id"] == "ua-missing-instruction"


def test_invalid_task_id_is_sanitized_in_error_envelope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logs = tmp_path / "logs" / "agent"
    exit_code = main(
        [
            "--task-id",
            "../escape",
            "--instruction",
            "test",
            "--workspace-root",
            str(workspace),
            "--logs-dir",
            str(logs),
        ]
    )
    result = _read_error_result(logs)
    assert exit_code == 1
    assert result["error_code"] == "HC_INPUT_CONTRACT_INVALID"
    assert result["task_id"] == "escape"
