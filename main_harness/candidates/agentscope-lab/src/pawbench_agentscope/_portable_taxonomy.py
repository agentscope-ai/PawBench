from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


TAXONOMY_VERSION = "harness_core_v2_20260710"
LEGACY_TAXONOMY_VERSION = "legacy_p0_20260709"


@dataclass(frozen=True)
class CodeEntry:
    code: str
    family: str
    short_name: str
    owner: str
    assign_when: str
    do_not_use_when: str
    minimum_evidence: str
    status: str = "active"


@dataclass(frozen=True)
class FeatureEntry:
    feature_id: str
    key: str
    name_en: str
    name_zh: str
    layer: str
    primary_codes: tuple[str, ...]
    related_codes: tuple[str, ...]
    legacy_names: tuple[str, ...]
    switch_contract: str
    trace_evidence: str
    expected_effect: str
    evidence_patterns: tuple[str, ...]


CODE_ORDER: tuple[str, ...] = (
    "Ex-1",
    "Ex-2",
    "Ex-3",
    "H1",
    "H2",
    "H3",
    "H4",
    "H5",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
)


CODE_TABLE: dict[str, CodeEntry] = {
    "Ex-1": CodeEntry(
        code="Ex-1",
        family="Ex",
        short_name="Task design",
        owner="Benchmark / task",
        assign_when="The prompt, fixture, hidden requirement, or task contract is impossible, contradictory, underspecified, or wrong.",
        do_not_use_when="The task is clear but the harness, scorer, model, or an external service failed.",
        minimum_evidence="Quote or path showing the bad task contract, missing fixture, or contradiction.",
    ),
    "Ex-2": CodeEntry(
        code="Ex-2",
        family="Ex",
        short_name="Scoring system",
        owner="Benchmark / scorer",
        assign_when="The judge, parser, metric schema, scorer process, or score output is missing, malformed, crashed, or inconsistent.",
        do_not_use_when="The model answer is wrong and the scorer correctly penalizes it.",
        minimum_evidence="Scorer exception, malformed metrics, missing score, parser failure, or inconsistent score fields.",
    ),
    "Ex-3": CodeEntry(
        code="Ex-3",
        family="Ex",
        short_name="External provider / service",
        owner="External dependency",
        assign_when="A healthy harness is blocked by provider authentication, unavailable model, quota, persistent 429/5xx, DNS/TLS/network outage, or hosted execution service failure.",
        do_not_use_when="The external event is handled incorrectly by the harness; add the evidenced H/F pair as a separate code instead.",
        minimum_evidence="Provider response, transport exception, service status, quota event, or hosted-platform failure record.",
    ),
    "H1": CodeEntry(
        code="H1",
        family="H",
        short_name="Environment / workspace",
        owner="Harness",
        assign_when="Workspace binding, readiness/reset, isolation, or harness-owned permission policy prevents a fair run.",
        do_not_use_when="The environment is valid and the model chooses the wrong path, or a healthy harness is blocked only by an external outage.",
        minimum_evidence="CWD/mount map, preflight/reset record, dependency check, or harness policy event.",
    ),
    "H2": CodeEntry(
        code="H2",
        family="H",
        short_name="Tool contract",
        owner="Harness",
        assign_when="A plausible tool action is unavailable, violates the declared action contract, or returns bad, empty, malformed, false-success, or non-actionable feedback.",
        do_not_use_when="The model supplies bad arguments and the tool behaves according to its contract.",
        minimum_evidence="Tool registry/schema plus the call/result showing availability, validation, result, or feedback failure.",
    ),
    "H3": CodeEntry(
        code="H3",
        family="H",
        short_name="Runtime / loop",
        owner="Harness",
        assign_when="Completion logic, runtime budget, or recovery/resume behavior blocks completion.",
        do_not_use_when="The model voluntarily stops or reasons badly within adequate budget and a healthy loop.",
        minimum_evidence="Stop reason, counters/limits, runtime exception, retry record, or checkpoint/resume record.",
    ),
    "H4": CodeEntry(
        code="H4",
        family="H",
        short_name="Observability / acceptance",
        owner="Harness",
        assign_when="The harness trace, state/artifact evidence, or verification gate hides, loses, or misreports the real outcome.",
        do_not_use_when="The primary failure is already visible and belongs to H1, H2, or H3.",
        minimum_evidence="Missing/incorrect trace, before/after state, artifact manifest, verifier result, or acceptance decision.",
    ),
    "H5": CodeEntry(
        code="H5",
        family="H",
        short_name="Context / memory",
        owner="Harness",
        assign_when="Context assembly, persistent memory, or compaction loses, corrupts, retrieves, or injects required state incorrectly.",
        do_not_use_when="The needed fact remains visible and the model ignores it; use M5.",
        minimum_evidence="Context-source manifest, memory query/write, or compaction/reconstruction record.",
    ),
    "M1": CodeEntry(
        code="M1",
        family="M",
        short_name="Hallucination",
        owner="Model",
        assign_when="The model invents unsupported facts, files, observations, citations, tool outputs, numbers, or conclusions.",
        do_not_use_when="The model makes a wrong calculation from real evidence.",
        minimum_evidence="Claim with no supporting tool output, file, result, citation, or number.",
    ),
    "M2": CodeEntry(
        code="M2",
        family="M",
        short_name="Instruction following",
        owner="Model",
        assign_when="The model violates explicit output, path, schema, format, language, constraint, or deliverable instructions.",
        do_not_use_when="The instruction is hidden, contradictory, or ambiguous.",
        minimum_evidence="Explicit instruction plus output or deliverable that violates it.",
    ),
    "M3": CodeEntry(
        code="M3",
        family="M",
        short_name="Tool use",
        owner="Model",
        assign_when="The model chooses the wrong tool, bad arguments/path, skips a needed tool, ignores a recoverable error, or fails to retry.",
        do_not_use_when="The tool itself is unavailable or violates its contract for a plausible call.",
        minimum_evidence="Available tool and transcript showing missing, wrong, or unrecovered tool use.",
    ),
    "M4": CodeEntry(
        code="M4",
        family="M",
        short_name="Reasoning",
        owner="Model",
        assign_when="The model has the needed evidence but transforms it incorrectly.",
        do_not_use_when="The evidence was never gathered.",
        minimum_evidence="Visible evidence followed by an incorrect transformation or conclusion.",
    ),
    "M5": CodeEntry(
        code="M5",
        family="M",
        short_name="Model memory",
        owner="Model",
        assign_when="Needed information remains visible, but the model later forgets, contradicts, or ignores it.",
        do_not_use_when="The harness removed or failed to inject the state.",
        minimum_evidence="Earlier visible fact and later contradiction or omission in the model response.",
    ),
}


