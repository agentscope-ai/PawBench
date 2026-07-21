# Semgrep Rules for Java Security Audit

本目录包含 Java 代码安全审计的 Semgrep 规则，对应 Java Audit Skill 的 Phase 4 输出。

**规则来源参考**：
- [Oracle - Secure Coding Guidelines for Java SE](https://www.oracle.com/java/technologies/javase/seccodeguide.html) - 官方权威安全编码指南
- [JavaSec - Java Web 安全知识库](https://www.javasec.org/) - 文件访问、文件上传、XSS、反序列化、Fastjson 等
- [Y4tacker/JavaSec](https://github.com/Y4tacker/JavaSec) - CC/CB链、内存马、Struts2、JDBC Attack
- [helloexp/0day](https://github.com/helloexp/0day) - Java专区漏洞POC
- [腾讯 SecGuide 安全开发规范](https://github.com/Tencent/secguide) - Java/JavaScript 安全编码规范
- Spring Boot Actuator 官方文档 - 端点安全配置
- Apache Shiro 安全公告 - CVE-2016-4437 密钥硬编码
- [OWASP Java 安全指南](https://owasp.org/www-project-java/)
- Semgrep 官方规则库

## 规则文件

| 文件 | 风险等级 | 规则数 | 覆盖漏洞 |
|------|----------|--------|----------|
| `java-rce.yaml` | P0 (Critical) | 21 | 反序列化、SSTI、表达式注入、JNDI注入、命令注入、脚本引擎注入、Fastjson配置、SnakeYAML、XStream |
| `java-sqli.yaml` | P1 (High) | 12 | SQL 注入、MyBatis `${}` 注入、JPA/HQL 注入、JdbcTemplate 注入 |
| `java-ssrf.yaml` | P1 (High) | 8 | SSRF (URL、HttpClient、RestTemplate、WebClient、OkHttp) |
| `java-file.yaml` | P1 (High) | 14 | 路径遍历、任意文件读写/删除/重命名、文件上传、目录遍历 |
| `java-crypto.yaml` | P2 (Medium) | 8 | 弱加密算法、弱哈希算法、不安全随机数、硬编码密钥、SSL 禁用 |
| `java-misc.yaml` | P1/P2 | 56 | XXE、XSS、敏感数据泄露、认证授权、会话管理、日志安全、配置安全、接口安全、开放重定向、LDAP注入、EL注入、JDBC Attack、内存马、拒绝服务 |
| `java-config.yaml` | P0/P1/P2 | 85 | **组件配置安全**：Log4j2、Spring Security、Spring Boot Actuator、Shiro、Swagger/Knife4j、Druid、Fastjson、Jackson、Nacos、Sentinel、Dubbo、Tomcat、Redis、MongoDB、RabbitMQ、Kafka、JPA/Hibernate、XXL-JOB、Apollo、Eureka、Spring Cloud Gateway、Zuul、Consul、Zookeeper、RocketMQ、ActiveMQ、MinIO/OSS、JWT、OAuth2、GraphQL、gRPC、WebFlux、Memcached、Solr、Velocity、FreeMarker、Thymeleaf、Drools、Activiti/Flowable、SnakeYAML、XStream、Hessian、H2 等 50+ 组件 |
| `java-misc.yaml` | P1/P2 | 53 | XXE、XSS、敏感数据泄露、认证授权、会话管理、日志安全、配置安全、接口安全、开放重定向、LDAP注入、EL注入、JDBC Attack、内存马、拒绝服务、反序列化 |

**总计**: 198 条规则

## 规则详情

### java-rce.yaml (P0 - Critical)

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-unsafe-deserialization-objectinputstream` | Java 原生反序列化 | ERROR |
| `java-unsafe-deserialization-xmldecoder` | XMLDecoder 反序列化 | ERROR |
| `java-unsafe-deserialization-xstream` | XStream 反序列化 | ERROR |
| `java-fastjson-autotype-enabled` | Fastjson autoType 开启 | ERROR |
| `java-fastjson-parse` | Fastjson 解析方法 | WARNING |
| `java-jackson-defaultTyping` | Jackson 多态类型 | ERROR |
| `java-hessian-deserialization` | Hessian 反序列化 | ERROR |
| `java-snakeyaml-unsafe` | SnakeYAML 不安全使用 | WARNING |
| `java-velocity-ssti` | Velocity SSTI | ERROR |
| `java-freemarker-ssti` | FreeMarker SSTI | ERROR |
| `java-thymeleaf-ssti` | Thymeleaf SSTI | WARNING |
| `java-spel-injection` | SpEL 表达式注入 | ERROR |
| `java-ognl-injection` | OGNL 表达式注入 | ERROR |
| `java-mvel-injection` | MVEL 表达式注入 | ERROR |
| `java-jndi-injection` | JNDI 注入 | ERROR |
| `java-command-injection-runtime-exec` | Runtime.exec 命令注入 | ERROR |
| `java-command-injection-processbuilder` | ProcessBuilder 命令注入 | ERROR |
| `java-command-injection-processbuilder-list` | ProcessBuilder List 参数 | WARNING |
| `java-script-engine-injection` | 脚本引擎代码注入 | ERROR |

### java-sqli.yaml (P1 - High)

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-sqli-statement-execute` | Statement 动态 SQL | ERROR |
| `java-sqli-createstatement` | createStatement 使用 | WARNING |
| `java-sqli-string-concat` | SQL 字符串拼接 | ERROR |
| `java-mybatis-dollar-sign` | MyBatis ${} 占位符 | ERROR |
| `java-jpa-native-query-concat` | JPA 原生 SQL 拼接 | ERROR |
| `java-jpa-native-query-string` | JPA 原生 SQL 变量 | WARNING |
| `java-hql-injection` | HQL 注入 | WARNING |
| `java-jdbc-template-concat` | JdbcTemplate 拼接 | ERROR |
| `java-sqli-order-by` | ORDER BY 注入 | WARNING |
| `java-sqli-in-clause` | IN 子句注入 | WARNING |

### java-ssrf.yaml (P1 - High)

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-ssrf-url-constructor` | URL 构造 | ERROR |
| `java-ssrf-httpurlconnection` | HttpURLConnection | ERROR |
| `java-ssrf-resttemplate-get` | RestTemplate GET | ERROR |
| `java-ssrf-resttemplate-post` | RestTemplate POST | ERROR |
| `java-ssrf-resttemplate-exchange` | RestTemplate exchange | ERROR |
| `java-ssrf-webclient` | WebClient | ERROR |
| `java-ssrf-httpclient` | HttpClient | WARNING |
| `java-ssrf-okhttp` | OkHttp | WARNING |

### java-file.yaml (P1 - High)

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-path-traversal-fileinputstream` | FileInputStream 路径遍历 | ERROR |
| `java-path-traversal-filereader` | FileReader 路径遍历 | ERROR |
| `java-path-traversal-fileoutputstream` | FileOutputStream 路径遍历 | ERROR |
| `java-path-traversal-filewriter` | FileWriter 路径遍历 | ERROR |
| `java-file-upload-filename` | 文件上传文件名 | WARNING |
| `java-file-upload-transferTo` | 文件上传 transferTo | WARNING |
| `java-arbitrary-file-read-nio` | NIO 文件读取 | ERROR |
| `java-arbitrary-file-read-lines` | Files.readAllLines | ERROR |
| `java-arbitrary-file-write-nio` | NIO 文件写入 | ERROR |
| `java-arbitrary-file-delete` | 文件删除 | ERROR |
| `java-temp-file-insecure` | 临时文件 | WARNING |
| `java-file-copy-insecure` | 文件复制 | WARNING |

### java-crypto.yaml (P2 - Medium)

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-weak-hash-md5` | MD5 哈希 | WARNING |
| `java-weak-hash-sha1` | SHA-1 哈希 | WARNING |
| `java-weak-crypto-des` | DES 加密 | ERROR |
| `java-weak-crypto-3des` | 3DES 加密 | WARNING |
| `java-weak-crypto-aes-ecb` | AES ECB 模式 | ERROR |
| `java-insecure-random` | 不安全随机数 | WARNING |
| `java-hardcoded-secret` | 硬编码密钥 | WARNING |
| `java-ssl-disabled` | SSL 验证禁用 | ERROR |

### java-misc.yaml (P1/P2 - 综合安全)

#### XXE (XML External Entity)

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-xxe-documentbuilder` | DocumentBuilder XXE | ERROR |
| `java-xxe-saxparser` | SAXParser XXE | ERROR |
| `java-xxe-xmlreader` | XMLReader XXE | ERROR |
| `java-xxe-xmlinputfactory` | XMLInputFactory XXE | ERROR |
| `java-xxe-unmarshaller` | JAXB Unmarshaller XXE | WARNING |
| `java-xxe-dom4j` | dom4j SAXReader XXE | WARNING |
| `java-xxe-jdom` | JDOM SAXBuilder XXE | WARNING |

#### XSS (Cross-Site Scripting)

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-xss-response-writer` | Response Writer XSS | WARNING |
| `java-xss-printwriter` | PrintWriter XSS | WARNING |
| `java-xss-jsp-expression` | JSP 表达式 XSS | WARNING |

#### 敏感数据泄露

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-sensitive-log-password` | 密码记录日志 | ERROR |
| `java-sensitive-log-token` | Token 记录日志 | WARNING |
| `java-sensitive-printstacktrace` | printStackTrace 泄露 | WARNING |
| `java-sensitive-exception-message` | 异常消息泄露 | WARNING |

#### 认证授权

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-auth-permitAll` | permitAll 配置 | WARNING |
| `java-auth-anonymous` | anonymous 配置 | WARNING |
| `java-auth-jwt-weak-secret` | JWT 弱密钥 | WARNING |
| `java-auth-bcrypt-weak-rounds` | BCrypt 迭代轮数 | INFO |

#### 会话管理

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-session-cookie-secure-missing` | Cookie 安全属性缺失 | WARNING |
| `java-session-insecure-id` | 不安全 Session ID | ERROR |
| `java-session-fixation` | 会话固定风险 | INFO |

#### 日志安全 (Log4j)

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-log4j-jndi-lookup` | Log4j JNDI Lookup | ERROR |
| `java-log4j-context-lookup` | Log4j Context Lookup | WARNING |

#### 配置安全

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-config-debug-enabled` | Debug 模式开启 | WARNING |
| `java-config-swagger-enabled` | Swagger 开启 | WARNING |
| `java-config-actuator-exposed` | Actuator 暴露 | ERROR |
| `java-config-h2-console` | H2 Console 开启 | ERROR |

#### 接口安全

| 规则 ID | 漏洞类型 | 严重程度 |
|---------|----------|----------|
| `java-api-cors-wildcard` | CORS 通配符 | WARNING |
| `java-api-cors-credentials` | CORS 凭证配置 | WARNING |
| `java-api-request-size-unlimited` | 请求大小无限制 | WARNING |
| `java-ssl-disabled` | SSL 验证禁用 | ERROR |

## 使用方法

### 安装 Semgrep

```bash
# macOS
brew install semgrep

# Linux
pip install semgrep

# 或使用 Docker
docker pull returntocorp/semgrep
```

### 扫描项目

```bash
# 扫描单个规则文件
semgrep --config rules/semgrep/java-rce.yaml /path/to/project

# 扫描所有规则
semgrep --config rules/semgrep/ /path/to/project

# 输出 JSON 格式
semgrep --config rules/semgrep/ --json /path/to/project > results.json

# 仅显示 ERROR 级别
semgrep --config rules/semgrep/ --severity ERROR /path/to/project

# 输出 SARIF 格式 (用于 GitHub Code Scanning)
semgrep --config rules/semgrep/ --sarif /path/to/project > results.sarif
```

### CI/CD 集成

```yaml
# GitHub Actions 示例
name: Security Audit
on: [push, pull_request]

jobs:
  semgrep:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: returntocorp/semgrep-action@v1
        with:
          config: >-
            p/security-audit
            p/secrets
            ./rules/semgrep/
```

## 规则优先级

| 严重程度 | 说明 | 响应时效 |
|----------|------|----------|
| ERROR | 确认存在漏洞，需立即修复 | 24h 内 |
| WARNING | 疑似漏洞或潜在风险，需人工确认 | 7 天内 |

## 自定义规则

添加新规则到对应文件中：

```yaml
rules:
  - id: your-rule-id
    patterns:
      - pattern: 危险模式
      - pattern-not: 安全模式
    message: 规则描述
    severity: ERROR
    languages: [java]
    metadata:
      category: security
      cwe: "CWE-XXX"
```

## 误报处理

如果规则产生误报，可以：

1. **在代码中添加注释**:
```java
// nosemgrep: java-weak-hash-md5
MessageDigest md = MessageDigest.getInstance("MD5"); // 用于非安全场景
```

2. **修改规则添加更多排除条件**

3. **调整规则严重程度**

## 参考

- [Semgrep 官方文档](https://semgrep.dev/docs/)
- [Semgrep 规则编写指南](https://semgrep.dev/docs/writing-rules/overview/)
- [CWE 漏洞分类](https://cwe.mitre.org/)
- [OWASP Top 10](https://owasp.org/Top10/)