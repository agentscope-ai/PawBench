#!/bin/bash
# Tier 分类脚本
# 根据规则对 Java 文件进行 T1/T2/T3/SKIP 分类
#
# Usage: ./tier-classify.sh <target_dir> [output_file]
#
# Arguments:
#   target_dir   - 项目根目录 (default: .)
#   output_file  - 输出文件路径 (default: ./audit-output/tier-classification.md)
#
# Requirements:
#   - bash, find, grep, wc
#   - python3 OR bc (for EALOC calculation)

set -e

# 显示帮助信息
show_help() {
    echo "Usage: $0 [target_dir] [output_file]"
    echo ""
    echo "Arguments:"
    echo "  target_dir   - 项目根目录 (default: .)"
    echo "  output_file  - 输出文件路径 (default: ./audit-output/tier-classification.md)"
    echo ""
    echo "Example:"
    echo "  $0 /path/to/java/project"
    echo "  $0 /path/to/project ./output/tier.md"
    exit 0
}

# 检查参数
if [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    show_help
fi

TARGET_DIR="${1:-.}"
OUTPUT_FILE="${2:-./audit-output/tier-classification.md}"

# 创建输出目录
mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "=== Tier 分类开始 ==="
echo "目标目录: $TARGET_DIR"

# 初始化统计变量
T1_COUNT=0
T2_COUNT=0
T3_COUNT=0
SKIP_COUNT=0
T1_FILES=""
T2_FILES=""
T3_FILES=""
SKIP_FILES=""

# 遍历所有 Java 文件
while IFS= read -r file; do
    # 跳过 test 目录
    if [[ "$file" == *"/test/"* ]] || [[ "$file" == *"/Test.java" ]]; then
        continue
    fi
    
    # Rule 0: 第三方库源码 → SKIP
    if [[ "$file" == *"/target/"* ]] || [[ "$file" == *"/node_modules/"* ]]; then
        ((SKIP_COUNT++))
        SKIP_FILES="$SKIP_FILES$file\n"
        continue
    fi
    
    # 读取文件内容
    content=$(cat "$file" 2>/dev/null || echo "")
    
    # Rule 2: Controller/Filter → T1
    if echo "$content" | grep -qE "@Controller|@RestController|@WebServlet|extends Filter|implements Filter"; then
        ((T1_COUNT++))
        T1_FILES="$T1_FILES$file\n"
        continue
    fi
    
    # Rule 3: Service/Repository/Mapper → T2
    if echo "$content" | grep -qE "@Service|@Repository|@Mapper|@Dao"; then
        ((T2_COUNT++))
        T2_FILES="$T2_FILES$file\n"
        continue
    fi
    
    # Rule 4: Util/Helper/Handler → T2
    filename=$(basename "$file")
    if [[ "$filename" == *"Util"* ]] || [[ "$filename" == *"Helper"* ]] || [[ "$filename" == *"Handler"* ]]; then
        ((T2_COUNT++))
        T2_FILES="$T2_FILES$file\n"
        continue
    fi
    
    # Rule 6: Entity/VO/DTO → T3
    if echo "$content" | grep -qE "@Entity|@Table|@Data|class.*VO|class.*DTO|class.*Entity"; then
        ((T3_COUNT++))
        T3_FILES="$T3_FILES$file\n"
        continue
    fi
    
    # Rule 7: 未匹配 → T2 (保守兜底)
    ((T2_COUNT++))
    T2_FILES="$T2_FILES$file\n"
    
done < <(find "$TARGET_DIR" -name "*.java" -type f 2>/dev/null)

# 计算行数
T1_LOC=$(echo -e "$T1_FILES" | grep -v "^$" | xargs wc -l 2>/dev/null | tail -1 | awk '{print $1}' || echo "0")
T2_LOC=$(echo -e "$T2_FILES" | grep -v "^$" | xargs wc -l 2>/dev/null | tail -1 | awk '{print $1}' || echo "0")
T3_LOC=$(echo -e "$T3_FILES" | grep -v "^$" | xargs wc -l 2>/dev/null | tail -1 | awk '{print $1}' || echo "0")

# 计算 EALOC
# EALOC = T1_LOC * 1.0 + T2_LOC * 0.5 + T3_LOC * 0.1
EALOC=$(echo "$T1_LOC * 1.0 + $T2_LOC * 0.5 + $T3_LOC * 0.1" | bc 2>/dev/null || python3 -c "print(int($T1_LOC * 1.0 + $T2_LOC * 0.5 + $T3_LOC * 0.1))" 2>/dev/null || echo "N/A")

# 计算所需 Agent 数量
if [[ "$EALOC" != "N/A" ]]; then
    AGENT_COUNT=$(python3 -c "import math; print(math.ceil($EALOC / 15000))" 2>/dev/null || echo "N/A")
else
    AGENT_COUNT="N/A"
fi

# 生成报告
cat > "$OUTPUT_FILE" << EOF
# Tier 分类结果

## 统计摘要

| Tier | 文件数 | LOC | 权重 | EALOC |
|------|--------|-----|------|-------|
| T1 (Controller/Filter) | $T1_COUNT | $T1_LOC | 1.0 | $T1_LOC |
| T2 (Service/DAO/Util) | $T2_COUNT | $T2_LOC | 0.5 | $(python3 -c "print(int($T2_LOC * 0.5))" 2>/dev/null || echo "N/A") |
| T3 (Entity/VO/DTO) | $T3_COUNT | $T3_LOC | 0.1 | $(python3 -c "print(int($T3_LOC * 0.1))" 2>/dev/null || echo "N/A") |
| SKIP | $SKIP_COUNT | - | - | - |

**总 EALOC**: $EALOC  
**所需 Agent 数量**: $AGENT_COUNT (按 15,000 EALOC/Agent 预算)

## Tier 分类规则

| 规则 | 条件 | Tier |
|------|------|------|
| Rule 0 | 第三方库源码 | SKIP |
| Rule 1 | Layer 1 有 P0/P1 候选项 | T1 (动态提升) |
| Rule 2 | @Controller/@RestController/@WebServlet/Filter | T1 |
| Rule 3 | @Service/@Repository/@Mapper | T2 |
| Rule 4 | 类名含 Util/Helper/Handler | T2 |
| Rule 5 | .properties/.yml/security.xml | T2 |
| Rule 6 | @Entity/@Table/@Data | T3 |
| Rule 7 | 未匹配任何规则 | T2 (保守兜底) |

EOF

echo ""
echo "=== Tier 分类完成 ==="
echo "T1 文件数: $T1_COUNT"
echo "T2 文件数: $T2_COUNT"
echo "T3 文件数: $T3_COUNT"
echo "SKIP 文件数: $SKIP_COUNT"
echo "总 EALOC: $EALOC"
echo "所需 Agent: $AGENT_COUNT"
echo ""
echo "报告已生成: $OUTPUT_FILE"