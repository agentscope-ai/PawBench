# Contributing to Java Audit Skill

感谢你考虑为 Java Audit Skill 做贡献！🎉

本文档将帮助你了解如何参与项目贡献。

---

## 📋 目录

- [行为准则](#行为准则)
- [如何贡献](#如何贡献)
- [开发指南](#开发指南)
- [提交规范](#提交规范)
- [Pull Request 流程](#pull-request-流程)
- [报告 Bug](#报告-bug)
- [功能建议](#功能建议)
- [改进文档](#改进文档)

---

## 行为准则

本项目采用贡献者公约作为行为准则。参与本项目即表示你同意遵守其条款。请阅读 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 了解详情。

**简而言之**：尊重所有贡献者，保持友好、包容的交流环境。

---

## 如何贡献

### 🐛 报告 Bug

如果你发现了 Bug，请通过 [GitHub Issues](https://github.com/AuroraProudmoore/java-audit-skill/issues) 提交。

**Bug 报告模板**：

```markdown
**Bug 描述**
简要描述遇到的问题。

**复现步骤**
1. 执行命令 `python scripts/java_audit.py /path/to/project --scan`
2. 查看输出
3. 发现错误...

**期望行为**
描述你期望发生什么。

**实际行为**
描述实际发生了什么。

**环境信息**
- OS: [e.g. Ubuntu 22.04 / Windows 11 / macOS 14]
- Python: [e.g. 3.10.12]
- Java Audit Skill version: [e.g. v1.0.0]

**日志/截图**
如有日志或截图，请附上。
```

### 💡 功能建议

我们欢迎新功能建议！请在 Issue 中详细描述：

- 功能描述
- 使用场景
- 预期收益
- 可能的实现思路（可选）

### 📝 改进文档

文档改进包括但不限于：

- 修正拼写/语法错误
- 补充缺失的文档
- 改进文档结构
- 添加更多示例
- 翻译文档

### 🔧 贡献代码

详见下方的 [开发指南](#开发指南)。

---

## 开发指南

### 环境准备

```bash
# Fork 并克隆仓库
git clone https://github.com/YOUR_USERNAME/java-audit-skill.git
cd java-audit-skill

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows

# 安装开发依赖
pip install -r requirements-dev.txt
```

### 项目结构

```
java-audit-skill/
├── SKILL.md              # 主协议文档（核心）
├── README.md             # 项目说明
├── CONTRIBUTING.md       # 贡献指南（本文件）
├── LICENSE               # MIT 许可证
├── references/           # 参考文档
│   ├── dktss-scoring.md
│   ├── vulnerability-conditions.md
│   ├── security-checklist.md
│   └── report-template.md
├── scripts/              # 工具脚本
│   ├── java_audit.py
│   ├── layer1-scan.sh
│   ├── tier-classify.sh
│   └── coverage-check.sh
└── assets/               # 资源文件
```

### 代码规范

#### Python 代码

- 遵循 [PEP 8](https://peps.python.org/pep-0008/) 规范
- 使用 4 空格缩进
- 函数/类需添加 docstring
- 使用类型注解（Type Hints）

```python
def classify_tier(file_path: str, content: str = None) -> str:
    """根据规则分类文件 Tier
    
    Args:
        file_path: 文件路径
        content: 文件内容（可选，不提供则自动读取）
    
    Returns:
        Tier 分级: "T1" / "T2" / "T3" / "SKIP"
    """
    # ...
```

#### Shell 脚本

- 使用 `#!/bin/bash` 或 `#!/usr/bin/env bash`
- 使用 `set -e` 在脚本开头，遇到错误立即退出
- 函数命名使用小写下划线
- 添加注释说明复杂逻辑

#### Markdown 文档

- 使用 UTF-8 编码
- 标题层级清晰，不跳级
- 代码块指定语言
- 表格对齐

### 测试

```bash
# 运行测试
pytest tests/

# 检查代码风格
flake8 scripts/
black --check scripts/
```

---

## 提交规范

### Commit Message 格式

使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Type 类型

| Type | 说明 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(phase3): add DKTSS scoring calculator` |
| `fix` | Bug 修复 | `fix(scan): correct regex pattern for SQL injection` |
| `docs` | 文档更新 | `docs(readme): add English documentation` |
| `style` | 代码格式（不影响功能） | `style: format code with black` |
| `refactor` | 重构 | `refactor(tier): simplify classification logic` |
| `perf` | 性能优化 | `perf(scan): parallelize Layer 1 scanning` |
| `test` | 添加/修改测试 | `test: add unit tests for tier classification` |
| `chore` | 构建/工具变动 | `chore: update dependencies` |

### 示例

```bash
# 新功能
git commit -m "feat(phase2): add SpEL expression injection detection"

# Bug 修复
git commit -m "fix(mybatis): handle nested ${} in XML comments"

# 文档
git commit -m "docs(dktss): add scoring examples for SSRF"
```

---

## Pull Request 流程

### 1. 创建分支

```bash
# 从 main 创建功能分支
git checkout -b feat/your-feature-name
```

分支命名建议：
- `feat/xxx` - 新功能
- `fix/xxx` - Bug 修复
- `docs/xxx` - 文档更新
- `refactor/xxx` - 重构

### 2. 开发并提交

```bash
# 编写代码
# ...

# 提交
git add .
git commit -m "feat: add new feature"
```

### 3. 推送并创建 PR

```bash
# 推送到你的 fork
git push origin feat/your-feature-name
```

然后在 GitHub 上创建 Pull Request。

### 4. PR 检查清单

提交 PR 前，请确认：

- [ ] 代码通过所有测试
- [ ] 代码风格符合规范
- [ ] 新功能有对应的测试
- [ ] 文档已更新（如有必要）
- [ ] Commit message 符合规范
- [ ] PR 描述清晰说明改动内容

### 5. PR 模板

```markdown
## 改动描述

简要描述本次 PR 的改动内容。

## 改动类型

- [ ] Bug 修复
- [ ] 新功能
- [ ] 重构
- [ ] 文档更新
- [ ] 其他

## 相关 Issue

Fixes #123

## 测试

描述你如何测试这些改动。

## 检查清单

- [ ] 代码通过测试
- [ ] 代码风格检查通过
- [ ] 文档已更新
```

---

## 漏洞模式贡献指南

我们特别欢迎贡献新的漏洞检测模式！

### 添加新漏洞类型的步骤

1. **更新 `references/security-checklist.md`**

在对应风险等级（P0/P1/P2）下添加：

```markdown
| # | 检查项 | 扫描命令/方法 | 风险 | 验证要点 |
|---|--------|---------------|------|----------|
| x.x.x | 新漏洞类型 | `grep -rn "pattern" --include="*.java"` | 严重 | 验证条件说明 |
```

2. **更新 `references/vulnerability-conditions.md`**

添加漏洞成立判断条件：

```markdown
### 新漏洞类型

**触发条件**:
1. 条件一
2. 条件二

**判断流程**:
```
发现可疑模式 → 检查条件一 → 检查条件二 → 确认漏洞
```
```

3. **更新 Layer 1 扫描脚本**

在 `scripts/layer1-scan.sh` 或 `scripts/java_audit.py` 中添加扫描模式。

4. **添加 Semgrep 规则**（可选）

创建 `rules/semgrep/your-rule.yaml`。

---

## 分享审计案例

欢迎分享你的审计案例！案例可以帮助其他用户学习如何使用本框架。

### 案例模板

在 `examples/` 目录下创建 `case-study-xxx.md`：

```markdown
# 案例研究：[项目名称/类型] 安全审计

## 项目信息

- 项目类型：电商系统 / 内部管理系统 / ...
- 技术栈：Spring Boot / MyBatis / ...
- 代码规模：XXX LOC

## 审计过程

### Phase 0 代码度量
...

### Phase 1 侦察发现
...

### Phase 2 审计发现
...

## 发现的漏洞

### 漏洞 1：XXX

- 类型：SQL 注入
- 位置：`com/example/UserMapper.xml:42`
- DKTSS 评分：7
- 修复方案：...

## 经验总结

...
```

---

## 获取帮助

如果你有任何问题，可以通过以下方式获取帮助：

- 📖 阅读 [README.md](README.md) 和 [SKILL.md](SKILL.md)
- 💬 在 [GitHub Discussions](https://github.com/AuroraProudmoore/java-audit-skill/discussions) 提问
- 🐛 在 [GitHub Issues](https://github.com/AuroraProudmoore/java-audit-skill/issues) 提交问题

---

## 许可证

提交贡献即表示你同意你的代码将在 MIT 许可证下发布。

---

<div align="center">

**感谢你的贡献！❤️**

</div>