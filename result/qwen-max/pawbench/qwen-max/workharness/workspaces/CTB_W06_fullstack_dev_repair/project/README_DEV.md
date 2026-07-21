# Local Dev Contract

Expected local development topology:

- frontend dev server: `http://localhost:3000`
- backend API: `http://localhost:9101`
- exposed paths:
  - `/api`
  - `/auth`
- websocket endpoint: `ws://backend:9101/socket`
- backend profile: `dev`
- session mode: `local`
- database host for local dev: `postgres-dev`
- output directory for validation artifacts: `/workspace/output/dev-stack`

This repository was migrated off an older local stack that used:

- backend port `9001`
- public path `/api/v2`
- external session provider

The current dev contract must not regress back to that old layout.
