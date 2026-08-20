# PawBench AgentScope Harness

默认的 Harness backend。AgentScope 提供 agent、model adapter、tool 与 ReAct
loop；PawBench Core 提供 Feature switch、workspace isolation、trace
normalization 与 verification。`agentscope-develop-feature` 是受治理的开发
桥接：它不会自行打开 Feature，也不会改写 attribution 结论。

## Feature mapping

| H-code | Feature |
| --- | --- |
| H1 Environment and Workspace | F1.1 Workspace Binding, F1.2 Readiness and Reset, F1.3 Isolation and Permissions |
| H2 Tool Contract | F2.1 Action Contract, F2.2 Tool Availability, F2.3 Result and Error Feedback |
| H3 Runtime and Loop | F3.1 Completion and Termination, F3.2 Budget and Guards, F3.3 Recovery and Resume |
| H4 Observability and Acceptance | F4.1 Diagnostic Trace, F4.2 State and Artifact Deltas, F4.3 Verification |
| H5 Context and Memory | F5.1 Context Assembly, F5.2 Persistent Memory, F5.3 Compaction |

`Ex-3` 是外部 provider 或 hosted service 故障，不自动映射 F-code。只有
Harness 对故障的处理也有直接 evidence 时，才额外归因 H/F。

## 受治理的 Feature 开发

默认 coding agent 是 **Qwen3.8-Max through Claude Code**。它只接收一个已
接受的 optimization-arm H-to-F request，并使用仓库内的
`implement-attributed-feature` skill。执行顺序固定为：

```text
attribution bridge
  -> accepted H-to-F request
  -> prepare skill receipt
  -> Qwen3.8-Max through Claude Code
  -> independent admission receipt validation
  -> caller activates exactly one admitted Feature
  -> paired benchmark and holdout evaluation
```

`run --execute` 才允许 Claude Code 改写本地 workspace。子进程只获得
DashScope gateway 所需的凭据；GitHub、Alibaba 与其他主机 token 不会继承，
且不会写入 receipt。需要配置：

```bash
export DASHSCOPE_API_KEY='...'
export DASHSCOPE_ANTHROPIC_BASE_URL='https://.../apps/anthropic'
# 或使用 DASHSCOPE_BASE_URL=https://.../compatible-mode/v1 自动推导
```

从 `main_harness/` 运行。`prepare`、`run` 与 `verify` 总是通过
`--workspace-root` 读取 PawBench checkout 内的 canonical manifest 和 local
skill；evidence 必须来自 optimization arm，任何含 `holdout` 的路径都会被拒绝。
已安装的 command 可以在 checkout 外显示 `--help`，但不能脱离 checkout 执行开发。

```bash
python -m pip install -e candidates/agentscope

agentscope-develop-feature prepare \
  --workspace-root .. \
  --output-dir main_harness/candidates/agentscope/tmp/feature_development/round_01 \
  --h-code H2 --feature-id F2.2 \
  --selection-reason 'accepted three-peer attribution evidence' \
  --evidence main_harness/candidates/agentscope/tmp/round_01/ATTRIBUTION_SUMMARY.json

agentscope-develop-feature run --workspace-root .. --execute \
  --request main_harness/candidates/agentscope/tmp/feature_development/round_01/FEATURE_DEVELOPMENT_REQUEST.json

agentscope-develop-feature verify --workspace-root .. \
  --request main_harness/candidates/agentscope/tmp/feature_development/round_01/FEATURE_DEVELOPMENT_REQUEST.json
```

`FEATURE_CHANGE_STANDARD.md`、`feature_manifest.json` 与
`feature_value_contract.json` 是实现和验收的共同约束。receipt 必须使
boundary、OFF equivalence、spatial、temporal、safety、causal pair、holdout、
compatibility 和 benchmark matrix 九个 gate 全部通过。

## 本地测试

在 `main_harness/` 执行：

```bash
python -m pytest candidates/agentscope/tests -q
python candidates/agentscope/scripts/local_demo.py
python candidates/agentscope/scripts/feature_impact_smoke.py --iterations 10
python candidates/agentscope/scripts/v2_ablation_matrix.py
```

API-backed ablation 脚本保持原有 DashScope-compatible endpoint 配置。每次
run 创建新的 workspace 与 trace；`--resume` 仅用于明确支持续跑的脚本。
运行产物、request、receipt 和 token 都不得提交。
