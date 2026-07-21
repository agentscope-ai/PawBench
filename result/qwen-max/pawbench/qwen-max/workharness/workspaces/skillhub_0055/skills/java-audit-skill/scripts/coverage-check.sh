#!/bin/bash
# 覆盖率验证脚本
# 对比 Agent 输出的审阅文件清单与实际文件列表，计算覆盖率
#
# Usage: ./coverage-check.sh <target_dir> [reviewed_file] [output_file]
#
# Arguments:
#   target_dir    - 项目根目录 (default: .)
#   reviewed_file - 审阅清单文件 (default: ./audit-output/reviewed-files.md)
#   output_file   - 输出报告路径 (default: ./audit-output/coverage-report.md)
#
# Requirements:
#   - bash, find, grep, wc
#   - python3 (for calculations)

set -e

# 显示帮助信息
show_help() {
    echo "Usage: $0 [target_dir] [reviewed_file] [output_file]"
    echo ""
    echo "Arguments:"
    echo "  target_dir    - 项目根目录 (default: .)"
    echo "  reviewed_file - 审阅清单文件 (default: ./audit-output/reviewed-files.md)"
    echo "  output_file   - 输出报告路径 (default: ./audit-output/coverage-report.md)"
    echo ""
    echo "Exit codes:"
    echo "  0 - 覆盖率 100%"
    echo "  1 - 覆盖率 < 100%"
    echo ""
    echo "Example:"
    echo "  $0 /path/to/project ./findings/reviewed.md ./reports/coverage.md"
    exit 0
}

# 检查参数
if [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    show_help
fi

TARGET_DIR="${1:-.}"
REVIEWED_FILE="${2:-./audit-output/reviewed-files.md}"
OUTPUT_FILE="${3:-./audit-output/coverage-report.md}"

# 创建输出目录
mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "=== 覆盖率验证开始 ==="
echo "目标目录: $TARGET_DIR"
echo "审阅清单: $REVIEWED_FILE"

# 获取实际 Java 文件列表（排除 test 和第三方库）
echo "[1/3] 获取实际文件列表..."
ACTUAL_FILES=$(find "$TARGET_DIR" -name "*.java" -type f 2>/dev/null | grep -v "/test/" | grep -v "/target/" | grep -v "/node_modules/" | sort)
ACTUAL_COUNT=$(echo "$ACTUAL_FILES" | grep -v "^$" | wc -l)

# 从审阅清单中提取已审阅文件
echo "[2/3] 解析审阅清单..."
if [[ -f "$REVIEWED_FILE" ]]; then
    # 提取 Markdown 表格中的文件路径
    REVIEWED_FILES=$(grep -oE "[a-zA-Z0-9_/-]+\.java" "$REVIEWED_FILE" | sort -u)
    REVIEWED_COUNT=$(echo "$REVIEWED_FILES" | grep -v "^$" | wc -l)
else
    echo "警告: 审阅清单文件不存在: $REVIEWED_FILE"
    REVIEWED_FILES=""
    REVIEWED_COUNT=0
fi

# 计算遗漏文件
echo "[3/3] 计算覆盖率..."
MISSED_FILES=""
MISSED_COUNT=0

while IFS= read -r actual_file; do
    if [[ -z "$actual_file" ]]; then
        continue
    fi
    
    # 提取文件名
    filename=$(basename "$actual_file")
    
    # 检查是否在已审阅列表中
    if ! echo "$REVIEWED_FILES" | grep -q "$filename"; then
        MISSED_FILES="$MISSED_FILES$actual_file\n"
        ((MISSED_COUNT++))
    fi
done <<< "$ACTUAL_FILES"

# 计算覆盖率
if [[ $ACTUAL_COUNT -gt 0 ]]; then
    COVERAGE=$(python3 -c "print(round(($ACTUAL_COUNT - $MISSED_COUNT) / $ACTUAL_COUNT * 100, 1))" 2>/dev/null || echo "N/A")
else
    COVERAGE="N/A"
fi

# 生成报告
cat > "$OUTPUT_FILE" << EOF
# 覆盖率验证报告

## 覆盖率统计

| 指标 | 数值 |
|------|------|
| 实际文件总数 | $ACTUAL_COUNT |
| 已审阅文件数 | $REVIEWED_COUNT |
| 遗漏文件数 | $MISSED_COUNT |
| **覆盖率** | **$COVERAGE%** |

## 门禁状态

EOF

if [[ "$COVERAGE" == "100" ]] || [[ "$COVERAGE" == "100.0" ]]; then
    cat >> "$OUTPUT_FILE" << EOF
✅ **通过** - 覆盖率达到 100%，可以进入 Phase 3
EOF
else
    cat >> "$OUTPUT_FILE" << EOF
❌ **未通过** - 覆盖率未达到 100%，需要补扫

**需要启动补扫 Agent 处理以下 $MISSED_COUNT 个文件：**
EOF
fi

# 添加遗漏文件列表
if [[ $MISSED_COUNT -gt 0 ]]; then
    echo "" >> "$OUTPUT_FILE"
    echo "## 遗漏文件列表" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"
    echo '```' >> "$OUTPUT_FILE"
    echo -e "$MISSED_FILES" | head -50 >> "$OUTPUT_FILE"
    if [[ $MISSED_COUNT -gt 50 ]]; then
        echo "... 还有 $((MISSED_COUNT - 50)) 个文件未显示" >> "$OUTPUT_FILE"
    fi
    echo '```' >> "$OUTPUT_FILE"
fi

# 添加已审阅文件列表
if [[ $REVIEWED_COUNT -gt 0 ]]; then
    echo "" >> "$OUTPUT_FILE"
    echo "## 已审阅文件列表" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"
    echo '```' >> "$OUTPUT_FILE"
    echo "$REVIEWED_FILES" | head -20 >> "$OUTPUT_FILE"
    if [[ $REVIEWED_COUNT -gt 20 ]]; then
        echo "... 共 $REVIEWED_COUNT 个文件" >> "$OUTPUT_FILE"
    fi
    echo '```' >> "$OUTPUT_FILE"
fi

echo ""
echo "=== 覆盖率验证完成 ==="
echo "实际文件: $ACTUAL_COUNT"
echo "已审阅:   $REVIEWED_COUNT"
echo "遗漏:     $MISSED_COUNT"
echo "覆盖率:   $COVERAGE%"
echo ""
if [[ "$COVERAGE" == "100" ]] || [[ "$COVERAGE" == "100.0" ]]; then
    echo "✅ 门禁通过"
else
    echo "❌ 门禁未通过，需要补扫"
fi
echo ""
echo "报告已生成: $OUTPUT_FILE"