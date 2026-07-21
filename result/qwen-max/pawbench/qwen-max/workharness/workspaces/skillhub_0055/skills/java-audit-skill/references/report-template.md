# 标准化漏洞报告模板

每个漏洞报告必须包含三个核心部分：**描述**、**漏洞详情**、**修复建议**。

---

## 报告格式规范

```markdown
### [漏洞标题]

#### 描述

[漏洞概述，200字以内，说明漏洞类型、成因、核心风险点]

#### 漏洞详情

**代码位置**：

[完整文件路径]:[行号]

**代码片段**：

```java
// 带行号的代码片段（必须来自实际 Read 输出，不得编造）
```

**分析**：

[详细的漏洞分析]
1. 说明代码为什么存在安全问题
2. 分析攻击者如何利用该漏洞
3. 说明漏洞的触发条件和影响范围
4. 必要时给出恶意输入示例

#### 修复建议

[贴合问题代码的具体修复方案，分点说明并给出可执行的修复代码]

**修复代码示例**：

```java
// 具体的修复代码
```
```

---

## 完整示例

### 示例1: XXE 漏洞（文件上传接口）

#### 描述

在文件导入后，解析excel时使用EasyExcel对数据进行解析，没有配置禁用 XML 外部实体的选项，并且 MultipartFile file 来自用户输入导致存在XXE风险。该文件导入接口没有文件类型校验（只检查是否为空），没有文件大小限制，存在任意文件导入，以及大文件导入时所触发的 DDoS 问题。

#### 漏洞详情

**代码位置**：

```
\qd-train-api-master\qd-train-api-web\src\main\java\com\cmft\b2bjoy\train\api\web\controller\ExcelController.java
第35行进入 ExcelUtil.importExcel 方法进行文件数据解析
```

```
\qd-train-api-master\qd-train-api-service\src\main\java\com\cmft\b2bjoy\train\api\service\util\ExcelUtil.java
第79行
```

**代码片段**：

```java
// ExcelController.java
@PostMapping("/import")
public Result importExcel(@RequestParam("file") MultipartFile file) {
    if (file == null || file.isEmpty()) {
        throw new RuntimeException("没有文件或者文件内容为空！");
    }
    // 第35行：直接传入 ExcelUtil.importExcel 进行解析
    List<GeographicalInformationDto> dataList = ExcelUtil.importExcel(file);
    // ...
}

// ExcelUtil.java 第79行
public static List<GeographicalInformationDto> importExcel(MultipartFile file) {
    // ...
    ExcelListener<GeographicalInformationDto> listener = new ExcelListener<>();
    // 没有配置禁用 XML 外部实体的选项
    EasyExcel.read(ipt, GeographicalInformationDto.class, listener).sheet().doRead();
    // ...
}
```

**分析**：

1. **XXE 风险成因**：EasyExcel/POI 在底层解析 Excel（特别是 .xlsx 格式）时，会用到 XML 解析器。代码中没有配置禁用 XML 外部实体的选项，MultipartFile file 来自用户输入，没有进行充分的安全校验。

2. **文件本质**：.xlsx 文件本质上是 ZIP 压缩的 XML 文件集合。如果攻击者构造恶意的 Excel 文件，在 XML 中定义外部实体，可能导致：
   - 读取本地文件（如 `/etc/passwd`）
   - 发起 SSRF 请求
   - 拒绝服务攻击

3. **恶意输入示例**：
```xml
<!DOCTYPE data [
    <!ENTITY secret SYSTEM "file:///etc/passwd">
]>
<data>&secret;</data>
```

4. **其他风险**：
   - 没有文件类型校验（只检查是否为空）
   - 没有文件大小限制，可能触发 DDoS

#### 修复建议

**1. 限制上传类型和文件大小**：

```java
// 进行文件类型限制
private static final Set<String> ALLOWED_TYPES = Set.of(
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
);
private static final long MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

public static List<GeographicalInformationDto> importExcel(MultipartFile file) {
    // 校验文件类型
    String contentType = file.getContentType();
    if (!ALLOWED_TYPES.contains(contentType)) {
        throw new RuntimeException("不支持的文件类型");
    }
    
    // 校验文件大小
    if (file.getSize() > MAX_FILE_SIZE) {
        throw new RuntimeException("文件大小超过限制");
    }
    
    // 校验文件扩展名
    String filename = file.getOriginalFilename();
    if (filename == null || !filename.toLowerCase().endsWith(".xlsx")) {
        throw new RuntimeException("文件扩展名不合法");
    }
    // ...
}
```

**2. 禁用 XML 外部实体解析**：

```java
public static List<GeographicalInformationDto> importExcel(MultipartFile file) {
    if (file == null || file.isEmpty()) {
        throw new RuntimeException("没有文件或者文件内容为空！");
    }
    List<GeographicalInformationDto> dataList = null;
    BufferedInputStream ipt = null;
    try {
        InputStream is = file.getInputStream();
        ipt = new BufferedInputStream(is);

        ExcelListener<GeographicalInformationDto> listener = new ExcelListener<>();
        
        // 配置安全的 XML 解析器
        SAXParserFactory factory = SAXParserFactory.newInstance();
        factory.setNamespaceAware(true);
        factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
        factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
        factory.setXIncludeAware(false);
        factory.setValidating(false);
        
        XMLReader xmlReader = factory.newSAXParser().getXMLReader();
        
        ExcelReaderBuilder builder = EasyExcel.read(ipt, GeographicalInformationDto.class, listener);
        builder.xmlReader(xmlReader);
        builder.sheet().doRead();
        
        dataList = listener.getDataList();
    } catch (Exception e) {
        log.error(String.valueOf(e));
        throw new RuntimeException("数据导入失败！" + e);
    } finally {
        try {
            if (ipt != null) {
                ipt.close();
            }
        } catch (Exception e) {
            log.error("关闭输入流失败：" + e.getMessage());
        }
    }
    return dataList;
}
```

