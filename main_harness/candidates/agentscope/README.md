# PawBench AgentScope Harness

默认的极简 Harness backend。AgentScope 提供 agent、model adapter、tool 与 ReAct loop；PawBench Core 提供 Feature switch、workspace isolation、trace normalization 和 verification。

## Feature mapping

| H-code | Feature |
| --- | --- |
| H1 Environment / Workspace | F1.1 Workspace Binding, F1.2 Readiness / Reset, F1.3 Isolation / Permissions |
| H2 Tool Contract | F2.1 Action Contract, F2.2 Tool Availability, F2.3 Result / Error Feedback |
| H3 Runtime / Loop | F3.1 Completion / Termination, F3.2 Budget / Guards, F3.3 Recovery / Resume |
| H4 Observability / Acceptance | F4.1 Diagnostic Trace, F4.2 State / Artifact Deltas, F4.3 Verification |
| H5 Context / Memory | F5.1 Context Assembly, F5.2 Persistent Memory, F5.3 Context Compaction |

`Ex-3` 是外部 provider 或 hosted service 故障，不自动映射 F-code。只有当 Harness 对故障的处理也有直接 evidence 时，才额外归因 H/F。

## 本地测试

在 `main_harness/` 执行：

```bash
python -m pytest candidates/agentscope/tests -q
python candidates/agentscope/scripts/local_demo.py
python candidates/agentscope/scripts/feature_impact_smoke.py --iterations 10
python candidates/agentscope/scripts/v2_ablation_matrix.py
```

API-backed 脚本默认使用 DashScope-compatible endpoint：

```bash
HARNESS_MODEL_NAME=deepseek-v4-pro python candidates/agentscope/scripts/api_smoke.py
HARNESS_MODEL_NAME=deepseek-v4-pro python candidates/agentscope/scripts/real_audit_trail_loop.py
HARNESS_MODEL_NAME=deepseek-v4-pro python candidates/agentscope/scripts/real_feature_ablation.py
```

每次 run 默认创建新的 workspace 与 trace。`--resume` 仅用于明确支持续跑的脚本。运行产物不得提交。
