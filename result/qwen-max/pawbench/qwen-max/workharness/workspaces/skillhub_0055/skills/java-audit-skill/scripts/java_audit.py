#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Java/Kotlin 代码审计辅助脚本 - 跨平台统一入口
用于 Phase 0 代码度量、Phase 1 Tier 分类、Layer 1 预扫描、覆盖率检查

Usage:
    python java_audit.py <project_path> [options]

Options:
    --scan          执行 Layer 1 危险模式预扫描
    --tier          执行 Tier 分类
    --coverage      执行覆盖率检查（需配合 --reviewed-file）
    --reviewed-file 指定审阅清单文件路径
    --output        输出格式: json (默认), sarif
    --help, -h      显示帮助信息

Examples:
    python java_audit.py /path/to/project
    python java_audit.py /path/to/project --scan
    python java_audit.py /path/to/project --tier
    python java_audit.py /path/to/project --scan --tier
    python java_audit.py /path/to/project --coverage --reviewed-file reviewed.md
    python java_audit.py /path/to/project --scan --output sarif
"""

import os
import sys
import io
import json
import argparse
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# ============================================
# Windows 终端 UTF-8 编码修复
# ============================================
if sys.platform == 'win32':
    # 设置 stdout/stderr 为 UTF-8 编码
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ============================================
# 工具函数
# ============================================

def count_lines(file_path):
    """统计文件行数"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return sum(1 for _ in f)
    except:
        return 0

def get_file_content(file_path):
    """读取文件内容"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except:
        return ""

def write_file(file_path, content):
    """写入文件"""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

# ============================================
# Tier 分类
# ============================================

def classify_tier(file_path, content=None):
    """根据规则分类文件 Tier"""
    if content is None:
        content = get_file_content(file_path)
        if not content:
            return "T2"  # 保守兜底
    
    # Rule 0: 第三方库
    if '/target/' in file_path or 'node_modules' in file_path or '/build/' in file_path:
        return "SKIP"
    
    # Rule 2: Controller/Filter
    if any(x in content for x in ['@Controller', '@RestController', '@WebServlet', 'extends Filter', 'implements Filter', '@HttpController']):
        return "T1"
    
    # Rule 3: Service/DAO
    if any(x in content for x in ['@Service', '@Repository', '@Mapper', '@Dao', '@Component']):
        return "T2"
    
    # Rule 4: Util/Helper
    filename = os.path.basename(file_path).lower()
    if any(x in filename for x in ['util', 'helper', 'handler', 'utils', 'config']):
        return "T2"
    
    # Rule 6: Entity
    if any(x in content for x in ['@Entity', '@Table', '@Data', 'extends BaseEntity', 'data class']):
        return "T3"
    
    # Rule 7: 未匹配，保守兜底
    return "T2"

def run_tier_classification(project_path, output_dir):
    """执行 Tier 分类"""
    print("\n" + "=" * 60)
    print("Phase 1: Tier 分类")
    print("=" * 60)
    
    tier_files = {"T1": [], "T2": [], "T3": [], "SKIP": []}
    tier_loc = {"T1": 0, "T2": 0, "T3": 0, "SKIP": 0}
    
    for root, dirs, files in os.walk(project_path):
        # 排除目录
        dirs[:] = [d for d in dirs if d not in ['target', 'node_modules', '.git', 'build', 'out', '.gradle', '.idea', 'test', 'tests']]
        
        for file in files:
            if not file.endswith(('.java', '.kt')):
                continue
            
            file_path = os.path.join(root, file)
            content = get_file_content(file_path)
            tier = classify_tier(file_path, content)
            
            rel_path = os.path.relpath(file_path, project_path)
            lines = count_lines(file_path)
            
            tier_files[tier].append(rel_path)
            tier_loc[tier] += lines
    
    # 计算 EALOC
    ealoc = tier_loc["T1"] * 1.0 + tier_loc["T2"] * 0.5 + tier_loc["T3"] * 0.1
    agents_needed = max(1, -(-int(ealoc) // 15000))  # ceil division
    
    # 输出统计
    print("\n[*] Tier 分类统计:")
    for tier in ["T1", "T2", "T3", "SKIP"]:
        print(f"  {tier}: {len(tier_files[tier])} 文件, {tier_loc[tier]:,} LOC")
    
    print(f"\n[*] EALOC 计算:")
    print(f"  EALOC = {ealoc:,.0f}")
    print(f"  建议 Agent 数: {agents_needed}")
    
    # 生成报告
    report_path = os.path.join(output_dir, "tier-classification.md")
    report_content = f"""# Tier 分类结果

## 统计摘要