# Stable human-readable labels. Raw codes remain unchanged in machine fields so
# H-to-F mapping and historical parsers stay compatible.
CODE_DISPLAY_LABELS: dict[str, str] = {
    "Ex-1": "Ex-1-TaskDesign",
    "Ex-2": "Ex-2-ScoringSystem",
    "Ex-3": "Ex-3-ExternalService",
    "H1": "H1-EnvironmentWorkspace",
    "H2": "H2-ToolContract",
    "H3": "H3-RuntimeLoop",
    "H4": "H4-ObservabilityAcceptance",
    "H5": "H5-ContextMemory",
    "M1": "M1-Hallucination",
    "M2": "M2-InstructionFollowing",
    "M3": "M3-ToolUse",
    "M4": "M4-Reasoning",
    "M5": "M5-ModelMemory",
}


def display_code(code: str) -> str:
    return CODE_DISPLAY_LABELS.get(code, code)


CODE_TABLE_ZH: dict[str, dict[str, str]] = {
    code: {
        "owner": entry.owner,
        "short_name": entry.short_name,
        "assign_when": entry.assign_when,
        "do_not_use_when": entry.do_not_use_when,
        "minimum_evidence": entry.minimum_evidence,
        "status": entry.status,
    }
    for code, entry in CODE_TABLE.items()
}
CODE_TABLE_ZH.update(
    {
        "Ex-1": {**CODE_TABLE_ZH["Ex-1"], "short_name": "任务设计问题"},
        "Ex-2": {**CODE_TABLE_ZH["Ex-2"], "short_name": "评分系统问题"},
        "Ex-3": {**CODE_TABLE_ZH["Ex-3"], "short_name": "外部服务问题"},
        "H1": {**CODE_TABLE_ZH["H1"], "short_name": "环境 / 工作区"},
        "H2": {**CODE_TABLE_ZH["H2"], "short_name": "工具契约"},
        "H3": {**CODE_TABLE_ZH["H3"], "short_name": "运行循环"},
        "H4": {**CODE_TABLE_ZH["H4"], "short_name": "可观测性 / 验收"},
        "H5": {**CODE_TABLE_ZH["H5"], "short_name": "上下文 / 记忆"},
        "M1": {**CODE_TABLE_ZH["M1"], "short_name": "幻觉"},
        "M2": {**CODE_TABLE_ZH["M2"], "short_name": "指令遵循"},
        "M3": {**CODE_TABLE_ZH["M3"], "short_name": "工具使用"},
        "M4": {**CODE_TABLE_ZH["M4"], "short_name": "推理"},
        "M5": {**CODE_TABLE_ZH["M5"], "short_name": "模型记忆"},
    }
)


