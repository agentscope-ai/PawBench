#!/bin/bash
# Layer 1: 全量预扫描脚本
# 使用 ripgrep 扫描危险模式，按 P0-P3 优先级标记
#
# Usage: ./layer1-scan.sh <target_dir> [output_dir]
#
# Arguments:
#   target_dir   - 项目根目录 (default: .)
#   output_dir   - 输出目录 (default: ./audit-output)
#
# Requirements:
#   - bash, grep, find
#   - Note: Uses grep -rn with --include, works on Linux/macOS

set -e

# 显示帮助信息
show_help() {
    echo "Usage: $0 [target_dir] [output_dir]"
    echo ""
    echo "Arguments:"
    echo "  target_dir   - 项目根目录 (default: .)"
    echo "  output_dir   - 输出目录 (default: ./audit-output)"
    echo ""
    echo "Output files:"
    echo "  - p0-critical.md  - RCE/反序列化模式"
    echo "  - p1-high.md      - SQL注入/SSRF/文件操作"
    echo "  - p2-medium.md    - 认证/授权/加密"
    echo "  - p3-low.md       - 信息泄露/调试接口"
    echo ""
    echo "Example:"
    echo "  $0 /path/to/java/project"
    echo "  $0 /path/to/project ./scan-results"
    exit 0
}

