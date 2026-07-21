#!/usr/bin/env python3
"""Check a simulated local full-stack dev environment without Docker."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _resolve_workspace() -> Path:
    """Locate the workspace root.

    Order of resolution: explicit ``$WORKSPACE`` env var, the script's
    grandparent (``project/scripts/.. / ..``), or the current working
    directory.
    """
    if os.environ.get("WORKSPACE"):
        return Path(os.environ["WORKSPACE"]).resolve()
    here = Path(__file__).resolve()
    candidate = here.parent.parent.parent
    if (candidate / "project" / "config" / "backend.env").is_file():
        return candidate
    return Path.cwd()


WORKSPACE = _resolve_workspace()
ROOT = WORKSPACE / "project"
BACKEND_ENV_PATH = ROOT / "config/backend.env"
FRONTEND_ENV_PATH = ROOT / "frontend/.env.local"
PROXY_PATH = ROOT / "proxy/dev_proxy.json"
EXPECTED_STACK_OUTPUT_DIR = WORKSPACE / "output" / "dev-stack"


def load_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Bad env line: {line}")
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main() -> None:
    backend = load_env(BACKEND_ENV_PATH)
    frontend = load_env(FRONTEND_ENV_PATH)
    proxy = json.loads(PROXY_PATH.read_text())
    errors: list[str] = []

    require(backend.get("APP_MODE") == "dev", "APP_MODE must be dev", errors)
    require(backend.get("API_PORT") == "9101", "API_PORT must be 9101", errors)
    require(backend.get("DB_HOST") == "postgres-dev", "DB_HOST must be postgres-dev", errors)
    require(backend.get("PUBLIC_API_PATH") == "/api", "PUBLIC_API_PATH must be /api", errors)
    require(backend.get("SESSION_MODE") == "local", "SESSION_MODE must be local", errors)
    require(
        backend.get("STACK_OUTPUT_DIR") == "/workspace/output/dev-stack",
        "STACK_OUTPUT_DIR mismatch",
        errors,
    )

    require(
        frontend.get("VITE_API_ORIGIN") == "http://localhost:9101",
        "frontend api origin mismatch",
        errors,
    )
    require(frontend.get("VITE_API_PATH") == "/api", "frontend api path mismatch", errors)
    require(frontend.get("VITE_DEV_PROXY_PORT") == "3000", "frontend proxy port mismatch", errors)
    require(frontend.get("VITE_LOGIN_MODE") == "local", "frontend login mode mismatch", errors)

    require(proxy.get("listen") == 3000, "proxy listen must be 3000", errors)
    routes = proxy.get("routes") or {}
    require(routes.get("/api") == "http://backend:9101/api", "proxy /api route mismatch", errors)
    require(routes.get("/auth") == "http://backend:9101/auth", "proxy /auth route mismatch", errors)
    require(proxy.get("websocket") == "ws://backend:9101/socket", "proxy websocket mismatch", errors)

    if errors:
        print("DEV_STACK_FAILED")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    output_dir = EXPECTED_STACK_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "dev_stack_status.json"
    status_path.write_text(
        json.dumps(
            {
                "backend_mode": "dev",
                "backend_port": 9101,
                "db_host": "postgres-dev",
                "frontend_port": 3000,
                "public_api_path": "/api",
                "session_mode": "local",
                "status": "healthy",
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(f"DEV_STACK_OK {status_path}")


if __name__ == "__main__":
    main()