FEATURE_IDS: tuple[str, ...] = (
    "F1.1",
    "F1.2",
    "F1.3",
    "F2.1",
    "F2.2",
    "F2.3",
    "F3.1",
    "F3.2",
    "F3.3",
    "F4.1",
    "F4.2",
    "F4.3",
    "F5.1",
    "F5.2",
    "F5.3",
)
DEFAULT_FEATURE_IDS = FEATURE_IDS
LEGACY_P0_FEATURE_IDS: tuple[str, ...] = (
    "F1.1",
    "F1.2",
    "F1.5",
    "F2.1",
    "F2.3",
    "F2.4",
    "F3.1",
    "F3.3",
    "F4.1",
    "F5.1",
)


H_TO_FEATURES: dict[str, tuple[str, ...]] = {
    "H1": ("F1.1", "F1.2", "F1.3"),
    "H2": ("F2.1", "F2.2", "F2.3"),
    "H3": ("F3.1", "F3.2", "F3.3"),
    "H4": ("F4.1", "F4.2", "F4.3"),
    "H5": ("F5.1", "F5.2", "F5.3"),
}


def feature(
    feature_id: str,
    key: str,
    name_en: str,
    name_zh: str,
    layer: str,
    h_code: str,
    switch_contract: str,
    trace_evidence: str,
    expected_effect: str,
    evidence_patterns: tuple[str, ...],
    *,
    legacy_names: tuple[str, ...] = (),
    related_codes: tuple[str, ...] = (),
) -> FeatureEntry:
    return FeatureEntry(
        feature_id=feature_id,
        key=key,
        name_en=name_en,
        name_zh=name_zh,
        layer=layer,
        primary_codes=(h_code,),
        related_codes=related_codes,
        legacy_names=legacy_names,
        switch_contract=switch_contract,
        trace_evidence=trace_evidence,
        expected_effect=expected_effect,
        evidence_patterns=evidence_patterns,
    )


