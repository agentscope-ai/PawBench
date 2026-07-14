# main_harness

PawBench 的 Harness attribution 与 Feature ablation 模块。此目录只包含源码和测试，不包含 task data、API key 或运行记录。

## 工作流

```text
PawBench result + trajectory
  -> Reasoning backend 输出 H/M/Ex code 与 evidence
  -> bridge 校验 H-code 并映射 F-code
  -> AgentScope backend 开关对应 Feature
  -> ablation result 回传 PawBench
```

- `M-code`：模型可观察行为，不映射 Feature。
- `Ex-code`：task、scorer 或外部服务问题，不自动映射 Feature。
- `H-code`：Harness 机制问题，可根据 evidence 选择 0-2 个 F-code 做 ablation。

## 主要代码

| 路径 | 作用 |
| --- | --- |
| `scripts/feature_taxonomy.py` | H/M/Ex 定义、15 个 Feature 与 H-to-F mapping。 |
| `scripts/pawbench_output_adapter.py` | 将 PawBench output 转成 attribution input。 |
| `scripts/bridge_attribution_to_harness_core.py` | 校验 H-code，生成 Feature switch 建议。 |
| `candidates/agentscope/` | 默认的极简 AgentScope Harness backend。 |
| `tests/` | taxonomy、adapter、bridge、report 与 security 测试。 |

## PawBench 接入

Reasoning backend 需为每个 task 提供：

```json
{
  "task_id": "example-task",
  "codes": ["H2"],
  "evidence": "direct trajectory evidence"
}
```

Bridge 输出 `recommended_feature_ids` 与 `recommended_switch_keys`，供 AgentScope backend 逐项关闭并复跑。所有归因必须基于 trajectory evidence，不能由 score 直接推断。

## 安全边界

- API key 只从环境变量读取，不写入源码。
- provider key 与 base URL 按同一 namespace 绑定，避免 key 发往错误 endpoint。
- `Data/`、`tmp/`、cache、run records 和 ablation outputs 不进入代码提交。
- API-backed run 可能把 prompt、model output 和 response ID 写入本地记录；共享前必须再次脱敏。

## 测试

在本目录执行：

```bash
python -m pytest -q
python scripts/run_feature_contracts.py --candidate agentscope --pretty
python candidates/agentscope/scripts/v2_ablation_matrix.py
```

API-backed run 由 `DASHSCOPE_API_KEY`、`OPENAI_API_KEY`，或成对的 `LLM_API_KEY` + `LLM_BASE_URL` 配置。不要把 key 写入参数、URL 或文件。

## TODO

当前 H-code 与 Feature mapping 是 hard-coded 的统一 taxonomy。下一步按 PawBench task category 分别设计 Feature，并用真实 trajectory 做 task-specific ablation 验证。
