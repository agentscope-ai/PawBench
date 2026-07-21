# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-19

### Added

- **6-Phase Audit Pipeline**: Complete workflow from code metrics to standardized reports
- **3-Layer Audit Architecture**: Pre-scan + Dual-track audit + Semantic verification
- **Coverage Gate**: Enforces 100% code coverage before proceeding to verification
- **DKTSS Scoring System**: Practical vulnerability priority scoring (better than CVSS for real-world impact)
- **Anti-Hallucination Mechanism**: 5 iron rules ensuring report credibility
- **Cross-platform Python Scripts**: Unified entry point for Windows/Linux/macOS
- **Semgrep Rules**: 198 rules covering 55+ vulnerability types
- **Bilingual Documentation**: Full English and Chinese support

### Documentation

- `SKILL.md` - Complete audit protocol specification
- `references/dktss-scoring.md` - DKTSS scoring system details
- `references/vulnerability-conditions.md` - Vulnerability confirmation criteria
- `references/security-checklist.md` - Comprehensive security audit checklist
- `references/report-template.md` - Standardized vulnerability report template

### Vulnerability Coverage

#### P0 (Critical)
- Deserialization: Fastjson, Jackson, XStream, Hessian, SnakeYAML, Java native
- SSTI: Velocity, FreeMarker, Thymeleaf, Pebble
- Expression Injection: SpEL, OGNL, MVEL
- JNDI Injection
- Command Execution

#### P1 (High)
- SQL Injection (MyBatis `${}`, JDBC, JPA/HQL)
- SSRF
- Path Traversal / File Operations
- XXE

#### P2 (Medium)
- Authentication/Authorization issues
- Cryptographic weaknesses
- Information Disclosure
- Configuration vulnerabilities

---

## Future Roadmap

- [ ] RAG integration for large codebase handling
- [ ] LSP support for semantic call chain tracing
- [ ] CI/CD pipeline templates
- [ ] Web dashboard for audit management
- [ ] Multi-language support (Python, Go, PHP)