| Tier | 文件数 | LOC | 权重 | EALOC 贡献 |
|------|--------|-----|------|------------|
| T1 (Controller/Filter) | {len(tier_files['T1'])} | {tier_loc['T1']:,} | 1.0 | {tier_loc['T1']:,} |
| T2 (Service/DAO/Util) | {len(tier_files['T2'])} | {tier_loc['T2']:,} | 0.5 | {int(tier_loc['T2'] * 0.5):,} |
| T3 (Entity/VO/DTO) | {len(tier_files['T3'])} | {tier_loc['T3']:,} | 0.1 | {int(tier_loc['T3'] * 0.1):,} |
| SKIP | {len(tier_files['SKIP'])} | - | - | - |

**总 EALOC**: {ealoc:,.0f}  
**所需 Agent 数量**: {agents_needed} (按 15,000 EALOC/Agent 预算)

## Tier 分类规则

| 规则 | 条件 | Tier |
|------|------|------|
| Rule 0 | 第三方库源码 | SKIP |
| Rule 2 | @Controller/@RestController/@WebServlet/Filter | T1 |
| Rule 3 | @Service/@Repository/@Mapper | T2 |
| Rule 4 | 类名含 Util/Helper/Handler | T2 |
| Rule 5 | .properties/.yml/security.xml | T2 |
| Rule 6 | @Entity/@Table/@Data | T3 |
| Rule 7 | 未匹配任何规则 | T2 (保守兜底) |

## 文件清单

### T1 文件 ({len(tier_files['T1'])} 个)
```
{chr(10).join(tier_files['T1'][:50])}
{'... 还有 ' + str(len(tier_files['T1']) - 50) + ' 个文件' if len(tier_files['T1']) > 50 else ''}
```

### T2 文件 ({len(tier_files['T2'])} 个)
```
{chr(10).join(tier_files['T2'][:30])}
{'... 还有 ' + str(len(tier_files['T2']) - 30) + ' 个文件' if len(tier_files['T2']) > 30 else ''}
```

