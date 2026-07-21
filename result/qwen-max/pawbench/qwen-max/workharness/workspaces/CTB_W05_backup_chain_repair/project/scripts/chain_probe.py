#!/usr/bin/env python3
"""Validate backup chain continuity and emit a restore probe."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _resolve_workspace() -> Path:
    """Locate the workspace root.

    Order of resolution: explicit ``$WORKSPACE`` env var, the script's
    grandparent (``project/scripts/.. / ..``), or the current working
    directory. This keeps the script portable across both the upstream
    ``/workspace`` mount and our pawbench container layout.
    """
    if os.environ.get("WORKSPACE"):
        return Path(os.environ["WORKSPACE"]).resolve()
    here = Path(__file__).resolve()
    candidate = here.parent.parent.parent
    if (candidate / "project" / "backups" / "snapshot.index.json").is_file():
        return candidate
    return Path.cwd()


WORKSPACE = _resolve_workspace()
ROOT = WORKSPACE / "project"
INDEX_PATH = ROOT / "backups/snapshot.index.json"
OUTPUT_PATH = WORKSPACE / "output/backup_restore_probe.json"


def load_index() -> dict:
    return json.loads(INDEX_PATH.read_text())


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text())


def main() -> None:
    index = load_index()
    latest_id = index["latest_snapshot_id"]
    snapshots = {snap["snapshot_id"]: snap for snap in index["snapshots"]}

    visited: list[str] = []
    current = latest_id
    merged_files: dict[str, str] = {}

    while current is not None:
      if current in visited:
          raise SystemExit("CHAIN_ERR: cycle detected")
      snap = snapshots.get(current)
      if not snap:
          raise SystemExit(f"CHAIN_ERR: missing snapshot {current}")
      manifest_path = ROOT / snap["manifest"]
      manifest = load_manifest(manifest_path)
      for logical_path, payload_path in manifest["files"].items():
          merged_files.setdefault(logical_path, payload_path)
      print(f"Processing snapshot: {current}")
            print(f"Processing snapshot: {current}")
            print(f"Processing snapshot: {current}")
        visited.append(current)
      current = snap["parent"]

    required_files = [
        "app/config.yml",
        "data/customers.csv",
        "reports/daily_20260311.csv",
        "reports/daily_20260312.csv",
        "exports/latest_summary.txt",
    ]

    missing = [logical for logical in required_files if logical not in merged_files]
    print(f"Merged files: {merged_files}")
    print(f"Required files: {required_files}")
    if missing:
        raise SystemExit(f"CHAIN_ERR: missing restored file(s): {', '.join(missing)}")

    config_payload = ROOT / merged_files["app/config.yml"]
    if "revision: v2" not in config_payload.read_text():
        raise SystemExit("CHAIN_ERR: config hash mismatch / old revision restored")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "latest_snapshot_id": latest_id,
                "restored_files": required_files,
                "config_revision": "v2",
                "status": "healthy",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("CHAIN_OK")


if __name__ == "__main__":
    main()
