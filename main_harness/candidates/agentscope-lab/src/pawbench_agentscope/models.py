from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


FeatureLayer = Literal[
    "environment",
    "tool",
    "runtime",
    "observability",
    "acceptance",
    "context",
    "memory",
]


class TaskSpec(BaseModel):
    task_id: str
    instruction: str
    task_dir: Path
    required_artifacts: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_binaries: list[str] = Field(default_factory=list)
    required_env_vars: list[str] = Field(default_factory=list)
    reset_paths: list[str] = Field(default_factory=list)
    isolated_workspace: bool = False
    test_command: str | None = None
    hidden_contract: dict[str, Any] = Field(default_factory=dict)


class FeatureManifest(BaseModel):
    id: str
    name: str
    layer: FeatureLayer
    taxonomy_version: str
    priority: Literal["core", "optional", "reference"] = "core"
    h_codes: list[str] = Field(default_factory=list)
    expected_reduce: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ToolAvailability(BaseModel):
    enabled_tools: list[str]
    missing_required_tools: list[str] = Field(default_factory=list)


class ToolErrorResult(BaseModel):
    ok: bool = False
    tool: str
    error_type: str
    message: str
    recoverable: bool = True
    suggested_next_action: str | None = None


class CompactionStatus(BaseModel):
    enabled: bool
    token_estimate: int
    threshold: int
    should_compact: bool


class CompactionResult(BaseModel):
    mode: Literal["unchanged", "compacted", "truncated"]
    text: str
    before_size: int
    after_size: int
    summary: str = ""
    preserved_fact_hashes: list[str] = Field(default_factory=list)


class VerifierResult(BaseModel):
    ok: bool
    missing_artifacts: list[str] = Field(default_factory=list)
    empty_artifacts: list[str] = Field(default_factory=list)
    failed_tests: list[str] = Field(default_factory=list)


class RunResult(BaseModel):
    task_id: str
    run_id: str
    accepted: bool
    completion_ok: bool = True
    verification_gated: bool = True
    verifier: VerifierResult
    trace_path: Path
    workspace_root: Path
    final_text: str = ""
    event_count: int = 0
    runtime_summary: dict[str, Any] = Field(default_factory=dict)
    taxonomy_version: str = ""
    enabled_features: list[str] = Field(default_factory=list)
