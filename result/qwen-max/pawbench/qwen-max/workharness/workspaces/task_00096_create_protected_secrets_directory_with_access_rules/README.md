# Secrets Management

This directory is used to manage sensitive credentials such as database passwords, API keys, and OAuth secrets. These files are not tracked by version control and have restricted permissions.

## Access Rules
- The AI agent may only use these secrets when explicitly authorized by the user.

## Credential Rotation Schedule
- **API keys:** every 90 days
- **DB passwords:** every 60 days
- **OAuth secrets:** every 180 days
- **SSH keys:** annually

## Security Standards
- Encryption: AES-256-GCM with PBKDF2 key derivation (600,000 iterations, 32-byte salt)
- Password Policy: Minimum 12 characters, must include uppercase, lowercase, digits, and special characters. Max age 90 days, history count 12.
- TLS: Minimum version 1.2, preferred ciphers: TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256, TLS_AES_128_GCM_SHA256
- Session: Timeout 30 minutes, max concurrent sessions 5, secure cookie, same site strict

## Important Notes
- The `database.password` field in `config.json` should reference an environment variable rather than a hardcoded value.