---

### 示例2: Velocity SSTI 导致远程代码执行

#### 描述

TemplateController.renderTemplate() 方法接收用户输入的 template 参数，直接传入 Velocity.evaluate() 进行模板渲染，未配置 SecureUberspector 限制反射调用。攻击者可构造恶意 Velocity 模板代码，通过反射调用 Runtime.exec() 执行任意系统命令，导致服务器被完全控制。

#### 漏洞详情

**代码位置**：

```
TemplateController.java:45-52
```

**代码片段**：

```java
@PostMapping("/render")
public String renderTemplate(@RequestParam String template) {
    VelocityContext context = new VelocityContext();
    StringWriter writer = new StringWriter();
    // 危险：用户输入直接作为模板内容
    Velocity.evaluate(context, writer, "userTemplate", template);
    return writer.toString();
}
```

**分析**：

1. **漏洞成因**：用户输入的 `template` 参数未经任何过滤，直接作为模板内容传入 `Velocity.evaluate()`。Velocity 默认允许通过反射调用任意 Java 类和方法。

2. **攻击方式**：攻击者可构造包含恶意代码的 Velocity 模板：
```velocity
#set($x='')
#set($rt=$x.class.forName('java.lang.Runtime'))
#set($ex=$rt.getRuntime().exec('whoami'))
```

3. **影响范围**：
   - 执行任意系统命令
   - 读取服务器敏感文件
   - 植入后门实现持久化控制
   - 横向移动至内网其他系统

#### 修复建议

**1. 立即修复 - 配置 SecureUberspector**：

```java
VelocityEngine ve = new VelocityEngine();
ve.setProperty("runtime.introspector.uberspect", 
    "org.apache.velocity.util.introspection.SecureUberspector");
ve.init();

VelocityContext context = new VelocityContext();
StringWriter writer = new StringWriter();
ve.evaluate(context, writer, "userTemplate", template);
```

**2. 架构优化 - 使用预定义模板**：

```java
// 安全做法：不将用户输入直接作为模板内容
VelocityEngine ve = new VelocityEngine();
ve.init();

// 使用预定义模板
Template template = ve.getTemplate("templates/safe-template.vm");
VelocityContext context = new VelocityContext();
context.put("userContent", sanitizedInput);  // 用户内容作为参数注入

StringWriter writer = new StringWriter();
template.merge(context, writer);
return writer.toString();
```

**3. 纵深防御**：

```java
// 添加输入白名单校验
private static final Pattern SAFE_CONTENT = Pattern.compile("^[a-zA-Z0-9\\s\\.,!?]+$");

public String renderTemplate(@RequestParam String template) {
    // 白名单校验
    if (!SAFE_CONTENT.matcher(template).matches()) {
        throw new SecurityException("非法输入");
    }
    // ...
}
```

---

## 报告生成检查清单

每个漏洞报告提交前，确认以下要求：

### 描述部分
- [ ] 字数控制在200字以内
- [ ] 清晰说明漏洞类型（如XXE、SSTI、SQL注入等）
- [ ] 说明漏洞成因和核心风险点

### 漏洞详情部分
- [ ] 代码位置准确（完整路径 + 行号）
- [ ] 代码片段来自实际 Read 输出，带行号
- [ ] 分析内容详细，包括：
  - [ ] 为什么存在安全问题
  - [ ] 攻击者如何利用
  - [ ] 恶意输入示例（如适用）
  - [ ] 影响范围

### 修复建议部分
- [ ] 针对具体问题给出修复方案
- [ ] 提供可直接使用的修复代码
- [ ] 如有多处需要修复，分点说明

---

## 状态定义

| 状态 | 定义 | 要求 |
|------|------|------|
| **CONFIRMED** | 已验证可利用 | PoC 可执行，调用链完整，影响明确 |
| **HYPOTHESIS** | 疑似漏洞，需人工验证 | 发现可疑模式但无法完全确认 |

**关键原则**：宁可标记为 HYPOTHESIS 让人工验证，也不要把不确定的发现标记为 CONFIRMED 污染报告可信度。

---

## 多漏洞报告格式

当一个项目存在多个漏洞时，按以下格式组织：

```markdown
# [项目名称] 安全审计报告

**审计日期**：YYYY-MM-DD  
**审计人员**：[审计员名称]  
**项目规模**：[代码行数/文件数]  
**风险统计**：Critical X个 / High X个 / Medium X个 / Low X个

---

## 漏洞清单

| 编号 | 漏洞标题 | 严重程度 | 状态 | 文件位置 |
|------|---------|---------|------|---------|
| VULN-001 | XXE文件上传漏洞 | Critical | CONFIRMED | ExcelController.java:35 |
| VULN-002 | Velocity SSTI 远程代码执行 | Critical | CONFIRMED | TemplateController.java:45 |

---

## 详细漏洞报告

### VULN-001: XXE文件上传漏洞

[按上述三段式格式填写]

### VULN-002: Velocity SSTI 远程代码执行

[按上述三段式格式填写]

---

## 修复优先级建议

1. **立即修复**：[Critical 级别漏洞列表]
2. **本周修复**：[High 级别漏洞列表]
3. **计划修复**：[Medium/Low 级别漏洞列表]
```