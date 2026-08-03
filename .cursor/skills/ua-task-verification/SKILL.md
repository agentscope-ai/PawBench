---
name: ua-task-verification
description: >-
  验证 PawBench user-agent (ua-cw/ua-mt) 类 task 的产出是否符合预期。
  当用户说「检查 UA task 结果」「判断 user-agent 产出」「验证 ua-cw/ua-mt task」
  或需要分析 trajectory.json / user_sim_state.json 是否符合 hint 设计时使用。
---

# UA Task 产出验证

对 PawBench `ua-cw-*` / `ua-mt-*` task 的运行结果做四维度合规检查。

## 输入

- `task_id`（如 `ua-cw-academic-0023`）
- `agent`（如 `claude-code`、`codex`、`hermes` 等）
- 结果目录（Harbor-v2）：`results/{experiment}/.../trials/{task_id}__*_r1__*/`
- 原始 task 目录：`data/data_v2.2/{task_id}/`（或对应 dataset 版本）

## 核心数据源

| 文件 | 位置 | 用途 |
|------|------|------|
| `trajectory.json` | `trials/.../agent/trajectory.json` | **主数据源**：ATIF v1.7 完整时间线，含 agent 工具调用 + user 消息 |
| `user_sim_state.json` | `trials/.../agent/user_sim_state.json` | 对话状态摘要：started/done/termination_reason、精简 transcript、workspace_events |
| `ua_task_score.json` | `trials/.../verifier/ua_task_score.json` | 自动评分细节：result/process/fact_tokens |
| `turn_NN_hint.md` | `data/.../user/patches/` | Patch 定义（YAML）+ 对话指令（正文） |
| `messages.jsonl` | `data/.../` | Authored user turns（ua-mt 用） |
| workspace 快照 | trial 内 agent 产出目录或 verifier 采集 | 最终 deliverable 内容 |

### trajectory.json 结构（ATIF v1.7）

`trajectory.json` 现已包含 **agent 与 user 的完整交互**，是 UA 分析的首选数据源：

```json
{
  "schema_version": "ATIF-v1.7",
  "steps": [
    {"step_id": 1, "source": "user", "message": "<pawbench-multi-turn-protocol>..."},
    {"step_id": 2, "source": "agent", "tool_calls": [{"function_name": "user-sim__start_conversation"}]},
    {"step_id": 3, "source": "user", "message": "你好。我整理了实验室..."},
    {"step_id": 4, "source": "agent", "tool_calls": [{"function_name": "exec", ...}]},
    ...
  ]
}
```

关键字段：

| 字段 | 说明 |
|------|------|
| `source: "user"` | 用户消息（跳过 `<pawbench-multi-turn-protocol>` 开头的系统 prompt） |
| `source: "agent"` | Agent 步骤：含 `message`、`tool_calls`、`observation` |
| `tool_calls[].function_name` | 工具名；UA 相关：`user-sim__start_conversation`、`user-sim__send_message_to_user`（Claude Code 为 `mcp__user-sim__*`） |
| `observation.results[].content` | 工具返回；user-sim 返回含 `conversation_over`、`user_message` |

### 数据源选择策略

```
对话完整性 / 协议合规  → trajectory.json（user steps + user-sim tool calls）
对话状态摘要           → user_sim_state.json（started/done/termination_reason）
Patch 时序验证         → trajectory.json（file Read/Write 相对 user turn 的位置）
自动评分解读           → verifier/ua_task_score.json + aggregate breakdown
Patch 事件匹配         → user_sim_state.json → workspace_events[]（若为空则改查 trajectory/workspace）
```

## 四维度检查流程

### 维度 2.0：多轮协议合规（新增，决定最终 pass/fail）

Harbor backend 会检查 `multi_turn_protocol_compliance`；违反则 **score 归零**（即使 quality 很高）。

```
✅ 第一步工具调用是 start_conversation（不能先 Read/Write/Bash）
✅ 每轮工作后调用 send_message_to_user(message) 回复用户
✅ 最后一次 send_message_to_user 返回 conversation_over: true
❌ 常见违规：只 start_conversation 后直接写文件/跑命令，从未 send_message_to_user
```

