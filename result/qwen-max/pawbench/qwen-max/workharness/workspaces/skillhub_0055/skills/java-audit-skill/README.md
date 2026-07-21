# Java Audit Skill

<div align="center">

**AI-Powered Java Security Audit Framework**

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Java](https://img.shields.io/badge/Java-8%2B-orange.svg)](https://www.oracle.com/java/)
[![AI](https://img.shields.io/badge/AI-LLM%20Driven-purple.svg)](https://en.wikipedia.org/wiki/Large_language_model)
[![Security](https://img.shields.io/badge/Security-Policy-green.svg)](SECURITY.md)

[English](#english) | [中文](#中文)
<a name="">email：aurora1219@139.com</a>
</div>

---

<a name="中文"></a>

## 📖 中文文档

### 概述

**Java Audit Skill** 是一个 AI 驱动的 Java/Kotlin 代码安全审计框架，将资深安全审计员的方法论编码成 LLM 可执行的工作流协议。

**核心价值**：解决裸跑 LLM 做代码审计的三大痛点——**覆盖率低、幻觉高、优先级混乱**。

> **LLM 有能力，缺纪律。** 本框架不教 LLM "什么是 SQL 注入"，而是给它装上资深审计员的工作骨架——定义工作流、分配资源、设置护栏、标准化输出。

---

### ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🔄 **6 阶段审计流水线** | 从代码度量到标准化报告，每个阶段有明确的输入输出和质量标准 |
| 📊 **三层审计架构** | 预扫描 + 双轨审计 + 语义验证，兼顾效率与深度 |
| 🚧 **覆盖率门禁** | 强制 100% 代码覆盖，解决 LLM "跳过不重要代码" 的天性 |
| 🎯 **DKTSS 评分体系** | 比 CVSS 更贴合实战的漏洞优先级评分 |
| 🛡️ **反幻觉机制** | 5 条铁律确保报告可信度 |
| 🔗 **调用链追踪** | 支持 LSP 语义级追踪，每一跳标注文件:行号 |

---

### 🎯 适用场景

- ✅ Java/Kotlin 项目的 **0day 漏洞挖掘**
- ✅ 企业级代码库的**安全审计**
- ✅ **CI/CD 集成**的前期漏洞发现
- ✅ 甲方安全建设（代码审计标准化）
- ✅ 安全培训（审计方法论学习）

---

### 📊 项目结构

```
java-audit-skill/
├── SKILL.md                    # 主协议文档（6阶段审计流水线）
├── README.md                   # 项目说明文档
├── references/
│   ├── dktss-scoring.md        # DKTSS 漏洞评分体系
│   ├── vulnerability-conditions.md  # 漏洞成立判断条件
│   ├── security-checklist.md   # 55+ 漏洞类型检查清单
│   └── report-template.md      # 标准化报告模板
├── scripts/
│   ├── java_audit.py           # 审计辅助脚本
│   ├── layer1-scan.sh          # Layer 1 危险模式预扫描
│   ├── tier-classify.sh        # Tier 分级脚本
│   └── coverage-check.sh       # 覆盖率门禁检查
└── assets/                     # 图表/流程图资源
```

---

### ⚙️ 6 阶段审计流水线

```
Phase 0 → Phase 1 → Phase 2 → Phase 2.5 → Phase 3 → Phase 4 → Phase 5
 代码度量   项目侦察   全量审计   覆盖率门禁  漏洞验证  规则沉淀  标准化报告
```

#### Phase 0: 代码库度量

统计项目规模，计算审计工作量。

**输出**: `metrics.json`

```json
{
  "total_loc": 131000,
  "java_files": 847,
  "controllers": 40,
  "modules": 5,
  "complexity_score": "HIGH"
}
```

#### Phase 1: 项目侦察 & EALOC 资源分配

**Tier 分类规则**：

| 规则 | 条件 | 分析深度 |
|------|------|----------|
| T1 | Controller / Filter / Servlet | 完整深度分析 |
| T2 | Service / DAO / Util | 聚焦关键维度 |
| T3 | Entity / VO / DTO | 快速模式匹配 |
| SKIP | 第三方库源码 | 不审计 |

**EALOC 公式**: `EALOC = T1_LOC × 1.0 + T2_LOC × 0.5 + T3_LOC × 0.1`

**Agent 分配**: `Agent数量 = ceil(EALOC / 15000)`

#### Phase 2: 三层审计架构

| 层级 | 内容 | 工具 |
|------|------|------|
| **Layer 1** | 全量预扫描 | ripgrep + Semgrep |
| **Layer 2** | 双轨审计 | LLM |
| **Layer 3** | 调用链语义验证 | LSP / Grep |

**双轨审计模型**：

```
轨道 1 (Sink-driven):    危险代码 ← 追踪来源 ← 用户输入
轨道 2 (Control-driven): 端点入口 → 检查权限 → 业务逻辑
```

> **为什么需要两条轨道？** 认证绕过这类漏洞，单独用 Sink-driven 找不到——它不是某行代码有问题，而是某个端点缺少了应有的权限检查。

#### Phase 2.5: 覆盖率门禁（核心创新）

**这是反 LLM 天性的核心设计**——LLM 倾向于跳过"看起来不重要"的代码，而漏洞偏偏喜欢藏在那些地方。

**门禁规则**：

- 每个 Agent 必须输出「审阅文件清单」
- 清单与实际文件列表做 diff
- **覆盖率 < 100% → 禁止进入 Phase 3**

#### Phase 3: 漏洞验证 & DKTSS 评分

**反幻觉 5 条铁律**：

1. 报告漏洞前必须验证文件存在
2. 代码片段必须来自实际文件，不得编造
3. 调用链每一跳必须标注 **文件:行号**
4. 不确定标记为 `HYPOTHESIS`，不得标记为 `CONFIRMED`
5. **宁可漏报，不可误报**

#### Phase 4: Semgrep 规则沉淀

将确认的漏洞模式转换为 Semgrep 规则，可集成到 CI/CD。

```yaml
rules:
  - id: velocity-ssti
    patterns:
      - pattern: Velocity.evaluate($CONTEXT, $WRITER, $NAME, $USER_INPUT)
    message: 检测到用户可控的 Velocity 模板输入，存在 SSTI 风险
    severity: ERROR
    languages: [java]
```

#### Phase 5: 标准化报告

**9 个必填字段组**：

1. 基本信息（状态、类型、CWE-ID、DKTSS 评分）
2. 触发条件
3. 所需权限
4. 漏洞原理
5. 代码证据
6. 调用链
7. PoC
8. 验证结果
9. 修复建议

---

### 🎯 DKTSS 评分体系

DKTSS（DarkKnight Threat Scoring System）是比 CVSS 更贴合实战的漏洞优先级评分标准。

**核心公式**：

```
Score = Base - Friction + Weapon + Ver
```

| 维度 | 说明 |
|------|------|
| **Base** | 漏洞类型 + 实际影响 |
| **Friction** | 实战阻力（访问路径/权限门槛/交互复杂度） |
| **Weapon** | 武器化程度 |
| **Ver** | 版本因子 |

**评分示例**：

| 漏洞 | CVSS | DKTSS | 分析 |
|------|------|-------|------|
| 后台 SQL 注入 | 8.8 (High) | 6 (Medium) | 需要管理员权限，实战优先级降低 |
| 前台 Velocity SSTI | 9.8 (Critical) | 10 (Critical) | 无需认证 + 现成 EXP |
| 内网 SSRF | 7.5 (High) | 5 (Medium) | 需要内网访问 |

**严重程度等级**：

| DKTSS | 等级 | 响应时效 |
|-------|------|----------|
| 9-10 | Critical | 24h 内 |
| 7-8 | High | 7 天内 |
| 5-6 | Medium | 30 天内 |
| 3-4 | Low | 90 天内 |

---

### 🔍 覆盖的漏洞类型

#### P0 级（严重）- Semgrep 规则

| 类型 | 具体漏洞 |
|------|---------|
| **反序列化** | Fastjson、Jackson、XStream、Hessian、SnakeYAML、Java原生序列化 |
| **SSTI** | Velocity、FreeMarker、Thymeleaf、Pebble |
| **表达式注入** | SpEL、OGNL、MVEL |
| **JNDI 注入** | InitialContext.lookup、JdbcRowSetImpl |
| **命令执行** | Runtime.exec、ProcessBuilder |

#### P1 级（高危）- Semgrep 规则 + 代码分析

| 类型 | 具体漏洞 | 检测方式 |
|------|---------|----------|
| **SQL 注入** | MyBatis `${}`、JDBC 原生拼接、JPA/HQL 注入 | Semgrep |
| **SSRF** | URL 类、HttpClient、RestTemplate、WebClient | Semgrep |
| **文件操作** | 路径穿越、任意文件上传/读取/写入 | Semgrep |
| **XXE** | DocumentBuilder、SAXParser、XMLReader | Semgrep |
| **越权** | 水平越权、垂直越权 | 代码分析 |
| **业务逻辑** | 支付金额篡改、库存并发 | 代码分析 |

#### P2 级（中危）- Semgrep 规则 + 配置分析

| 类型 | 具体漏洞 | 检测方式 |
|------|---------|----------|
| **认证授权** | 越权访问、认证绕过、Session 固定、弱密码策略 | Semgrep + 代码分析 |
| **加密安全** | 弱哈希算法、硬编码密钥、不安全随机数 | Semgrep |
| **信息泄露** | 敏感信息日志、错误信息暴露、配置文件泄露 | Semgrep |
| **配置安全** | Debug 模式、Swagger 暴露、Actuator 暴露 | Semgrep + 配置分析 |

**Semgrep 规则统计**: 198 条规则，覆盖 50+ 组件配置安全

#### 依赖安全 - 版本分析

读取 `pom.xml`/`build.gradle`，检查以下依赖版本：

| 依赖 | 危险版本 | 安全版本 |
|------|----------|----------|
| Log4j2 | < 2.17.1 | ≥ 2.17.1 |
| Fastjson | < 1.2.83 | ≥ 1.2.83 或 2.x |
| Shiro | < 1.13.0 | ≥ 1.13.0 |
| Spring | < 5.3.18 | ≥ 5.3.18 |

---

### 🚀 快速开始

#### 前置要求

- Python 3.8+
- ripgrep (`rg`)
- Semgrep（可选，用于 Layer 1 扫描）

#### 安装

```bash
# 克隆仓库
git clone https://github.com/AuroraProudmoore/java-audit-skill.git
cd java-audit-skill

# 安装依赖
pip install -r requirements.txt
```

#### 使用方式

**方式一：作为 OpenClaw Skill 使用**

将项目放置在 `~/.openclaw/workspace/skills/java-audit-skill/` 目录下，在对话中触发：

```
帮我审计这个 Java 项目：/path/to/project
```

**方式二：独立使用脚本**

**Linux/macOS:**

```bash
# Phase 0: 代码度量
python scripts/java_audit.py /path/to/project

# 度量 + Layer 1 预扫描
python scripts/java_audit.py /path/to/project --scan

# 度量 + Tier 分类
python scripts/java_audit.py /path/to/project --tier

# 全部执行
python scripts/java_audit.py /path/to/project --scan --tier

# 覆盖率检查
python scripts/java_audit.py /path/to/project --coverage --reviewed-file reviewed.md

# 输出 SARIF 格式（用于 GitHub Code Scanning）
python scripts/java_audit.py /path/to/project --scan --output sarif
```

**查看帮助：**

```bash
python scripts/java_audit.py --help
```

**注意**：Shell 脚本（`.sh` 文件）仍保留在 `scripts/` 目录中，供 Linux/macOS 用户在需要时使用。

---

### 📚 审计方法论

#### AI 代码审计 6 大核心方法

| 方法 | 说明 | 适用漏洞 |
|------|------|----------|
| **语义化规则匹配** | 识别业务含义，适配任意命名规范 | 越权、未授权访问、验证码绕过 |
| **因果推理审计** | 构建业务状态机，反事实推理 | 流程绕过、状态跳转 |
| **权限一致性审计** | 水平/垂直/一致性三维校验 | 越权漏洞 |
| **边界条件对抗** | 基于业务语义生成测试用例 | 支付漏洞、库存漏洞 |
| **漏洞链推理** | 自动串联可利用的漏洞点 | 组合攻击链 |
| **逻辑缺陷审计** | 白盒扫描高频缺陷模式 | 权限缺失、异常处理错误 |

#### 长上下文 5 层解决方案

| 层级 | 方案 | 效果 |
|------|------|------|
| 1 | 源头治理（过滤注释/测试/第三方库） | 降低 60%+ 上下文 |
| 2 | 三层递进式架构 | 模块级 32K-64K |
| 3 | 结构化语义压缩 | 降低 70%+ Token |
| 4 | RAG + 多轮对话 | 突破窗口物理限制 |
| 5 | 增量审计（Git diff） | 减少 90%+ 上下文 |

---

### ⚠️ 5 大落地误区

| 误区 | 问题 | 正确做法 |
|------|------|----------|
| ❌ 简单按行数拆分 | 破坏代码语义关联 | 按业务边界、依赖关系拆分 |
| ❌ 过度依赖长窗口 | 超过 128K 后注意力衰减 | 中等窗口 + 裁剪分块 + RAG |
| ❌ 全量代码丢给 AI | 上下文溢出、误报飙升 | 完成过滤与风险分级 |
| ❌ 完全抛弃传统工具 | LLM 存在幻觉问题 | 传统工具 + AI + 传统验证 |
| ❌ 只关注已知漏洞 | 浪费 AI 核心能力 | 聚焦业务逻辑漏洞 |

---

### 📖 参考文档

| 文档 | 说明 |
|------|------|
| [SKILL.md](SKILL.md) | 完整协议文档 |
| [references/dktss-scoring.md](references/dktss-scoring.md) | DKTSS 评分体系 |
| [references/vulnerability-conditions.md](references/vulnerability-conditions.md) | 漏洞成立条件 |
| [references/security-checklist.md](references/security-checklist.md) | 安全检查清单 |
| [references/report-template.md](references/report-template.md) | 报告模板 |

---

### 🤝 贡献指南

欢迎贡献！请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解详情。

**贡献方向**：

- 🐛 报告 Bug
- 💡 提出新功能建议
- 📝 改进文档
- 🔧 贡献代码
- 📋 分享审计案例

---

### 📄 许可证

本项目采用 [MIT License](LICENSE) 开源协议。

---

<a name="english"></a>

## 📖 English Documentation

### Overview

**Java Audit Skill** is an AI-powered Java/Kotlin code security audit framework that encodes senior security auditors' methodologies into LLM-executable workflow protocols.

**Core Value**: Solves the three main pain points of using LLMs for code auditing—**low coverage, high hallucination, and chaotic prioritization**.

> **LLMs have capability but lack discipline.** This framework doesn't teach LLMs "what is SQL injection"—they already know. It equips them with the work framework of senior auditors: defining workflows, allocating resources, setting guardrails, and standardizing outputs.

---

### ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🔄 **6-Phase Audit Pipeline** | From code metrics to standardized reports, each phase has clear inputs, outputs, and quality standards |
| 📊 **3-Layer Audit Architecture** | Pre-scan + Dual-track audit + Semantic verification |
| 🚧 **Coverage Gate** | Enforces 100% code coverage, solving LLM's tendency to skip "unimportant code" |
| 🎯 **DKTSS Scoring System** | Vulnerability priority scoring more practical than CVSS |
| 🛡️ **Anti-Hallucination Mechanism** | 5 iron rules ensure report credibility |
| 🔗 **Call Chain Tracing** | Supports LSP semantic-level tracing with file:line annotations |

---

### 🎯 Use Cases

- ✅ **0-day vulnerability hunting** in Java/Kotlin projects
- ✅ **Security audits** for enterprise codebases
- ✅ **CI/CD integration** for early vulnerability detection
- ✅ Security team standardization
- ✅ Security training and methodology learning

---

### ⚙️ 6-Phase Audit Pipeline

```
Phase 0 → Phase 1 → Phase 2 → Phase 2.5 → Phase 3 → Phase 4 → Phase 5
 Metrics    Recon     Audit     Coverage    Verify    Rules    Report
```

#### Phase 0: Code Metrics

Measure project scale and calculate audit workload.

**Output**: `metrics.json`

```json
{
  "total_loc": 131000,
  "java_files": 847,
  "controllers": 40,
  "modules": 5,
  "complexity_score": "HIGH"
}
```

#### Phase 1: Reconnaissance & EALOC Allocation

**Tier Classification Rules**:

| Tier | Condition | Analysis Depth |
|------|-----------|----------------|
| T1 | Controller / Filter / Servlet | Full depth analysis |
| T2 | Service / DAO / Util | Focus on key dimensions |
| T3 | Entity / VO / DTO | Quick pattern matching |
| SKIP | Third-party library source | Skip audit |

**EALOC Formula**: `EALOC = T1_LOC × 1.0 + T2_LOC × 0.5 + T3_LOC × 0.1`

**Agent Allocation**: `Agents = ceil(EALOC / 15000)`

#### Phase 2: 3-Layer Audit Architecture

| Layer | Content | Tools |
|-------|---------|-------|
| **Layer 1** | Full pre-scan (no LLM) | ripgrep + Semgrep |
| **Layer 2** | Dual-track audit | LLM |
| **Layer 3** | Call chain semantic verification | LSP / Grep |

**Dual-Track Audit Model**:

```
Track 1 (Sink-driven):    Dangerous code ← Trace source ← User input
Track 2 (Control-driven): Endpoint entry → Check permissions → Business logic
```

> **Why dual tracks?** Authentication bypass vulnerabilities cannot be found with Sink-driven alone—they're not a problematic line of code, but a missing permission check at an endpoint.

#### Phase 2.5: Coverage Gate (Core Innovation)

**This is the core design against LLM's nature**—LLMs tend to skip "seemingly unimportant" code, while vulnerabilities love to hide there.

**Gate Rules**:

- Each Agent must output a "Reviewed Files List"
- Diff the list against actual files
- **Coverage < 100% → Phase 3 blocked**

#### Phase 3: Verification & DKTSS Scoring

**5 Anti-Hallucination Iron Rules**:

1. Must verify file exists before reporting vulnerability
2. Code snippets must come from actual files, no fabrication
3. Every hop in call chain must have **file:line** annotation
4. Uncertain findings marked as `HYPOTHESIS`, never `CONFIRMED`
5. **Better to miss than to false positive**

#### Phase 4: Semgrep Rule Generation

Convert confirmed vulnerability patterns to Semgrep rules for CI/CD integration.

```yaml
rules:
  - id: velocity-ssti
    patterns:
      - pattern: Velocity.evaluate($CONTEXT, $WRITER, $NAME, $USER_INPUT)
    message: User-controllable Velocity template input detected, SSTI risk
    severity: ERROR
    languages: [java]
```

#### Phase 5: Standardized Report

**9 Required Field Groups**:

1. Basic Info (status, type, CWE-ID, DKTSS score)
2. Trigger Conditions
3. Required Privileges
4. Vulnerability Principle
5. Code Evidence
6. Call Chain
7. PoC
8. Verification Results
9. Remediation

---

### 🎯 DKTSS Scoring System

DKTSS (DarkKnight Threat Scoring System) is a vulnerability priority scoring standard more practical than CVSS.

**Core Formula**:

```
Score = Base - Friction + Weapon + Ver
```

| Dimension | Description |
|-----------|-------------|
| **Base** | Vulnerability type + actual impact |
| **Friction** | Practical resistance (access path / privilege threshold / interaction complexity) |
| **Weapon** | Weaponization level |
| **Ver** | Version factor |

**Scoring Example**:

| Vulnerability | CVSS | DKTSS | Analysis |
|---------------|------|-------|----------|
| Admin SQL Injection | 8.8 (High) | 6 (Medium) | Requires admin privileges, lowers practical priority |
| Frontend Velocity SSTI | 9.8 (Critical) | 10 (Critical) | No auth required + existing exploit |
| Internal SSRF | 7.5 (High) | 5 (Medium) | Requires internal network access |

**Severity Levels**:

| DKTSS | Level | Response Time |
|-------|-------|---------------|
| 9-10 | Critical | Within 24h |
| 7-8 | High | Within 7 days |
| 5-6 | Medium | Within 30 days |
| 3-4 | Low | Within 90 days |

---

### 🔍 Covered Vulnerability Types

#### P0 (Critical)

| Category | Specific Vulnerabilities |
|----------|-------------------------|
| **Deserialization** | Fastjson, Jackson, XStream, Hessian, SnakeYAML, Java native serialization |
| **SSTI** | Velocity, FreeMarker, Thymeleaf, Pebble |
| **Expression Injection** | SpEL, OGNL, MVEL |
| **JNDI Injection** | InitialContext.lookup, JdbcRowSetImpl |
| **Command Execution** | Runtime.exec, ProcessBuilder |

#### P1 (High)

| Category | Specific Vulnerabilities |
|----------|-------------------------|
| **SQL Injection** | MyBatis `${}`, JDBC native concatenation, JPA/HQL injection |
| **SSRF** | URL class, HttpClient, RestTemplate, WebClient |
| **File Operations** | Path traversal, arbitrary file upload/read/write |

#### P2 (Medium)

| Category | Specific Vulnerabilities |
|----------|-------------------------|
| **Authentication/Authorization** | Access bypass, auth bypass, session fixation, weak password policy |
| **Cryptography** | Weak hashing, hardcoded keys, insecure random numbers |
| **Information Disclosure** | Sensitive logs, error exposure, config file leaks |

---

### 🚀 Quick Start

#### Prerequisites

- Python 3.8+
- ripgrep (`rg`)
- Semgrep (optional, for Layer 1 scanning)

#### Installation

```bash
# Clone repository
git clone https://github.com/AuroraProudmoore/java-audit-skill.git
cd java-audit-skill

# Install dependencies
pip install -r requirements.txt
```

#### Usage

**Option 1: As OpenClaw Skill**

Place the project in `~/.openclaw/workspace/skills/java-audit-skill/` and trigger in conversation:

```
Help me audit this Java project: /path/to/project
```

**Option 2: Standalone Script Usage (Cross-Platform)**

```bash
# Phase 0: Code metrics
python scripts/java_audit.py /path/to/project

# Metrics + Layer 1 pre-scan
python scripts/java_audit.py /path/to/project --scan

# Metrics + Tier classification
python scripts/java_audit.py /path/to/project --tier

# All features
python scripts/java_audit.py /path/to/project --scan --tier

# Coverage check
python scripts/java_audit.py /path/to/project --coverage --reviewed-file reviewed.md

# Output SARIF format (for GitHub Code Scanning)
python scripts/java_audit.py /path/to/project --scan --output sarif
```

**View help:**

```bash
python scripts/java_audit.py --help
```

**Note**: Shell scripts (`.sh` files) are still available in `scripts/` directory for Linux/macOS users who prefer them.

---

### 📚 Methodology

#### 6 Core Methods for AI Code Auditing

| Method | Description | Applicable Vulnerabilities |
|--------|-------------|---------------------------|
| **Semantic Rule Matching** | Identify business meaning, adapt to any naming convention | Authorization bypass, unauthorized access |
| **Causal Reasoning Audit** | Build business state machine, counterfactual reasoning | Process bypass, state jumping |
| **Permission Consistency Audit** | Horizontal/vertical/consistency 3D validation | Authorization vulnerabilities |
| **Boundary Condition Adversarial** | Generate test cases based on business semantics | Payment vulnerabilities, inventory issues |
| **Vulnerability Chain Reasoning** | Auto-chain exploitable vulnerability points | Combined attack chains |
| **Logic Defect Audit** | White-box scan for high-frequency defect patterns | Missing permissions, exception handling errors |

---

### ⚠️ 5 Common Pitfalls

| Pitfall | Problem | Correct Approach |
|---------|---------|------------------|
| ❌ Simple line-based splitting | Breaks code semantic relationships | Split by business boundaries and dependencies |
| ❌ Over-reliance on long context | Attention degrades after 128K tokens | Medium context + chunking + RAG |
| ❌ Throw all code at AI | Context overflow, high false positives | Complete filtering and risk grading |
| ❌ Abandon traditional tools | LLMs have hallucination issues | Traditional tools + AI + traditional verification |
| ❌ Focus only on known vulnerabilities | Wastes AI's core capability | Focus on business logic vulnerabilities |

---

### 🤝 Contributing

Contributions welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

**Contribution Areas**:

- 🐛 Report bugs
- 💡 Suggest new features
- 📝 Improve documentation
- 🔧 Contribute code
- 📋 Share audit case studies

---

### 📄 License

This project is licensed under the [MIT License](LICENSE).

---

<div align="center">

**Made with ❤️ by Security Researchers**

[⬆ Back to Top](#java-audit-skill)

</div>
