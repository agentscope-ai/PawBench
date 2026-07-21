# Harness-core Harbor Boundary Error Codes

Schema: `harness-core-error-codes/v1`.

These codes describe process-boundary failures. They do not replace PawBench
attribution codes. In particular, provider codes are evidence candidates for
`Ex-3`; configuration/runtime codes require the normal reasoning evidence
policy before any H attribution.

| Code | Scope | Retry | Meaning |
| --- | --- | --- | --- |
| `HC_CONFIG_INVALID_FEATURE` | configuration | no | Unknown Feature ID or invalid Feature switch. |
| `HC_INPUT_CONTRACT_INVALID` | configuration | no | Invalid task ID, artifact path, instruction, timeout, or other bridge input. |
| `HC_PREFLIGHT_FAILED` | harness runtime | no | Workspace/dependency preflight did not pass. |
| `HC_PROVIDER_MODEL_NOT_FOUND` | external provider | no | Model is absent or unavailable to the account. |
| `HC_PROVIDER_AUTH` | external provider | no | Credential or authorization failure. |
| `HC_PROVIDER_RATE_LIMIT` | external provider | yes | Provider rate limit or HTTP 429. |
| `HC_PROVIDER_UNAVAILABLE` | external provider | yes | Provider 5xx, overload, DNS/TLS, or connection outage. |
| `HC_RUNTIME_TIMEOUT` | harness runtime | yes | The bounded AgentScope runtime timed out. |
| `HC_RUNTIME_ERROR` | harness runtime | no by default | Unclassified runtime exception requiring inspection. |

Example error result:

```json
{
  "schema_version": "harness-core-harbor-result/v1",
  "task_id": "example-task",
  "success": false,
  "error_type": "RuntimeError",
  "error": "AgentScope runtime failed: NotFoundError",
  "error_schema_version": "harness-core-error-codes/v1",
  "error_code": "HC_PROVIDER_MODEL_NOT_FOUND",
  "failure_scope": "external_provider",
  "retryable": false,
  "cause_type": "NotFoundError"
}
```

Harbor should base automatic retry on `retryable`, while retaining the full
redacted `result.json` and native trace for later attribution.