### T3 文件 ({len(tier_files['T3'])} 个)
```
{chr(10).join(tier_files['T3'][:30])}
{'... 还有 ' + str(len(tier_files['T3']) - 30) + ' 个文件' if len(tier_files['T3']) > 30 else ''}
```
"""
    
    write_file(report_path, report_content)
    print(f"\n[OK] Tier 分类报告: {report_path}")
    
    return {
        "tier_files": {k: len(v) for k, v in tier_files.items()},
        "tier_loc": tier_loc,
        "ealoc": ealoc,
        "agents_needed": agents_needed
    }

# ============================================
# Layer 1 预扫描
# ============================================

DANGER_PATTERNS = {
    "P0": {
        "反序列化": [
            "ObjectInputStream", "XMLDecoder", "XStream", "JSON.parseObject", "JSON.parse", "@type",
            "enableDefaultTyping", "activateDefaultTyping", "HessianInput", "Hessian2Input",
            "new Yaml(", "SnakeYAML"
        ],
        "SSTI": [
            "Velocity.evaluate", "VelocityEngine", "freemarker.template", "Template.process",
            "SpringTemplateEngine", "TemplateEngine.process"
        ],
        "表达式注入": [
            "SpelExpressionParser", "parseExpression", "evaluateExpression", "OgnlUtil", "Ognl.getValue",
            "MVEL.eval", "MVEL.executeExpression"
        ],
        "JNDI": ["InitialContext.lookup", "JdbcRowSetImpl", "setDataSourceName"],
        "命令执行": ["Runtime.getRuntime", "ProcessBuilder", ".exec("]
    },
    "P1": {
        "SQL注入": [
            "Statement", "createStatement", "executeQuery", "executeUpdate",
            "createQuery", "createNativeQuery"
        ],
        "MyBatis注入": ["${"],
        "SSRF": ["new URL(", "HttpURLConnection", "HttpClient", "RestTemplate", "WebClient", "OkHttpClient"],
        "文件操作": [
            "FileInputStream", "FileOutputStream", "FileWriter", "Files.read", "Files.write",
            "getOriginalFilename", "transferTo", "MultipartFile", "Paths.get"
        ],
        "XXE": ["DocumentBuilder", "SAXParser", "XMLReader", "XMLInputFactory", "SAXReader", "SAXBuilder"]
    },
    "P2": {
        "认证": ["@PreAuthorize", "@Secured", "@RolesAllowed", "hasRole", "hasAuthority", "permitAll"],
        "加密": ["MessageDigest", "Cipher", "SecretKey", "PasswordEncoder", "MD5", "SHA-1"],
        "配置": ["debug:", "swagger", "actuator", "h2.console"]
    }
}

def run_layer1_scan(project_path, output_dir):
    """执行 Layer 1 预扫描"""
    print("\n" + "=" * 60)
    print("Layer 1: 危险模式预扫描")
    print("=" * 60)
    
    results = defaultdict(lambda: defaultdict(list))
    
    for root, dirs, files in os.walk(project_path):
        # 排除目录
        dirs[:] = [d for d in dirs if d not in ['target', 'node_modules', '.git', 'build', 'out', '.gradle', '.idea', 'test', 'tests']]
        
        for file in files:
            if not file.endswith(('.java', '.kt', '.xml', '.gradle', '.kts', '.yml', '.yaml', '.properties')):
                continue
            
            file_path = os.path.join(root, file)
            content = get_file_content(file_path)
            
            for priority, categories in DANGER_PATTERNS.items():
                for category, keywords in categories.items():
                    for keyword in keywords:
                        if keyword in content:
                            # 找到行号
                            lines = content.split('\n')
                            for i, line in enumerate(lines, 1):
                                if keyword in line:
                                    rel_path = os.path.relpath(file_path, project_path)
                                    results[priority][category].append({
                                        "file": rel_path,
                                        "line": i,
                                        "keyword": keyword,
                                        "snippet": line.strip()[:100]
                                    })
                                    break  # 每个文件每种模式只记录一次
    
    # 输出结果
    total_findings = 0
    for priority in ["P0", "P1", "P2"]:
        if priority in results:
            print(f"\n[!] {priority} 级发现:")
            for category, findings in results[priority].items():
                print(f"  [{category}] {len(findings)} 处")
                total_findings += len(findings)
                for f in findings[:5]:
                    print(f"    - {f['file']}:{f['line']} ({f['keyword']})")
                if len(findings) > 5:
                    print(f"    ... 还有 {len(findings) - 5} 处")
    
    print(f"\n[*] 总计发现: {total_findings} 处危险模式")
    
    # 生成报告
    for priority in ["P0", "P1", "P2", "P3"]:
        report_path = os.path.join(output_dir, f"{priority.lower()}-{['critical', 'high', 'medium', 'low'][['P0', 'P1', 'P2', 'P3'].index(priority)]}.md")
        
        if priority in results or priority == "P3":
            content = f"# {priority} 级危险模式\n\n## 发现记录\n\n"
            
            if priority in results:
                for category, findings in results[priority].items():
                    content += f"### {category}\n\n"
                    for f in findings:
                        content += f"- `{f['file']}:{f['line']}` - `{f['keyword']}`\n"
                    content += "\n"
            else:
                content += "未发现该级别危险模式。\n"
            
            write_file(report_path, content)
    
    print(f"\n[OK] 扫描报告: {output_dir}/p0-critical.md, p1-high.md, p2-medium.md")
    
    return dict(results)

# ============================================
# 覆盖率检查
# ============================================

def run_coverage_check(project_path, reviewed_file, output_dir):
    """执行覆盖率检查"""
    print("\n" + "=" * 60)
    print("Phase 2.5: 覆盖率门禁检查")
    print("=" * 60)
    
    # 获取实际文件列表
    actual_files = set()
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ['target', 'node_modules', '.git', 'build', 'out', '.gradle', 'test', 'tests']]
        for file in files:
            if file.endswith(('.java', '.kt')):
                actual_files.add(file)
    
    actual_count = len(actual_files)
    
    # 读取审阅清单
    reviewed_files = set()
    if os.path.exists(reviewed_file):
        content = get_file_content(reviewed_file)
        # 提取 .java 和 .kt 文件名
        reviewed_files = set(re.findall(r'[a-zA-Z0-9_/-]+\.(java|kt)', content))
    
    reviewed_count = len(reviewed_files)
    
    # 计算遗漏
    missed_files = actual_files - reviewed_files
    missed_count = len(missed_files)
    
    # 计算覆盖率
    coverage = round((actual_count - missed_count) / actual_count * 100, 1) if actual_count > 0 else 0
    
    # 输出结果
    print(f"\n[*] 覆盖率统计:")
    print(f"  实际文件总数: {actual_count}")
    print(f"  已审阅文件数: {reviewed_count}")
    print(f"  遗漏文件数: {missed_count}")
    print(f"  覆盖率: {coverage}%")
    
    # 门禁判断
    if coverage == 100:
        print(f"\n[OK] 门禁通过 - 覆盖率 100%，可以进入 Phase 3")
    else:
        print(f"\n[!] 门禁未通过 - 覆盖率 < 100%，需要补扫")
        if missed_count > 0:
            print(f"\n[*] 遗漏文件列表 ({min(missed_count, 20)} 个):")
            for f in list(missed_files)[:20]:
                print(f"  - {f}")
            if missed_count > 20:
                print(f"  ... 还有 {missed_count - 20} 个文件")
    
    # 生成报告
    report_path = os.path.join(output_dir, "coverage-report.md")
    report_content = f"""# 覆盖率验证报告