FEATURES: dict[str, FeatureEntry] = {
    "F1.1": feature(
        "F1.1", "workspace_binding", "Workspace Binding", "工作区绑定", "environment", "H1",
        "Bind CWD, mounts, inputs, and artifact paths; OFF removes explicit binding but retains a safe temporary root.",
        "workspace_binding, cwd, mount_manifest, path_map",
        "OFF should expose wrong-root, mount, or artifact-path failures.",
        (r"\bcwd\b", r"working directory", r"workspace", r"mount", r"path (?:map|mapping)", r"wrong[- ]root", r"artifact path"),
        legacy_names=("Workspace Contract", "CWD / Workspace Injection"),
    ),
    "F1.2": feature(
        "F1.2", "readiness_reset", "Readiness / Reset", "就绪检查 / 重置", "environment", "H1",
        "Validate image, dependencies, services, fixtures, and clean state; OFF skips preflight/reset inside an isolated copy.",
        "preflight_result, dependency_check, service_check, reset_result, state_hash",
        "OFF should retain stale state or surface missing dependencies that preflight would catch.",
        (r"readiness", r"preflight", r"reset", r"fixture", r"dependenc", r"binary", r"service", r"image", r"clean state"),
    ),
    "F1.3": feature(
        "F1.3", "isolation_permissions", "Isolation / Permissions", "隔离 / 权限", "environment", "H1",
        "Enforce file, process, network, resource, and credential policy; OFF uses a minimal safety policy and never runs unsandboxed.",
        "policy_version, permission_decision, allow_event, deny_event",
        "OFF should relax the enhanced policy while preserving the host safety floor.",
        (r"isolat", r"permission", r"sandbox", r"policy", r"credential", r"network", r"path escape", r"guard"),
    ),
    "F2.1": feature(
        "F2.1", "action_contract", "Action Contract", "动作契约", "tool", "H2",
        "Expose tool names, descriptions, schemas, and argument validation; OFF retains tools but removes PawBench validation.",
        "action_schema, registration_result, argument_validation",
        "OFF should preserve availability while weakening action-contract evidence.",
        (r"schema", r"registration", r"argument", r"action contract", r"invalid .*arg", r"tool name"),
        legacy_names=("Tool Interface Contract", "Tool Schema & Registration"), related_codes=("M3",),
    ),
    "F2.2": feature(
        "F2.2", "tool_availability", "Tool Availability", "工具可用性", "tool", "H2",
        "Activate the task-relevant tool set; OFF hides one selected tool and leaves other tools unchanged.",
        "tool_registry, activation_decision, hidden_tool",
        "OFF should remove only the selected tool from the exposed registry.",
        (r"unavailable", r"not available", r"missing tool", r"tool .*hidden", r"tool registry", r"activation"),
        legacy_names=("Tool Access Contract", "Default Tool Availability"), related_codes=("M3",),
    ),
    "F2.3": feature(
        "F2.3", "result_error_feedback", "Result / Error Feedback", "结果 / 错误反馈", "tool", "H2",
        "Return structured output, exit status, effects, and actionable errors; OFF returns raw output without changing execution.",
        "tool_result, exit_status, normalized_tool_error, effect",
        "OFF should preserve tool execution but remove normalized/actionable feedback.",
        (r"tool result", r"empty result", r"malformed", r"false[- ]success", r"error feedback", r"actionable", r"exit status"),
        legacy_names=("Tool Feedback Contract", "Tool Error Handling & Feedback"), related_codes=("M3",),
    ),
    "F3.1": feature(
        "F3.1", "completion_termination", "Completion / Termination", "完成 / 终止", "runtime", "H3",
        "Enforce finish, abort, and stop conditions; OFF uses the framework baseline stop rule.",
        "completion_request, stop_reason, termination_decision",
        "OFF may accept premature framework termination but must not disable verification.",
        (r"premature stop", r"termination", r"completion", r"finish", r"abort", r"stop condition", r"missing final"),
        legacy_names=("Completion Contract", "Completion Detection"), related_codes=("M2",),
    ),
    "F3.2": feature(
        "F3.2", "budget_guards", "Budget / Guards", "预算 / 守卫", "runtime", "H3",
        "Enforce turn, time, token, cost, and repetition limits; OFF enlarges limits but retains an absolute safety cap.",
        "budget_policy, counters, limits, cutoff_reason",
        "OFF should enlarge controlled limits without removing the absolute cap.",
        (r"timeout", r"max(?:imum)? iteration", r"budget", r"token", r"cost", r"repetition", r"cutoff", r"time limit"),
    ),
    "F3.3": feature(
        "F3.3", "recovery_resume", "Recovery / Resume", "恢复 / 续跑", "runtime", "H3",
        "Classify failures and apply bounded retry, restore, or resume; OFF stops after the first recoverable failure.",
        "retry_reason, attempt, checkpoint, resume_result",
        "OFF should remove bounded repair/resume after a recoverable failure.",
        (r"retry", r"recover", r"resume", r"checkpoint", r"repair attempt"),
        legacy_names=("Recovery Contract", "Error Recovery & Retry"), related_codes=("M3",),
    ),
    "F4.1": feature(
        "F4.1", "diagnostic_trace", "Diagnostic Trace", "诊断轨迹", "observability", "H4",
        "Record causally linked model, tool, environment, and controller events; OFF omits diagnostic classes but retains the outer audit log.",
        "event_id, parent_id, timestamp, event_status, diagnostic_trace_policy",
        "OFF should omit diagnostic framework events while preserving start/end/error audit events.",
        (r"trace", r"transcript", r"event", r"swallow", r"missing diagnostic", r"causal", r"\blog\b"),
        legacy_names=("Trace",),
    ),
    "F4.2": feature(
        "F4.2", "state_artifact_deltas", "State / Artifact Deltas", "状态 / 产物差异", "observability", "H4",
        "Record exceptions, state changes, files, artifacts, cost, and latency; OFF omits deltas but retains the base trace.",
        "before_state, after_state, artifact_manifest, state_delta",
        "OFF should remove before/after delta evidence without changing execution.",
        (r"state delta", r"artifact delta", r"file change", r"before.*after", r"artifact manifest", r"side effect"),
    ),
    "F4.3": feature(
        "F4.3", "verification", "Verification", "验证", "acceptance", "H4",
        "Independently check outcome/process evidence and acceptance; OFF reports verification but does not gate completion.",
        "verifier_version, verifier_evidence, verifier_result, gate_decision",
        "OFF should preserve the verifier report while allowing a completed run to remain accepted.",
        (r"verif", r"validat", r"acceptance", r"false[- ]success", r"gate", r"scorer"),
        legacy_names=("Verification Contract", "Artifact Verification"), related_codes=("Ex-2", "M2"),
    ),
    "F5.1": feature(
        "F5.1", "context_assembly", "Context Assembly", "上下文组装", "context", "H5",
        "Build active call context from instructions and selected evidence; OFF supplies only the minimal task contract.",
        "context_sources, context_order, source_hashes, token_count",
        "OFF should remove discovered context while retaining the literal task instruction.",
        (r"context assembl", r"inject", r"instruction", r"context source", r"prompt", r"skill", r"context order"),
        legacy_names=("Context Contract", "Prompt Integrity Contract", "Skill Discovery & Injection"), related_codes=("Ex-1", "M5"),
    ),
    "F5.2": feature(
        "F5.2", "persistent_memory", "Persistent Memory", "持久记忆", "memory", "H5",
        "Write, index, update, retrieve, and read external memory; OFF starts with a fresh empty memory store.",
        "memory_write, memory_query, memory_version, memory_records",
        "OFF should prevent cross-run retrieval while leaving current-run context intact.",
        (r"persistent memory", r"memory store", r"retriev", r"stale memory", r"index", r"cross[- ]run"),
        related_codes=("M5",),
    ),
    "F5.3": feature(
        "F5.3", "compaction", "Compaction", "上下文压缩", "context", "H5",
        "Summarize history and reconstruct continuation context; OFF uses controlled truncation.",
        "compaction_trigger, before_size, after_size, summary, preserved_facts",
        "OFF should use the documented truncation baseline instead of an extractive reconstruction.",
        (r"compact", r"summary", r"summariz", r"truncat", r"reconstruct", r"context loss"),
        legacy_names=("State Budget Contract", "Context Compaction"), related_codes=("M5",),
    ),
}