# 检查参数
if [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    show_help
fi

TARGET_DIR="${1:-.}"
OUTPUT_DIR="${2:-./audit-output}"
P0_FILE="$OUTPUT_DIR/p0-critical.md"
P1_FILE="$OUTPUT_DIR/p1-high.md"
P2_FILE="$OUTPUT_DIR/p2-medium.md"
P3_FILE="$OUTPUT_DIR/p3-low.md"

# 创建输出目录
mkdir -p "$OUTPUT_DIR"

echo "=== Layer 1 预扫描开始 ==="
echo "目标目录: $TARGET_DIR"
echo "输出目录: $OUTPUT_DIR"

# ============================================
# P0 级 - RCE/反序列化（Critical）
# ============================================
echo "[P0] 扫描 RCE/反序列化模式..."

cat > "$P0_FILE" << 'EOF'
# P0 级危险模式 - RCE/反序列化

## 发现记录

EOF

# 反序列化
echo "### 反序列化" >> "$P0_FILE"
grep -rn "ObjectInputStream\|XMLDecoder\|XStream" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

# Fastjson
echo "### Fastjson" >> "$P0_FILE"
grep -rn "JSON\.parseObject\|JSON\.parse\|@type" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

# Jackson
echo "### Jackson" >> "$P0_FILE"
grep -rn "enableDefaultTyping\|activateDefaultTyping" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

# Hessian
echo "### Hessian" >> "$P0_FILE"
grep -rn "HessianInput\|Hessian2Input" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

# SSTI
echo "### SSTI - Velocity" >> "$P0_FILE"
grep -rn "Velocity\.evaluate\|VelocityEngine\|mergeTemplate" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

echo "### SSTI - FreeMarker" >> "$P0_FILE"
grep -rn "freemarker\.template\|Template\.process\|FreeMarkerConfigurer" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

echo "### SSTI - Thymeleaf" >> "$P0_FILE"
grep -rn "SpringTemplateEngine\|TemplateEngine\.process" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

# 表达式注入
echo "### SpEL 注入" >> "$P0_FILE"
grep -rn "SpelExpressionParser\|parseExpression\|evaluateExpression" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

echo "### OGNL 注入" >> "$P0_FILE"
grep -rn "OgnlUtil\|Ognl\.getValue\|ActionContext" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

# JNDI 注入
echo "### JNDI 注入" >> "$P0_FILE"
grep -rn "InitialContext\.lookup\|JdbcRowSetImpl\|setDataSourceName" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

# 命令执行
echo "### 命令执行" >> "$P0_FILE"
grep -rn "Runtime\.getRuntime\(\)\.exec\|ProcessBuilder" --include="*.java" "$TARGET_DIR" >> "$P0_FILE" 2>/dev/null || echo "未发现" >> "$P0_FILE"
echo "" >> "$P0_FILE"

# ============================================
# P1 级 - SQL 注入/SSRF/文件操作（High）
# ============================================
echo "[P1] 扫描 SQL 注入/SSRF/文件操作模式..."

cat > "$P1_FILE" << 'EOF'
# P1 级危险模式 - SQL 注入/SSRF/文件操作

## 发现记录

EOF

# SQL 注入
echo "### SQL 注入风险" >> "$P1_FILE"
grep -rn "Statement\|createStatement\|executeQuery\|executeUpdate" --include="*.java" "$TARGET_DIR" >> "$P1_FILE" 2>/dev/null || echo "未发现" >> "$P1_FILE"
echo "" >> "$P1_FILE"

echo "### MyBatis \${} 注入" >> "$P1_FILE"
grep -rn '\$\{' --include="*.xml" "$TARGET_DIR" >> "$P1_FILE" 2>/dev/null || echo "未发现" >> "$P1_FILE"
echo "" >> "$P1_FILE"

# SSRF
echo "### SSRF 风险" >> "$P1_FILE"
grep -rn "URL(\|HttpURLConnection\|HttpClient\|RestTemplate\|WebClient" --include="*.java" "$TARGET_DIR" >> "$P1_FILE" 2>/dev/null || echo "未发现" >> "$P1_FILE"
echo "" >> "$P1_FILE"

# 文件操作
echo "### 文件读取" >> "$P1_FILE"
grep -rn "FileInputStream\|FileReader\|Files\.read\|readString" --include="*.java" "$TARGET_DIR" >> "$P1_FILE" 2>/dev/null || echo "未发现" >> "$P1_FILE"
echo "" >> "$P1_FILE"

echo "### 文件写入" >> "$P1_FILE"
grep -rn "FileOutputStream\|FileWriter\|Files\.write\|writeString" --include="*.java" "$TARGET_DIR" >> "$P1_FILE" 2>/dev/null || echo "未发现" >> "$P1_FILE"
echo "" >> "$P1_FILE"

echo "### 文件上传" >> "$P1_FILE"
grep -rn "getOriginalFilename\|transferTo\|MultipartFile" --include="*.java" "$TARGET_DIR" >> "$P1_FILE" 2>/dev/null || echo "未发现" >> "$P1_FILE"
echo "" >> "$P1_FILE"

# ============================================
# P2 级 - 认证/授权/加密（Medium）
# ============================================
echo "[P2] 扫描认证/授权/加密模式..."

cat > "$P2_FILE" << 'EOF'
# P2 级危险模式 - 认证/授权/加密

## 发现记录

EOF

# 认证授权
echo "### 认证注解" >> "$P2_FILE"
grep -rn "@PreAuthorize\|@Secured\|@RolesAllowed\|hasRole\|hasAuthority" --include="*.java" "$TARGET_DIR" >> "$P2_FILE" 2>/dev/null || echo "未发现" >> "$P2_FILE"
echo "" >> "$P2_FILE"

echo "### Spring Security 配置" >> "$P2_FILE"
grep -rn "permitAll\|anonymous\|authenticated" --include="*.java" "$TARGET_DIR" >> "$P2_FILE" 2>/dev/null || echo "未发现" >> "$P2_FILE"
echo "" >> "$P2_FILE"

# 加密
echo "### 加密相关" >> "$P2_FILE"
grep -rn "MessageDigest\|Cipher\|SecretKey\|PasswordEncoder" --include="*.java" "$TARGET_DIR" >> "$P2_FILE" 2>/dev/null || echo "未发现" >> "$P2_FILE"
echo "" >> "$P2_FILE"

# 配置文件
echo "### 配置文件" >> "$P2_FILE"
find "$TARGET_DIR" -name "*.properties" -o -name "*.yml" -o -name "*.yaml" 2>/dev/null | head -20 >> "$P2_FILE"
echo "" >> "$P2_FILE"

# ============================================
# P3 级 - 信息泄露/调试接口（Low）
# ============================================
echo "[P3] 扫描信息泄露/调试接口..."

cat > "$P3_FILE" << 'EOF'
# P3 级危险模式 - 信息泄露/调试接口

## 发现记录

EOF

# 调试接口
echo "### Actuator 端点" >> "$P3_FILE"
grep -rn "actuator\|/health\|/env\|/metrics" --include="*.java" --include="*.yml" --include="*.properties" "$TARGET_DIR" >> "$P3_FILE" 2>/dev/null || echo "未发现" >> "$P3_FILE"
echo "" >> "$P3_FILE"

# API 文档
echo "### API 文档暴露" >> "$P3_FILE"
grep -rn "swagger\|/api-docs\|OpenAPI" --include="*.java" --include="*.yml" --include="*.properties" "$TARGET_DIR" >> "$P3_FILE" 2>/dev/null || echo "未发现" >> "$P3_FILE"
echo "" >> "$P3_FILE"

# 敏感信息
echo "### 敏感信息（密码/密钥）" >> "$P3_FILE"
grep -rn "password\|secret\|apikey\|token" --include="*.properties" --include="*.yml" "$TARGET_DIR" | grep -v "^\s*#" >> "$P3_FILE" 2>/dev/null || echo "未发现" >> "$P3_FILE"
echo "" >> "$P3_FILE"

echo ""
echo "=== Layer 1 预扫描完成 ==="
echo "P0 (Critical): $P0_FILE"
echo "P1 (High):     $P1_FILE"
echo "P2 (Medium):   $P2_FILE"
echo "P3 (Low):      $P3_FILE"