## 覆盖率统计

| 指标 | 数值 |
|------|------|
| 实际文件总数 | {actual_count} |
| 已审阅文件数 | {reviewed_count} |
| 遗漏文件数 | {missed_count} |
| **覆盖率** | **{coverage}%** |

## 门禁状态

{"✅ **通过** - 覆盖率 100%，可以进入 Phase 3" if coverage == 100 else "❌ **未通过** - 覆盖率 < 100%，需要补扫"}

"""
    
    if missed_count > 0:
        report_content += f"""## 遗漏文件列表

```
{chr(10).join(list(missed_files)[:50])}
{'... 还有 ' + str(missed_count - 50) + ' 个文件' if missed_count > 50 else ''}
```
"""
    
    write_file(report_path, report_content)
    print(f"\n[OK] 覆盖率报告: {report_path}")
    
    return {
        "actual_count": actual_count,
        "reviewed_count": reviewed_count,
        "missed_count": missed_count,
        "coverage": coverage,
        "passed": coverage == 100
    }

# ============================================
# Phase 0 代码度量
# ============================================

def measure_project(project_path):
    """Phase 0: 项目度量"""
    stats = {
        "total_loc": 0,
        "java_files": 0,
        "kt_files": 0,
        "xml_files": 0,
        "gradle_files": 0,
        "controllers": 0,
        "modules": 0,
        "build_system": "unknown",
        "tier_stats": {
            "T1": {"files": 0, "loc": 0},
            "T2": {"files": 0, "loc": 0},
            "T3": {"files": 0, "loc": 0},
            "SKIP": {"files": 0, "loc": 0}
        }
    }
    
    pom_count = 0
    gradle_count = 0
    
    for root, dirs, files in os.walk(project_path):
        # 统计构建系统
        if 'pom.xml' in files:
            pom_count += 1
        if any(f in files for f in ['build.gradle', 'build.gradle.kts', 'settings.gradle', 'settings.gradle.kts']):
            gradle_count += 1
        
        # 排除目录
        dirs[:] = [d for d in dirs if d not in ['target', 'node_modules', '.git', 'build', 'out', '.gradle', '.idea']]
        
        for file in files:
            file_path = os.path.join(root, file)
            
            if file.endswith('.java'):
                stats["java_files"] += 1
                lines = count_lines(file_path)
                stats["total_loc"] += lines
                
                tier = classify_tier(file_path)
                stats["tier_stats"][tier]["files"] += 1
                stats["tier_stats"][tier]["loc"] += lines
                
                # 统计 Controller
                content = get_file_content(file_path)
                if any(x in content for x in ['@Controller', '@RestController', '@WebServlet', '@HttpController']):
                    stats["controllers"] += 1
            
            elif file.endswith('.kt'):
                stats["kt_files"] += 1
                lines = count_lines(file_path)
                stats["total_loc"] += lines
                
                tier = classify_tier(file_path)
                stats["tier_stats"][tier]["files"] += 1
                stats["tier_stats"][tier]["loc"] += lines
            
            elif file.endswith('.xml'):
                stats["xml_files"] += 1
            
            elif file.endswith(('.gradle', '.gradle.kts')):
                stats["gradle_files"] += 1
    
    # 确定构建系统
    if pom_count > 0:
        stats["build_system"] = "maven"
        stats["modules"] = pom_count
    elif gradle_count > 0:
        stats["build_system"] = "gradle"
        stats["modules"] = gradle_count
    
    # 计算 EALOC
    stats["ealoc"] = (
        stats["tier_stats"]["T1"]["loc"] * 1.0 +
        stats["tier_stats"]["T2"]["loc"] * 0.5 +
        stats["tier_stats"]["T3"]["loc"] * 0.1
    )
    
    stats["agents_needed"] = max(1, -(-int(stats["ealoc"]) // 15000))
    
    return stats

# ============================================
# SARIF 输出
# ============================================

def to_sarif(scan_results, project_path):
    """转换为 SARIF 格式"""
    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "Java Audit Skill - Layer 1 Scanner",
                    "version": "1.0.0",
                    "informationUri": "https://github.com/your-username/java-audit-skill"
                }
            },
            "results": []
        }]
    }
    
    severity_map = {"P0": "error", "P1": "warning", "P2": "note"}
    
    for priority, categories in scan_results.items():
        for category, findings in categories.items():
            for finding in findings:
                sarif["runs"][0]["results"].append({
                    "ruleId": f"{priority}-{category}",
                    "level": severity_map.get(priority, "warning"),
                    "message": {
                        "text": f"发现 {category} 相关的危险模式: {finding['keyword']}"
                    },
                    "locations": [{
                        "physicalLocation": {
                            "artifactLocation": {
                                "uri": finding["file"]
                            },
                            "region": {
                                "startLine": finding["line"]
                            }
                        }
                    }]
                })
    
    return sarif

# ============================================
# 主函数
# ============================================

def main():
    parser = argparse.ArgumentParser(
        description="Java/Kotlin 代码审计辅助脚本 - 跨平台统一入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python java_audit.py /path/to/project                    # 仅度量
  python java_audit.py /path/to/project --scan             # 度量 + Layer 1 预扫描
  python java_audit.py /path/to/project --tier             # 度量 + Tier 分类
  python java_audit.py /path/to/project --scan --tier      # 全部执行
  python java_audit.py /path/to/project --coverage --reviewed-file reviewed.md  # 覆盖率检查
  python java_audit.py /path/to/project --scan --output sarif  # SARIF 格式输出
        """
    )
    parser.add_argument("project_path", help="项目根目录")
    parser.add_argument("--scan", action="store_true", help="执行 Layer 1 危险模式预扫描")
    parser.add_argument("--tier", action="store_true", help="执行 Tier 分类")
    parser.add_argument("--coverage", action="store_true", help="执行覆盖率检查")
    parser.add_argument("--reviewed-file", help="审阅清单文件路径（用于覆盖率检查）")
    parser.add_argument("--output", choices=["json", "sarif"], default="json", help="输出格式 (默认: json)")
    parser.add_argument("--output-dir", help="输出目录 (默认: <project>/audit-output)")
    
    args = parser.parse_args()
    
    if not os.path.isdir(args.project_path):
        print(f"Error: {args.project_path} is not a valid directory")
        sys.exit(1)
    
    # 设置输出目录
    output_dir = args.output_dir or os.path.join(args.project_path, "audit-output")
    os.makedirs(output_dir, exist_ok=True)
    
    # Phase 0: 代码度量
    print("=" * 60)
    print("Phase 0: 代码库度量")
    print("=" * 60)
    
    stats = measure_project(args.project_path)
    
    print("\n[*] 项目统计:")
    print(f"  构建系统: {stats['build_system'].upper()}")
    print(f"  总代码行数: {stats['total_loc']:,}")
    print(f"  Java 文件: {stats['java_files']}")
    print(f"  Kotlin 文件: {stats['kt_files']}")
    print(f"  XML 文件: {stats['xml_files']}")
    print(f"  Controller 数量: {stats['controllers']}")
    print(f"  模块数: {stats['modules']}")
    
    print("\n[*] Tier 分类统计:")
    for tier in ["T1", "T2", "T3", "SKIP"]:
        print(f"  {tier}: {stats['tier_stats'][tier]['files']} 文件, {stats['tier_stats'][tier]['loc']:,} LOC")
    
    print("\n[*] EALOC 计算:")
    print(f"  EALOC = {stats['ealoc']:,.0f}")
    print(f"  建议 Agent 数: {stats['agents_needed']}")
    
    # Phase 1: Tier 分类
    tier_results = None
    if args.tier:
        tier_results = run_tier_classification(args.project_path, output_dir)
    
    # Layer 1: 预扫描
    scan_results = {}
    if args.scan:
        scan_results = run_layer1_scan(args.project_path, output_dir)
    
    # Phase 2.5: 覆盖率检查
    coverage_results = None
    if args.coverage:
        if not args.reviewed_file:
            print("\n[!] 错误: 覆盖率检查需要指定 --reviewed-file 参数")
        else:
            coverage_results = run_coverage_check(args.project_path, args.reviewed_file, output_dir)
    
    # 输出结果
    if args.output == "sarif" and args.scan:
        output_data = to_sarif(scan_results, args.project_path)
        output_path = os.path.join(output_dir, "audit-results.sarif")
    else:
        output_data = {
            "metrics": stats,
            "tier_results": tier_results,
            "scan_results": scan_results if args.scan else None,
            "coverage_results": coverage_results,
            "generated_at": datetime.now().isoformat()
        }
        output_path = os.path.join(output_dir, "audit-metrics.json")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n[OK] 结果已保存到: {output_path}")

if __name__ == "__main__":
    main()