FEATURE_EFFECT_ZH: dict[str, str] = {
    feature_id: entry.expected_effect for feature_id, entry in FEATURES.items()
}
FEATURE_NAMES: dict[str, str] = {
    feature_id: entry.name_en for feature_id, entry in FEATURES.items()
}
H_CODE_MEANING: dict[str, str] = {
    code: CODE_TABLE[code].short_name for code in H_TO_FEATURES
}


LEGACY_FEATURE_MIGRATION: dict[str, dict[str, Any]] = {
    "F1.1": {"targets": ("F1.1",), "lossy": False, "note": "Workspace contract became workspace binding."},
    "F1.2": {"targets": ("F5.1",), "lossy": True, "note": "Legacy context discovery is one part of context assembly."},
    "F1.5": {"targets": ("F5.1",), "lossy": True, "note": "Prompt integrity moved under context assembly provenance."},
    "F2.1": {"targets": ("F2.1",), "lossy": False, "note": "Tool interface contract became action contract."},
    "F2.3": {"targets": ("F2.2",), "lossy": False, "note": "Legacy tool access is the new availability feature."},
    "F2.4": {"targets": ("F2.3",), "lossy": False, "note": "Legacy tool feedback is the new result/error feedback feature."},
    "F3.1": {"targets": ("F3.1", "F4.3"), "lossy": True, "note": "Legacy completion conflated termination with acceptance gating."},
    "F3.3": {"targets": ("F3.3",), "lossy": False, "note": "Recovery contract retained its meaning."},
    "F4.1": {"targets": ("F3.2", "F5.3"), "lossy": True, "note": "Legacy state budget conflated runtime budgets and compaction."},
    "F5.1": {"targets": ("F4.3",), "lossy": False, "note": "Legacy verifier became the verification feature."},
}
LEGACY_CODE_MIGRATION: dict[str, dict[str, Any]] = {
    "H6": {
        "targets": ("Ex-3",),
        "lossy": True,
        "note": "Use Ex-3 only for an external dependency failure; add H/F separately when harness handling is also wrong.",
    }
}


def feature_label(feature_id: str, *, zh: bool = False) -> str:
    entry = FEATURES[feature_id]
    return f"{feature_id} {entry.name_zh if zh else entry.name_en}"


