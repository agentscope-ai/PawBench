#!/usr/bin/env python3
"""Replay a native Harness-core trace through the non-authoritative audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_ROOT / "src"))

from pawbench_agentscope.trajectory_audit import audit_trace_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--latency-threshold-seconds", type=float, default=120.0)
    parser.add_argument("--consecutive-tool-threshold", type=int, default=3)
    args = parser.parse_args()
    try:
        receipt = audit_trace_file(
            args.trace.expanduser(),
            output_path=args.output.expanduser() if args.output else None,
            latency_threshold_seconds=args.latency_threshold_seconds,
            consecutive_tool_threshold=args.consecutive_tool_threshold,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
