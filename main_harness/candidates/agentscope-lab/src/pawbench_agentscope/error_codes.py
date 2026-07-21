"""Stable boundary error codes for Harbor retry and attribution routing."""

from __future__ import annotations

import re
from typing import Any, Mapping

from pydantic import BaseModel


ERROR_SCHEMA_VERSION = "harness-core-error-codes/v1"

HC_CONFIG_INVALID_FEATURE = "HC_CONFIG_INVALID_FEATURE"
HC_INPUT_CONTRACT_INVALID = "HC_INPUT_CONTRACT_INVALID"
HC_PREFLIGHT_FAILED = "HC_PREFLIGHT_FAILED"
HC_PROVIDER_MODEL_NOT_FOUND = "HC_PROVIDER_MODEL_NOT_FOUND"
HC_PROVIDER_AUTH = "HC_PROVIDER_AUTH"
HC_PROVIDER_RATE_LIMIT = "HC_PROVIDER_RATE_LIMIT"
HC_PROVIDER_UNAVAILABLE = "HC_PROVIDER_UNAVAILABLE"
HC_RUNTIME_TIMEOUT = "HC_RUNTIME_TIMEOUT"
HC_RUNTIME_ERROR = "HC_RUNTIME_ERROR"

ERROR_CODES = {
    HC_CONFIG_INVALID_FEATURE,
    HC_INPUT_CONTRACT_INVALID,
    HC_PREFLIGHT_FAILED,
    HC_PROVIDER_MODEL_NOT_FOUND,
    HC_PROVIDER_AUTH,
    HC_PROVIDER_RATE_LIMIT,
    HC_PROVIDER_UNAVAILABLE,
    HC_RUNTIME_TIMEOUT,
    HC_RUNTIME_ERROR,
}


def _has_http_status(text: str, *codes: int) -> bool:
    values = "|".join(str(code) for code in codes)
    return bool(
        re.search(
            rf"(?:http|status(?:[_ ]code)?|error[_ ]code)\s*[:=]?\s*(?:{values})\b"
            rf"|(?:provider|upstream)\s+(?:responded|returned)\s+(?:{values})\b",
            text,
        )
    )


class BridgeErrorClassification(BaseModel):
    error_schema_version: str = ERROR_SCHEMA_VERSION
    error_code: str
    failure_scope: str
    retryable: bool
    cause_type: str


