# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |

## Reporting a Vulnerability

We take the security of Java Audit Skill seriously. If you discover a security vulnerability, please follow these steps:

### 1. Do NOT create a public issue

Security vulnerabilities should be reported privately to avoid potential exploitation.

### 2. Report via Email

Send a detailed report to: **aurora1219@139.com**

Please include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### 3. Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Fix Timeline**: Depends on severity
  - Critical: 7 days
  - High: 14 days
  - Medium: 30 days

### 4. Disclosure Policy

- We follow responsible disclosure
- Please do not disclose the vulnerability publicly until a fix is released
- We will credit you in the security advisory (unless you prefer to remain anonymous)

## Security Best Practices

When using Java Audit Skill:

1. **Review audit reports carefully** - AI-generated reports may contain false positives or miss some vulnerabilities
2. **Do not share audit reports publicly** if they contain sensitive information about your codebase
3. **Keep dependencies updated** - Regularly update Python and any optional dependencies
4. **Run in isolated environments** when auditing untrusted code

## Scope

This security policy covers:

- Vulnerabilities in the Java Audit Skill codebase
- Security issues in the audit methodology that could lead to false negatives
- Bugs that could cause security problems for users

Out of scope:

- Vulnerabilities in projects being audited (that's what the tool is for!)
- Issues in third-party dependencies (report to the respective maintainers)

## Recognition

We appreciate security researchers who help keep our project safe. Contributors who report valid security vulnerabilities will be:

- Listed in our security acknowledgments (with permission)
- Credited in the fix commit and release notes

---

Thank you for helping keep Java Audit Skill secure! 🛡️