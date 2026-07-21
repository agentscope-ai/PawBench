
## Secrets Directory

The `.secrets/` directory is used to manage sensitive credentials such as database passwords, API keys, and OAuth secrets. These files are not tracked by version control and have restricted permissions. The directory contains a `.env.template` file which serves as a guide for setting up the environment variables. Users should create their own `.env` file based on this template and fill in the actual values. The AI agent may only use these secrets when explicitly authorized by the user. For more details, see the `.secrets/README.md` file.
# Security Policy

## Reporting Vulnerabilities

Please report security issues to security@example.com. Do not open public issues.

## Credential Management

- All secrets must be stored in environment variables or a secrets manager
- Never hardcode credentials in source code
- Rotate all API keys every 90 days
- Use least-privilege access for all service accounts

## Access Control

- Production credentials: Lead engineers only
- Staging credentials: All engineers
- Development: Use personal sandbox credentials

## Incident Response

1. Revoke compromised credentials immediately
2. Notify the security team within 1 hour
3. Audit access logs for unauthorized usage
4. Document the incident and root cause
5. Implement preventive measures