def classify_bridge_error(
    *,
    error_type: str,
    error: str,
    runtime_context: Mapping[str, Any] | None = None,
) -> BridgeErrorClassification:
    """Classify a redacted bridge exception without exposing provider payloads."""

    context = runtime_context or {}
    cause_type = str(context.get("error_type") or error_type or "UnknownError")
    cause_error = str(context.get("error") or "")
    text = f"{error_type} {error} {cause_type} {cause_error}".lower()
    normalized_cause_type = cause_type.rsplit(".", 1)[-1].lower()

    if "unknown feature id" in text or "unknown feature ids" in text:
        code, scope, retryable = HC_CONFIG_INVALID_FEATURE, "configuration", False
    elif (
        "preflight failed" in text
        or "preflight check" in text
        or "preflight environment check failed" in text
        or (
            "preflight" in text
            and any(
                marker in text
                for marker in (
                    "dependency",
                    "environment variable",
                    "health check",
                    "initialization",
                    "readiness",
                    "script",
                    "workspace",
                )
            )
        )
        or (
            normalized_cause_type
            in {
                "dependencycheckfailure",
                "dependencycheckerror",
                "dependencyresolutionfault",
                "environmentvalidationerror",
                "environmentvalidationfault",
                "environmentfault",
                "environmentsetuperror",
                "healthprobetimeout",
                "readinesscheckerror",
                "readinessprobeerror",
                "readinessprobefailure",
                "workspacereadinessfault",
                "workspaceiniterror",
            }
            and any(
                marker in text
                for marker in (
                    "dependency",
                    "pre-execution",
                    "preflight",
                    "readiness",
                    "required binary",
                    "required system library",
                    "workspace",
                )
            )
        )
    ):
        code, scope, retryable = HC_PREFLIGHT_FAILED, "harness_runtime", False
    elif (
        normalized_cause_type == "notfounderror"
        or "model_not_found" in text
        or "model not found" in text
        or "does not exist or you do not have access" in text
        or (
            "model" in text
            and _has_http_status(text, 404)
        )
        or normalized_cause_type
        in {
            "deploymentnotfounderror",
            "endpointnotfounderror",
        }
        or (
            "model version" in text
            and "not accessible" in text
        )
        or (
            "deployment" in text
            and "does not exist" in text
        )
        or (
            "endpoint" in text
            and any(marker in text for marker in ("deleted", "never created"))
        )
        or (
            "model" in text
            and any(
                marker in text
                for marker in (
                    "does not exist",
                    "not available",
                    "decommissioned",
                    "not found",
                    "http 404",
                    "no serving endpoint",
                    "removed from api",
                )
            )
        )
        or (
            "model" in text
            and "deprecated" in text
            and "inaccessible" in text
        )
        or (
            "model endpoint" in text
            and (
                _has_http_status(text, 404)
                or normalized_cause_type == "endpointmissingexception"
            )
        )
    ):
        code, scope, retryable = HC_PROVIDER_MODEL_NOT_FOUND, "external_provider", False
    elif (
        "authentication" in text
        or "authorization rejected" in text
        or "insufficient permissions" in text
        or "not authorized" in text
        or "unauthorized" in text
        or "permissiondeniederror" in text
        or "forbidden" in text
        or "access denied" in text
        or "invalid api key" in text
        or "incorrect api key" in text
        or "api key lacks" in text
        or "error code: 401" in text
        or "error code: 403" in text
        or _has_http_status(text, 401, 403)
        or "token expired" in text
        or ("token refresh failed" in text and "revoked" in text)
        or (
            "client certificate" in text
            and any(
                marker in text
                for marker in ("does not match", "invalid", "mismatch", "rejected")
            )
        )
    ):
        code, scope, retryable = HC_PROVIDER_AUTH, "external_provider", False
    elif (
        "ratelimit" in text
        or "rate limit" in text
        or "error code: 429" in text
        or _has_http_status(text, 429)
        or "throttl" in text
        or ("concurrency limit" in text and "reached" in text)
        or normalized_cause_type == "quotaexhaustederror"
        or "quota exhausted" in text
    ):
        code, scope, retryable = HC_PROVIDER_RATE_LIMIT, "external_provider", True
    elif (
        "service unavailable" in text
        or "overloaded" in text
        or "connectionerror" in text
        or "connection refused" in text
        or "connection reset" in text
        or "dns" in text
        or "tls" in text
        or "error code: 500" in text
        or "error code: 502" in text
        or "error code: 503" in text
        or "error code: 504" in text
        or _has_http_status(text, 500, 502, 503, 504)
        or normalized_cause_type
        in {"apitimeouterror", "connecttimeout", "readtimeout"}
    ):
        code, scope, retryable = HC_PROVIDER_UNAVAILABLE, "external_provider", True
    elif (
        "timeouterror" in text
        or "runtime timeout" in text
        or normalized_cause_type
        in {
            "budgetexhaustederror",
            "executiondeadlineexceeded",
            "watchdogtimeoutexception",
        }
        or "task execution exceeded" in text
        or ("task" in text and "exceeded budget" in text)
        or ("pipeline step" in text and "timed out" in text)
        or "overall execution exceeded max duration" in text
        or "harness runtime deadline" in text
        or (
            "no progress heartbeat" in text
            and any(marker in text for marker in ("force-killed", "hung", "unresponsive"))
        )
        or (
            "pipeline budget" in text
            and any(marker in text for marker in ("consumed", "exhausted", "exceeded"))
        )
        or (
            "worker heartbeat lost" in text
            and any(marker in text for marker in ("assumed dead", "killed", "terminated"))
        )
        or "max_queue_time" in text
        or "task expired waiting for execution slot" in text
        or (
            "global deadline" in text
            and "task completion" in text
            and any(marker in text for marker in ("exceeded", "passed"))
        )
        or "runtime budget" in text
        or "runtime watchdog" in text
    ):
        code, scope, retryable = HC_RUNTIME_TIMEOUT, "harness_runtime", True
    elif (
        error_type
        in {
            "FileNotFoundError",
            "IsADirectoryError",
            "NotADirectoryError",
            "PermissionError",
            "ValueError",
        }
        or normalized_cause_type
        in {
            "contractvalidationerror",
            "filenotfoundexception",
            "malformedpayloadexception",
            "typecoercionerror",
        }
        or (
            "input path" in text
            and any(
                marker in text
                for marker in (
                    "does not exist",
                    "does not match expected",
                    "invalid",
                    "missing",
                    "not readable",
                )
            )
        )
        or (
            "input file header" in text
            and any(marker in text for marker in ("not accepted", "unsupported"))
        )
        or ("unsupported file type" in text and "input artifact" in text)
        or "input schema validation failed" in text
        or (
            "input contract" in text
            and any(marker in text for marker in ("does not match", "validation failed"))
        )
        or "input artifact validation failed" in text
        or ("input json" in text and "undeclared fields" in text)
        or (
            "task contract" in text
            and any(marker in text for marker in ("expects", "received", "mismatch"))
        )
        or any(
            marker in text
            for marker in (
                "input schema mismatch",
                "local file schema validation failed",
                "local input file",
                "task contract violation",
                "input yaml",
            )
        )
    ):
        code, scope, retryable = HC_INPUT_CONTRACT_INVALID, "configuration", False
    else:
        code, scope, retryable = HC_RUNTIME_ERROR, "harness_runtime", False

    return BridgeErrorClassification(
        error_code=code,
        failure_scope=scope,
        retryable=retryable,
        cause_type=cause_type,
    )


__all__ = [
    "BridgeErrorClassification",
    "ERROR_CODES",
    "ERROR_SCHEMA_VERSION",
    "classify_bridge_error",
]