从 trajectory 提取协议合规：

```python
# 伪代码
steps = trajectory["steps"]
agent_steps = [s for s in steps if s["source"] == "agent"]
first_tools = agent_steps[0].get("tool_calls", [])
assert any("start_conversation" in t["function_name"] for t in first_tools)

user_sim_calls = [
    tc["function_name"]
    for s in agent_steps
    for tc in s.get("tool_calls", [])
    if "user-sim" in tc["function_name"]
]
assert any("send_message_to_user" in c for c in user_sim_calls)
```

aggregate result 中查看：

```json
"breakdown": {
  "quality": 0.8457,
  "reward": 1.0,
  "multi_turn_protocol_compliance": 0.0
},
"anomaly": {
  "multi_turn_protocol_violation": "send_message_to_user was never called"
}
```

### 维度 2.1：多轮对话是否完成

**优先读 trajectory.json** 的 user steps；用 user_sim_state.json 交叉验证：

```
✅ user_sim_state.started == true
✅ user_sim_state.done == true
✅ termination_reason == "user_done"
✅ trajectory 中 user 消息数 >= 4（ua-cw-academic-0023 为 5 轮）
✅ 最后一条 user 消息含 [DONE]
✅ send_message_to_user 调用次数 >= user 轮次数
```

从 trajectory 统计 user 轮次：

```python
user_msgs = [
    s["message"] for s in steps
    if s.get("source") == "user"
    and not s["message"].startswith("<pawbench")
]
```

常见失败模式：
- `started=true, done=false` → agent 未完成多轮（claude-code/codex 典型）
- `termination_reason="max_turns"` → 对话超时
- `send_message_to_user` 从未调用 → 协议违规，score=0

### 维度 2.2：Hint/Patch 合规

分两部分：**文件 Patch** 和 **对话话术**。

#### 2.2a — Patch 文件操作验证

**第一层：workspace_events 匹配**（若 events 非空）

```
1. 解析 turn_NN_hint.md YAML → files: [{path, action, content/old/new}]
2. 读 user_sim_state.json → workspace_events[]
3. 逐条比对 (turn, action, path)，确认 status == "applied"
```

**注意**：当前 harness 可能不写入 `workspace_events`（为空数组）。此时改用 **trajectory 时序验证**：

```
1. 从 trajectory 定位每轮 user 消息（turn N 的 user step）
2. 在该 user step 之后的 agent steps 中，查找对该 patch 文件的 Read/Write
3. turn 2 patch（error_model_config.json）应在 turn 2 user 消息出现后才被 agent 读取
4. 若 agent 在 patch 文件出现前就尝试 Read → 文件尚不存在（合理行为）
```

**第二层：结果文件内容校验**（若有 workspace 快照）

```
- create/overwrite: diff(hint.content, workspace_file)
- edit: assert new_text in file AND old_text not in file
- delete: assert file does not exist
```

#### 2.2b — 对话话术合规

对 ua-cw task，从 trajectory 的 user steps 提取各轮 user 消息，对照 hint 正文：

```
1. 读 turn_NN_hint.md Markdown 正文（YAML 之后）
2. 提取 hint 中关键路径（反引号内）和核心指令
3. 对照 trajectory 第 N 个 user step 的 message
4. 检查：关键文件路径是否被提及、核心指令是否传达
```

对 ua-mt task：读 `messages.jsonl`，对照 trajectory user steps。

### 维度 2.3：对话连贯性

基于 trajectory 中 user/agent 消息交替序列：

```
1. 逐轮检查：user 第 N+1 轮是否回应了 agent 第 N 轮 send_message_to_user 的内容
2. 数字一致性：user 引用的统计是否与 agent 产出匹配
3. 文件引用一致：user 提到的文件名是否确实存在于 workspace
4. 无"幻觉回应"：user 不应回应 agent 没说过的内容
```

### 维度 2.4：自动评分解读

读 `verifier/ua_task_score.json`：

