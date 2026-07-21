#!/usr/bin/env python3
"""Validate a materialized Harness-core AgentScope Harbor handoff directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pawbench_agentscope.harbor_contract import validate_contract_directory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs_dir", type=Path, help="Directory containing result.json")
    args = parser.parse_args()
    receipt = validate_contract_directory(args.logs_dir)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