def h_feature_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for h_code, feature_ids in H_TO_FEATURES.items():
        code = CODE_TABLE[h_code]
        rows.append(
            {
                "h_code": h_code,
                "h_name": code.short_name,
                "status": code.status,
                "features": [
                    {
                        "feature_id": feature_id,
                        "key": FEATURES[feature_id].key,
                        "name_en": FEATURES[feature_id].name_en,
                        "name_zh": FEATURES[feature_id].name_zh,
                        "layer": FEATURES[feature_id].layer,
                        "trace_evidence": FEATURES[feature_id].trace_evidence,
                    }
                    for feature_id in feature_ids
                ],
            }
        )
    return rows


def evidence_text(evidence: Any) -> str:
    if evidence is None:
        return ""
    if isinstance(evidence, str):
        return evidence
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)


def select_features_for_evidence(
    h_code: str,
    evidence: Any,
    *,
    max_features: int = 2,
) -> list[dict[str, Any]]:
    """Select at most two candidate features, each backed by a distinct text match."""
    if h_code not in H_TO_FEATURES:
        return []
    text = evidence_text(evidence).lower()
    if not text.strip():
        return []
    candidates: list[dict[str, Any]] = []
    for order, feature_id in enumerate(H_TO_FEATURES[h_code]):
        matches = [
            match.group(0)
            for pattern in FEATURES[feature_id].evidence_patterns
            if (match := re.search(pattern, text, flags=re.I))
        ]
        if matches:
            candidates.append(
                {
                    "feature_id": feature_id,
                    "score": len(matches),
                    "evidence_matches": matches,
                    "candidate_order": order,
                }
            )
    candidates.sort(key=lambda item: (-item["score"], item["candidate_order"]))
    selected: list[dict[str, Any]] = []
    used_matches: set[str] = set()
    for item in candidates:
        distinct = [match for match in item["evidence_matches"] if match not in used_matches]
        if not distinct:
            continue
        item = {**item, "evidence_matches": distinct}
        selected.append(item)
        used_matches.update(distinct)
        if len(selected) >= max(0, min(max_features, 2)):
            break
    return selected


def migrate_legacy_feature_ids(feature_ids: Iterable[str]) -> dict[str, Any]:
    targets: set[str] = set()
    warnings: list[str] = []
    unknown: list[str] = []
    for feature_id in feature_ids:
        migration = LEGACY_FEATURE_MIGRATION.get(feature_id)
        if migration is None:
            unknown.append(feature_id)
            continue
        targets.update(migration["targets"])
        if migration["lossy"]:
            warnings.append(f"{feature_id}: {migration['note']}")
    return {
        "from_version": LEGACY_TAXONOMY_VERSION,
        "to_version": TAXONOMY_VERSION,
        "feature_ids": sorted(targets),
        "lossy_warnings": warnings,
        "unknown_feature_ids": sorted(unknown),
    }


def validate_taxonomy() -> list[str]:
    errors: list[str] = []
    if set(FEATURE_IDS) != set(FEATURES):
        errors.append("FEATURE_IDS and FEATURES keys differ")
    if set(H_TO_FEATURES) != {"H1", "H2", "H3", "H4", "H5"}:
        errors.append("H_TO_FEATURES must contain exactly H1-H5")
    if "H6" in CODE_TABLE:
        errors.append("H6 must not be active; use Ex-3")
    if "Ex-3" not in CODE_TABLE or "Ex-3" in H_TO_FEATURES:
        errors.append("Ex-3 must be an external code with no automatic feature mapping")
    for h_code, feature_ids in H_TO_FEATURES.items():
        if not 2 <= len(feature_ids) <= 3:
            errors.append(f"{h_code} must map to 2-3 candidate features")
        for feature_id in feature_ids:
            entry = FEATURES.get(feature_id)
            if entry is None:
                errors.append(f"{h_code} maps to unknown feature {feature_id}")
            elif entry.primary_codes != (h_code,):
                errors.append(f"{h_code}+{feature_id} does not have one matching primary code")
    for feature_id, entry in FEATURES.items():
        if not entry.evidence_patterns:
            errors.append(f"{feature_id} has no evidence patterns")
        for code in entry.primary_codes + entry.related_codes:
            if code not in CODE_TABLE:
                errors.append(f"{feature_id} references unknown code {code}")
    return errors