| 字段 | 含义 |
|------|------|
| `result` | deliverable 中 fact_tokens 命中率（如 `0.3`, `0.5`, `0.8`, `0.6`） |
| `process` | 轨迹中 required_source_substrings 命中 + 多轮信号 |
| `score` | `0.6 * result + 0.4 * process`（format gate 不过则 0） |
| `pass_threshold` | 通常 0.7 |

最终 pass 还需满足 `multi_turn_protocol_compliance == 1.0`。

## 输出报告模板

```markdown
## UA Task 验证报告：{agent}/{task_id}

### 基本信息
- started: {true/false}
- done: {true/false}
- termination_reason: {reason}
- trajectory user/agent steps: {N} / {M}
- user-sim calls: start={bool}, send={count}
- protocol_compliance: {0/1} ({violation})

### 2.0 协议合规
- [✅/❌] start_conversation 作为首步
- [✅/❌] send_message_to_user 被调用
- [✅/❌] conversation_over: true

### 2.1 多轮完成度
- [✅/❌] 对话正常结束（user_done + [DONE]）
- [✅/❌] 轮次数符合预期（{expected} 轮）

### 2.2 Patch 合规
| Turn | Action | Path | 验证方式 | 结果 |
|------|--------|------|---------|------|
| ... |

### 2.3 连贯性
- [✅/❌] 各轮逻辑连贯
- 问题点：{如有}

### 2.4 自动评分
- quality: {score} (result={result}, process={process})
- fact_tokens 命中: {hits}/{total}
- 最终 pass: {bool}

### 结论
{PASS / FAIL + 根因}
```

## 快速命令

```bash
EXPERIMENT="results/multi3-0731"
TASK="ua-cw-academic-0023"
AGENT="openclaw"

# 定位 trial
TRIAL=$(find $EXPERIMENT/$AGENT -path "*${TASK}*" -name trajectory.json | head -1 | xargs dirname)

# 对话状态
cat $TRIAL/user_sim_state.json | python3 -m json.tool | head -30

# 从 trajectory 提取 user 轮次
python3 -c "
import json, sys
t=json.load(open('$TRIAL/trajectory.json'))
users=[s for s in t['steps'] if s.get('source')=='user' and not s.get('message','').startswith('<pawbench')]
print(f'user turns: {len(users)}')
for i,u in enumerate(users): print(f'--- turn {i+1} ---\n{u[\"message\"][:200]}...')
"

# user-sim 工具调用
python3 -c "
import json
t=json.load(open('$TRIAL/trajectory.json'))
for s in t['steps']:
    for tc in s.get('tool_calls',[]):
        if 'user-sim' in tc.get('function_name',''):
            print(tc['function_name'])
"

# 自动评分
cat $(find $EXPERIMENT/$AGENT -path "*${TASK}*/verifier/ua_task_score.json" | head -1) | python3 -m json.tool

# aggregate breakdown（含 protocol_compliance）
python3 -c "
import json,glob
p=sorted(glob.glob('$EXPERIMENT/$AGENT/**/harbor:*/*.json',recursive=True))
p=[x for x in p if 'trials' not in x][-1]
r=[x for x in json.load(open(p))['results'] if x['task_id']=='$TASK'][0]
print(json.dumps(r.get('breakdown',{}),indent=2))
print('violation:', r.get('anomaly',{}).get('multi_turn_protocol_violation',''))
"
```

## 注意事项

- **trajectory.json 是 UA 分析的主数据源**（含完整 user/agent 交互）；user_sim_state.json 用于状态摘要和 workspace_events
- 跳过 trajectory 中 `<pawbench-multi-turn-protocol>` 开头的 user step（系统注入，非真实用户）
- Claude Code 的 user-sim 工具名前缀为 `mcp__user-sim__`；其他 harness 为 `user-sim__`
- `workspace_events` 在当前 harness 可能为空；Patch 验证改走 trajectory 时序或 workspace 快照
- ua-cw 有 `user/patches/`；ua-mt 只有 `messages.jsonl`
- **协议违规（未调用 send_message_to_user）会导致 score=0**，即使 deliverable quality 很高
- Agent 产出的文件（如 `data_quality_report.md`）是 agent 交付物，不是 patch
