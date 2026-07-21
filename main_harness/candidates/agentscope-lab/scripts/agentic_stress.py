from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import shlex
import shutil
import statistics
import stat
import sys
import time
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
MAIN_HARNESS_ROOT = CANDIDATE_ROOT.parents[1]
PROJECT_ROOT = MAIN_HARNESS_ROOT.parent
sys.path.insert(0, str(CANDIDATE_ROOT / "src"))

from pawbench_agentscope._atomic_io import (  # noqa: E402
    append_text_durable,
    atomic_write_text,
    read_text_no_follow,
)
from pawbench_agentscope._portable_security import (  # noqa: E402
    redact_sensitive_text,
    redact_sensitive_value,
)
from pawbench_agentscope.error_codes import classify_bridge_error  # noqa: E402
from pawbench_agentscope.features import FeatureConfig, TAXONOMY_VERSION  # noqa: E402
from pawbench_agentscope.models import TaskSpec  # noqa: E402
from pawbench_agentscope.runtime.agentscope_runner import dashscope_model_from_env, run_task  # noqa: E402
from pawbench_agentscope.trajectory_audit import analyze_native_trace, load_native_trace  # noqa: E402


DEFAULT_MODELS = ("qwen3.7-max", "deepseek-v4-pro", "kimi-k2.6")
DEFAULT_OUT = PROJECT_ROOT / "harness_ablation_runs" / "HARNESS_CORE_STRESS_TESTING_20260714"
OUTPUT_MARKER = ".harness-core-agentic-stress"
OUTPUT_SCHEMA = "harness-core-agentic-stress/v1"
MAX_STRESS_ARTIFACT_HASH_BYTES = 64 * 1024 * 1024
MAX_STRESS_RESULTS_BYTES = 32 * 1024 * 1024
MAX_STRESS_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_STRESS_RECORD_BYTES = 16 * 1024 * 1024
MAX_STRESS_MARKER_BYTES = 4 * 1024
MAX_STRESS_MODELS = 64
MAX_STRESS_MODEL_NAME_BYTES = 256
MAX_STRESS_CONCURRENCY = 1_024
MAX_STRESS_ITERS = 1_000
MAX_STRESS_TIMEOUT_SECONDS = 3_600.0
MAX_STRESS_ERROR_CHARS = 16_000


@dataclass(frozen=True)
class StressTask:
    task_id: str
    category: str
    instruction: str
    fixtures: dict[str, str]
    required_artifacts: tuple[str, ...]
    expected_text: dict[str, str] = field(default_factory=dict)
    required_tools: tuple[str, ...] = ("read_file", "write_file", "run_shell")
    hidden_validator: str | None = None


def task_suite() -> list[StressTask]:
    return [
        StressTask(
            task_id="01_sum_numbers",
            category="calculation",
            instruction=(
                "Read inputs/numbers.txt, sum every signed integer, and write only the decimal total "
                "to answer.txt. Do not include a label or explanation in the file."
            ),
            fixtures={"inputs/numbers.txt": "17\n-4\n23\n8\n"},
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "44"},
        ),
        StressTask(
            task_id="02_top_csv_row",
            category="data_extraction",
            instruction=(
                "Inspect inputs/scores.csv, find the row with the highest numeric score, and write "
                "name,score to answer.txt with no header."
            ),
            fixtures={"inputs/scores.csv": "name,score\natlas,81\nboron,95\ncinder,88\n"},
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "boron,95"},
        ),
        StressTask(
            task_id="03_extract_error_codes",
            category="log_analysis",
            instruction=(
                "Read logs/app.log. Extract the bracketed code from every ERROR line, sort the codes "
                "lexicographically, and write one code per line to answer.txt."
            ),
            fixtures={
                "logs/app.log": (
                    "INFO boot complete\nERROR [E404] missing route\nWARN slow request\n"
                    "ERROR [E102] invalid token\nINFO retrying\nERROR [E207] stale state\n"
                )
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "E102\nE207\nE404"},
        ),
        StressTask(
            task_id="04_inventory_totals",
            category="json_reasoning",
            instruction=(
                "Read inputs/inventory.json. Write exactly two lines to answer.txt: sku_count=<number> "
                "and total_quantity=<number>. Count all listed items."
            ),
            fixtures={
                "inputs/inventory.json": json.dumps(
                    {"items": [{"sku": "A", "qty": 4}, {"sku": "B", "qty": 7}, {"sku": "C", "qty": 2}]},
                    indent=2,
                )
                + "\n"
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "sku_count=3\ntotal_quantity=13"},
        ),
        StressTask(
            task_id="05_join_two_sources",
            category="multi_file_reasoning",
            instruction=(
                "Join inputs/users.csv with inputs/teams.json using user id. Write name,team rows to "
                "answer.txt sorted by name, with no header."
            ),
            fixtures={
                "inputs/users.csv": "id,name\n2,bob\n1,alice\n3,cara\n",
                "inputs/teams.json": json.dumps({"1": "red", "2": "blue", "3": "red"}, indent=2) + "\n",
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "alice,red\nbob,blue\ncara,red"},
        ),
        StressTask(
            task_id="06_open_todos",
            category="document_parsing",
            instruction=(
                "Read notes/plan.md. Write the text of unchecked Markdown checkbox items to answer.txt "
                "in their original order, one item per line, without checkbox markers."
            ),
            fixtures={
                "notes/plan.md": (
                    "# Launch plan\n\n- [x] collect data\n- [ ] calibrate sensor\n"
                    "- [x] run baseline\n- [ ] publish report\n"
                )
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "calibrate sensor\npublish report"},
        ),
        StressTask(
            task_id="07_edit_config",
            category="file_editing",
            instruction=(
                "Edit config/service.ini in place. Change only mode from safe to strict and timeout "
                "from 30 to 45. Preserve the section, retries value, key order, and spacing."
            ),
            fixtures={"config/service.ini": "[service]\nmode = safe\ntimeout = 30\nretries = 2\n"},
            required_artifacts=("config/service.ini",),
            expected_text={"config/service.ini": "[service]\nmode = strict\ntimeout = 45\nretries = 2"},
            required_tools=("read_file", "edit_file"),
        ),
        StressTask(
            task_id="08_rename_json_keys",
            category="data_transformation",
            instruction=(
                "Read inputs/legacy.json. Remove the old_ prefix from every top-level key, sort keys "
                "alphabetically, and write compact JSON with no spaces to output.json."
            ),
            fixtures={"inputs/legacy.json": '{"old_beta": 2, "old_alpha": 1}\n'},
            required_artifacts=("output.json",),
            expected_text={"output.json": '{"alpha":1,"beta":2}'},
        ),
        StressTask(
            task_id="09_sorted_unique_merge",
            category="data_transformation",
            instruction=(
                "Read inputs/a.txt and inputs/b.txt. Merge all words, remove duplicates, sort "
                "lexicographically, and write one word per line to answer.txt."
            ),
            fixtures={"inputs/a.txt": "pear\napple\npear\n", "inputs/b.txt": "banana\napple\nkiwi\n"},
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "apple\nbanana\nkiwi\npear"},
        ),
        StressTask(
            task_id="10_average_measurement",
            category="calculation",
            instruction=(
                "Read inputs/measurements.csv and calculate the arithmetic mean of the value column. "
                "Write average=<value> to answer.txt rounded to exactly two decimal places."
            ),
            fixtures={"inputs/measurements.csv": "sample,value\na,12.4\nb,10.6\nc,15.0\nd,14.0\n"},
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "average=13.00"},
        ),
        StressTask(
            task_id="11_latest_jsonl_event",
            category="json_reasoning",
            instruction=(
                "Read inputs/events.jsonl, identify the event with the latest ISO-8601 timestamp, "
                "and write only its event value to answer.txt."
            ),
            fixtures={
                "inputs/events.jsonl": (
                    '{"timestamp":"2026-07-14T01:00:00Z","event":"boot"}\n'
                    '{"timestamp":"2026-07-14T03:30:00Z","event":"deploy-complete"}\n'
                    '{"timestamp":"2026-07-14T02:15:00Z","event":"tests-passed"}\n'
                )
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "deploy-complete"},
        ),
        StressTask(
            task_id="12_dependency_order",
            category="planning",
            instruction=(
                "Read inputs/dependencies.txt where each line is task:comma-separated-prerequisites. "
                "Produce a valid execution order in answer.txt as one comma-separated line. Whenever "
                "multiple tasks are ready, choose alphabetically first."
            ),
            fixtures={
                "inputs/dependencies.txt": "build:\ntest:build\npackage:build\nrelease:package,test\n"
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "build,package,test,release"},
        ),
        StressTask(
            task_id="13_repair_python_function",
            category="code_repair",
            instruction=(
                "Inspect calc.py and repair the bug in add(). Do not change multiply(), function "
                "names, or signatures. The hidden validator will import calc.py and test both functions."
            ),
            fixtures={
                "calc.py": "def add(a, b):\n    return a - b\n\n\ndef multiply(a, b):\n    return a * b\n"
            },
            required_artifacts=("calc.py",),
            required_tools=("read_file", "edit_file", "run_shell"),
            hidden_validator=(
                "import os\n"
                "import sys\n"
                "sys.path.insert(0, os.environ['PAWBENCH_WORKSPACE_ROOT'])\n"
                "import calc\n"
                "assert calc.add(2, 3) == 5\n"
                "assert calc.add(-4, 1) == -3\n"
                "assert calc.multiply(6, 7) == 42\n"
            ),
        ),
        StressTask(
            task_id="14_reconcile_missing_payments",
            category="multi_file_reasoning",
            instruction=(
                "Compare inputs/orders.csv and inputs/payments.csv by order_id. Write unpaid order ids "
                "to answer.txt sorted lexicographically, one per line."
            ),
            fixtures={
                "inputs/orders.csv": "order_id,amount\nA1,10\nA2,25\nA3,8\nA4,13\n",
                "inputs/payments.csv": "order_id,paid\nA3,true\nA1,true\n",
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "A2\nA4"},
        ),
        StressTask(
            task_id="15_extract_markdown_section",
            category="document_parsing",
            instruction=(
                "Read docs/design.md and copy only the body text under the 'Decision' heading, stopping "
                "before the next heading. Preserve the two body lines exactly in answer.txt."
            ),
            fixtures={
                "docs/design.md": (
                    "# Design\n\n## Context\nOld context.\n\n## Decision\nUse bounded retries.\n"
                    "Keep verification independent.\n\n## Consequences\nMore trace events.\n"
                )
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "Use bounded retries.\nKeep verification independent."},
        ),
        StressTask(
            task_id="16_directory_manifest",
            category="filesystem_reasoning",
            instruction=(
                "Inspect the docs directory recursively. For every .txt file, write relative_path,byte_size "
                "to answer.txt, sorted by relative path. Paths must be relative to the workspace root."
            ),
            fixtures={
                "docs/a.txt": "alpha",
                "docs/nested/b.txt": "beta\n",
                "docs/nested/ignore.bin": "1234567",
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "docs/a.txt,5\ndocs/nested/b.txt,5"},
            required_tools=("glob", "read_file", "write_file", "run_shell"),
        ),
        StressTask(
            task_id="17_http_status_counts",
            category="log_analysis",
            instruction=(
                "Read logs/access.log. Count each HTTP status code. Write status=count lines to answer.txt "
                "sorted numerically by status."
            ),
            fixtures={
                "logs/access.log": (
                    "GET /a 200\nGET /b 404\nPOST /c 200\nGET /d 500\nGET /e 404\nGET /f 200\n"
                )
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "200=3\n404=2\n500=1"},
        ),
        StressTask(
            task_id="18_follow_local_skill",
            category="context_following",
            instruction=(
                "Read inputs/token.txt and follow the local SKILL.md mapping rule. Write only the mapped "
                "value to answer.txt."
            ),
            fixtures={
                "SKILL.md": "# Token mapping\n\nWhen the input token is ALPHA, the required mapped value is cobalt-17.\n",
                "inputs/token.txt": "ALPHA\n",
            },
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "cobalt-17"},
        ),
        StressTask(
            task_id="19_reverse_uppercase_lines",
            category="data_transformation",
            instruction=(
                "Read inputs/colors.txt. Reverse the line order, convert each value to uppercase, and "
                "write the result to answer.txt with one value per line."
            ),
            fixtures={"inputs/colors.txt": "red\nblue\ngreen\n"},
            required_artifacts=("answer.txt",),
            expected_text={"answer.txt": "GREEN\nBLUE\nRED"},
        ),
        StressTask(
            task_id="20_multi_artifact_sales_report",
            category="multi_artifact",
            instruction=(
                "Read inputs/sales.csv. Sum sales by region and compute the grand total. Create report.md "
                "with a '# Sales Summary' heading, exactly one blank line, then bullets for North, South, and "
                "Grand total. Also "
                "create summary.csv with header region,total and rows North, South, ALL in that order."
            ),
            fixtures={"inputs/sales.csv": "region,value\nNorth,10\nSouth,7\nNorth,15\nSouth,9\n"},
            required_artifacts=("report.md", "summary.csv"),
            expected_text={
                "report.md": "# Sales Summary\n\n- North: 25\n- South: 16\n- Grand total: 41",
                "summary.csv": "region,total\nNorth,25\nSouth,16\nALL,41",
            },
        ),
    ]


def safe_write(root: Path, rel_path: str, content: str) -> None:
    root = root.resolve()
    path = (root / rel_path).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"fixture path escapes workspace: {rel_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def atomic_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(
            redact_sensitive_value(payload),
            ensure_ascii=False,
            indent=2,
            default=str,
            allow_nan=False,
        )
        + "\n",
    )


def prepare_output_root(out_root: Path, *, fresh: bool) -> None:
    if out_root.is_symlink():
        raise ValueError(f"stress output directory must not be a symlink: {out_root}")
    marker = out_root / OUTPUT_MARKER
    if out_root.exists() and fresh:
        if (
            marker.is_symlink()
            or not marker.is_file()
            or read_text_no_follow(marker, max_bytes=MAX_STRESS_MARKER_BYTES).strip()
            != OUTPUT_SCHEMA
        ):
            raise ValueError(f"refusing to delete unmarked stress output: {out_root}")
        shutil.rmtree(out_root)
    if out_root.exists() and any(out_root.iterdir()) and (
        marker.is_symlink() or not marker.is_file()
    ):
        raise ValueError(f"refusing to write into unmarked non-empty output: {out_root}")
    out_root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(marker, OUTPUT_SCHEMA + "\n")


def model_slug(model_name: str) -> str:
    candidate = model_name.replace("/", "__")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip(".") or "model"
    safe = safe[:80]
    if candidate == model_name and safe == candidate and len(candidate) <= 80:
        return safe
    suffix = hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:10]
    return f"{safe}-{suffix}"


def safe_run_root(out_root: Path, model_name: str, task_id: str) -> Path:
    model_root = out_root / model_slug(model_name)
    run_root = model_root / task_id
    if model_root.is_symlink() or run_root.is_symlink():
        raise ValueError(f"stress run path must not contain a symlink: {run_root}")
    resolved = run_root.resolve()
    resolved_output = out_root.resolve()
    if resolved != resolved_output and resolved_output not in resolved.parents:
        raise ValueError(f"stress run path escapes output directory: {run_root}")
    return run_root


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows, _ = load_native_trace(path)
    return rows


def artifact_hashes(workspace: Path, artifacts: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    root = workspace.resolve()
    for rel_path in artifacts:
        path = workspace / rel_path
        if path.is_symlink():
            values[rel_path] = {"hash_status": "skipped_symlink"}
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved != root and root not in resolved.parents:
            values[rel_path] = {"hash_status": "skipped_non_regular_or_external"}
            continue
        digest = hashlib.sha256()
        flags = (
            os.O_RDONLY
            | int(getattr(os, "O_NOFOLLOW", 0))
            | int(getattr(os, "O_NONBLOCK", 0))
        )
        try:
            fd = os.open(path, flags)
        except OSError:
            values[rel_path] = {"hash_status": "skipped_non_regular_or_external"}
            continue
        with os.fdopen(fd, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                values[rel_path] = {"hash_status": "skipped_non_regular_or_external"}
                continue
            if metadata.st_size > MAX_STRESS_ARTIFACT_HASH_BYTES:
                values[rel_path] = {
                    "size": metadata.st_size,
                    "sha256": None,
                    "hash_status": "skipped_too_large",
                }
                continue
            observed_size = 0
            too_large = False
            while chunk := handle.read(64 * 1024):
                observed_size += len(chunk)
                if observed_size > MAX_STRESS_ARTIFACT_HASH_BYTES:
                    too_large = True
                    break
                digest.update(chunk)
        if too_large:
            values[rel_path] = {
                "size": observed_size,
                "sha256": None,
                "hash_status": "skipped_too_large",
            }
            continue
        values[rel_path] = {
            "size": observed_size,
            "sha256": digest.hexdigest(),
            "hash_status": "hashed",
        }
    return values


def trace_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    event_types = [str(row.get("type", "")) for row in rows]
    model_names = sorted(
        {
            str(row.get("payload", {}).get("model_name"))
            for row in rows
            if row.get("type") == "agentscope_event"
            and row.get("payload", {}).get("type") == "MODEL_CALL_START"
            and row.get("payload", {}).get("model_name")
        }
    )
    completion = next(
        (row.get("payload", {}) for row in reversed(rows) if row.get("type") == "completion_decision"),
        {},
    )
    return {
        "event_count_on_disk": len(rows),
        "event_types": sorted(set(event_types)),
        "model_names_observed": model_names,
        "tool_calls": [
            str(row.get("payload", {}).get("tool_call_name"))
            for row in rows
            if row.get("type") == "agentscope_event" and row.get("payload", {}).get("type") == "TOOL_CALL_START"
        ],
        "model_call_count": sum(
            row.get("type") == "agentscope_event" and row.get("payload", {}).get("type") == "MODEL_CALL_START"
            for row in rows
        ),
        "tool_error_count": sum(item in {"normalized_tool_error", "raw_tool_error"} for item in event_types),
        "workspace_guard_denials": sum(item == "workspace_guard_denied" for item in event_types),
        "repair_used": "retry_start" in event_types,
        "trace_complete": all(item in event_types for item in ("run_start", "verifier_result", "completion_decision")),
        "stop_reason": completion.get("stop_reason"),
    }


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def load_results(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    raw = read_text_no_follow(path, max_bytes=MAX_STRESS_RESULTS_BYTES)
    lines = raw.splitlines()
    nonempty_indexes = [index for index, line in enumerate(lines) if line.strip()]
    last_nonempty = nonempty_indexes[-1] if nonempty_indexes else -1
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_nonfinite_json_constant,
            )
        except json.JSONDecodeError as exc:
            if index == last_nonempty and not raw.endswith(("\n", "\r")):
                break
            raise ValueError(f"corrupt JSONL record at {path}:{index + 1}") from exc
        except ValueError as exc:
            raise ValueError(f"corrupt JSONL record at {path}:{index + 1}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL record must be an object at {path}:{index + 1}")
        rows.append(value)
    return rows


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    append_text_durable(
        path,
        json.dumps(
            redact_sensitive_value(payload),
            ensure_ascii=False,
            default=str,
            allow_nan=False,
        )
        + "\n",
    )


def validated_models(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("--models must contain non-empty model names")
    if len(values) > MAX_STRESS_MODELS:
        raise ValueError(f"--models supports at most {MAX_STRESS_MODELS} names")
    models: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("--models must contain non-empty trimmed model names")
        if len(value.encode("utf-8")) > MAX_STRESS_MODEL_NAME_BYTES:
            raise ValueError(
                f"model names must not exceed {MAX_STRESS_MODEL_NAME_BYTES} UTF-8 bytes"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("model names must not contain control characters")
        models.append(value)
    if len(set(models)) != len(models):
        raise ValueError("--models must not contain duplicates")
    return models


def bounded_error_text(value: Any) -> str:
    message = redact_sensitive_text(str(value))
    if len(message) <= MAX_STRESS_ERROR_CHARS:
        return message
    half = (MAX_STRESS_ERROR_CHARS - 48) // 2
    return f"{message[:half]}...[truncated stress error]...{message[-half:]}"


def validate_execution_limits(args: argparse.Namespace) -> None:
    integer_fields = {
        "tasks_per_model": (1, 20),
        "per_model_concurrency": (1, MAX_STRESS_CONCURRENCY),
        "global_concurrency": (1, MAX_STRESS_CONCURRENCY),
        "max_iters": (1, MAX_STRESS_ITERS),
    }
    for field_name, (minimum, maximum) in integer_fields.items():
        value = getattr(args, field_name, None)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(
                f"--{field_name.replace('_', '-')} must be an integer in [{minimum}, {maximum}]"
            )
    timeout = getattr(args, "timeout_seconds", None)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or not 0 < float(timeout) <= MAX_STRESS_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"--timeout-seconds must be finite and in (0, {MAX_STRESS_TIMEOUT_SECONDS:g}]"
        )


def validate_resume_record(
    record: dict[str, Any],
    *,
    expected_keys: set[tuple[str, str]],
) -> tuple[str, str]:
    model = record.get("model")
    task_id = record.get("task_id")
    if not isinstance(model, str) or not isinstance(task_id, str):
        raise ValueError("resume record model and task_id must be strings")
    key = (model, task_id)
    if key not in expected_keys:
        raise ValueError(f"resume record is outside the configured task matrix: {key!r}")
    status = record.get("status")
    if status not in {"completed", "exception"}:
        raise ValueError(f"resume record has invalid status for {key!r}: {status!r}")
    for field_name in ("accepted", "completion_ok", "verifier_ok"):
        if not isinstance(record.get(field_name), bool):
            raise ValueError(f"resume record {field_name} must be boolean for {key!r}")
    duration = record.get("duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or float(duration) < 0
    ):
        raise ValueError(f"resume record duration_seconds is invalid for {key!r}")
    metrics = record.get("trace_metrics")
    if not isinstance(metrics, dict) or not isinstance(metrics.get("trace_complete"), bool):
        raise ValueError(f"resume record trace_metrics is invalid for {key!r}")
    observed_models = metrics.get("model_names_observed")
    if not isinstance(observed_models, list) or any(
        not isinstance(item, str) for item in observed_models
    ):
        raise ValueError(f"resume record model_names_observed is invalid for {key!r}")
    if status == "completed":
        if not isinstance(record.get("verifier"), dict):
            raise ValueError(f"completed resume record verifier must be an object for {key!r}")
        if record["accepted"] != bool(record["completion_ok"] and record["verifier_ok"]):
            raise ValueError(f"completed resume record acceptance is inconsistent for {key!r}")
    elif record["accepted"] or record["completion_ok"] or record["verifier_ok"]:
        raise ValueError(f"exception resume record cannot report success for {key!r}")
    return key


def _load_json_object(path: Path, *, max_bytes: int) -> dict[str, Any]:
    value = json.loads(
        read_text_no_follow(path, max_bytes=max_bytes),
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_nonfinite_json_constant,
    )
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _matches_local_path_receipt(value: Any, path: Path) -> bool:
    """Match only the exact local path or its persisted redacted form."""

    if not isinstance(value, str):
        return False
    raw = str(path)
    return value in {raw, redact_sensitive_text(raw)}


def resume_record_has_evidence(
    record: dict[str, Any],
    *,
    out_root: Path,
    task: StressTask,
) -> bool:
    """Accept a resume receipt only when its local evidence still agrees."""

    if record.get("status") != "completed":
        return False
    model = str(record["model"])
    try:
        run_root = safe_run_root(out_root, model, task.task_id)
        workspace = run_root / "workspace"
        trace_path = run_root / "trace.jsonl"
        shadow_path = run_root / "trajectory-shadow.json"
        result_path = run_root / "result.json"
        trajectory_shadow = record.get("trajectory_shadow")
        if (
            not _matches_local_path_receipt(record.get("trace_path"), trace_path)
            or not _matches_local_path_receipt(record.get("workspace_root"), workspace)
            or not isinstance(trajectory_shadow, dict)
            or not _matches_local_path_receipt(trajectory_shadow.get("path"), shadow_path)
            or workspace.is_symlink()
            or not workspace.is_dir()
        ):
            return False
        rows = read_trace(trace_path)
        observed_metrics = trace_metrics(rows)
        if (
            observed_metrics != record.get("trace_metrics")
            or not observed_metrics["trace_complete"]
            or model not in observed_metrics["model_names_observed"]
        ):
            return False
        shadow = _load_json_object(shadow_path, max_bytes=MAX_STRESS_RECORD_BYTES)
        if (
            trajectory_shadow.get("authority") != shadow.get("authority")
            or trajectory_shadow.get("summary") != shadow.get("summary")
        ):
            return False
        result = _load_json_object(result_path, max_bytes=MAX_STRESS_RECORD_BYTES)
        if result != record:
            return False
        if artifact_hashes(workspace, task.required_artifacts) != record.get("artifacts"):
            return False
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return False
    return True


def progress_snapshot(
    *,
    target: int,
    completed: int,
    running: int,
    accepted: int,
    exceptions: int,
    retryable_exceptions: int,
    resumed_completed: int,
    status: str,
) -> dict[str, Any]:
    return {
        "schema_version": "harness-core-stress-progress/v1",
        "status": status,
        "target": target,
        "completed": completed,
        "running": running,
        "remaining": max(0, target - completed - running),
        "accepted": accepted,
        "exceptions": exceptions,
        "retryable_exceptions": retryable_exceptions,
        "resumed_completed": resumed_completed,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def summarize(records: list[dict[str, Any]], models: list[str], tasks: list[StressTask]) -> dict[str, Any]:
    expected_keys = {(model, task.task_id) for model in models for task in tasks}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        latest[(str(record.get("model")), str(record.get("task_id")))] = record
    selected = [latest[key] for key in sorted(expected_keys) if key in latest]

    def group_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
        durations = [float(item.get("duration_seconds", 0.0)) for item in items]
        accepted = sum(bool(item.get("accepted")) for item in items)
        p95_index = max(0, math.ceil(len(durations) * 0.95) - 1)
        return {
            "total": len(items),
            "accepted": accepted,
            "accept_rate": round(accepted / len(items), 4) if items else 0.0,
            "verifier_passed": sum(bool(item.get("verifier_ok")) for item in items),
            "completion_passed": sum(bool(item.get("completion_ok")) for item in items),
            "exceptions": sum(item.get("status") == "exception" for item in items),
            "repairs": sum(bool(item.get("trace_metrics", {}).get("repair_used")) for item in items),
            "tool_errors": sum(int(item.get("trace_metrics", {}).get("tool_error_count", 0)) for item in items),
            "workspace_guard_denials": sum(
                int(item.get("trace_metrics", {}).get("workspace_guard_denials", 0)) for item in items
            ),
            "median_duration_seconds": round(statistics.median(durations), 3) if durations else 0.0,
            "p95_duration_seconds": round(sorted(durations)[p95_index], 3)
            if durations
            else 0.0,
        }

    by_model = {model: group_payload([item for item in selected if item.get("model") == model]) for model in models}
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in selected:
        categories[str(item.get("category"))].append(item)
    by_category = {name: group_payload(items) for name, items in sorted(categories.items())}
    overall = group_payload(selected)
    overall.update(
        {
            "expected": len(expected_keys),
            "completed_keys": len(selected),
            "false_accepts": sum(bool(item.get("accepted")) and not bool(item.get("verifier_ok")) for item in selected),
            "trace_incomplete": sum(not bool(item.get("trace_metrics", {}).get("trace_complete")) for item in selected),
            "model_id_mismatches": sum(
                item.get("status") != "exception"
                and item.get("model") not in item.get("trace_metrics", {}).get("model_names_observed", [])
                for item in selected
            ),
        }
    )
    shadow_summaries = [
        item["trajectory_shadow"]["summary"]
        for item in selected
        if isinstance(item.get("trajectory_shadow"), dict)
        and isinstance(item["trajectory_shadow"].get("summary"), dict)
    ]
    shadow_flag_counts = Counter(
        str(check)
        for summary in shadow_summaries
        for check in summary.get("flagged_checks", [])
        if isinstance(check, str)
    )
    shadow_audit = {
        "authority": {
            "mode": "shadow_non_authoritative",
            "canonical_score_modified": False,
            "canonical_verdict_modified": False,
            "causal_attribution_allowed": False,
        },
        "audited_runs": len(shadow_summaries),
        "expected_runs": len(selected),
        "clean_runs": sum(not summary.get("flagged_checks") for summary in shadow_summaries),
        "flagged_runs": sum(bool(summary.get("flagged_checks")) for summary in shadow_summaries),
        "anomaly_count": sum(int(summary.get("anomaly_count", 0)) for summary in shadow_summaries),
        "manual_review_recommended_runs": sum(
            bool(summary.get("manual_review_recommended")) for summary in shadow_summaries
        ),
        "flagged_check_counts": dict(sorted(shadow_flag_counts.items())),
        "all_recorded_runs_audited": len(shadow_summaries) == len(selected),
        "all_audits_clean": bool(shadow_summaries) and not shadow_flag_counts,
    }
    minimum_model_rate = min((payload["accept_rate"] for payload in by_model.values()), default=0.0)
    ready = bool(
        overall["completed_keys"] == overall["expected"]
        and overall["exceptions"] == 0
        and overall["false_accepts"] == 0
        and overall["trace_incomplete"] == 0
        and overall["model_id_mismatches"] == 0
        and overall["accept_rate"] >= 0.90
        and minimum_model_rate >= 0.80
    )
    failures = [
        {
            "model": item.get("model"),
            "task_id": item.get("task_id"),
            "category": item.get("category"),
            "status": item.get("status"),
            "accepted": item.get("accepted"),
            "verifier_ok": item.get("verifier_ok"),
            "completion_ok": item.get("completion_ok"),
            "stop_reason": item.get("trace_metrics", {}).get("stop_reason"),
            "error": item.get("error"),
            "verifier": item.get("verifier"),
            "trace_path": item.get("trace_path"),
        }
        for item in selected
        if not item.get("accepted")
    ]
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "taxonomy_version": TAXONOMY_VERSION,
        "readiness": {
            "ready": ready,
            "verdict": "READY" if ready else "NOT_READY",
            "criteria": {
                "all_expected_tasks_recorded": overall["completed_keys"] == overall["expected"],
                "no_harness_exceptions": overall["exceptions"] == 0,
                "no_false_accepts": overall["false_accepts"] == 0,
                "all_traces_complete": overall["trace_incomplete"] == 0,
                "all_model_ids_verified": overall["model_id_mismatches"] == 0,
                "overall_accept_rate_at_least_90_percent": overall["accept_rate"] >= 0.90,
                "each_model_accept_rate_at_least_80_percent": minimum_model_rate >= 0.80,
            },
        },
        "overall": overall,
        "by_model": by_model,
        "by_category": by_category,
        "trajectory_shadow_audit": shadow_audit,
        "tool_call_counts": dict(
            sorted(Counter(tool for item in selected for tool in item.get("trace_metrics", {}).get("tool_calls", [])).items())
        ),
        "failures": failures,
    }


def render_report(summary: dict[str, Any], out_root: Path) -> str:
    readiness = summary["readiness"]
    overall = summary["overall"]
    lines = [
        "# Harness-core Agentic Stress Test",
        "",
        f"- Verdict: **{readiness['verdict']}**",
        f"- Taxonomy: `{summary['taxonomy_version']}`",
        f"- Recorded tasks: `{overall['completed_keys']}/{overall['expected']}`",
        f"- Accepted: `{overall['accepted']}/{overall['total']}` (`{overall['accept_rate']:.1%}`)",
        f"- Harness exceptions: `{overall['exceptions']}`",
        f"- False accepts: `{overall['false_accepts']}`",
        f"- Output directory: `{out_root}`",
        "",
        "> This is a deterministic local agentic readiness suite, not an official PawBench score.",
        "",
        "## Results by model",
        "",
        "| Model | Accepted | Rate | Verifier passed | Exceptions | Repairs | Median seconds |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model, payload in summary["by_model"].items():
        lines.append(
            f"| `{model}` | {payload['accepted']}/{payload['total']} | {payload['accept_rate']:.1%} | "
            f"{payload['verifier_passed']}/{payload['total']} | {payload['exceptions']} | {payload['repairs']} | "
            f"{payload['median_duration_seconds']:.3f} |"
        )
    lines += ["", "## Readiness criteria", ""]
    for name, passed in readiness["criteria"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    shadow = summary["trajectory_shadow_audit"]
    lines += [
        "",
        "## Trajectory shadow audit",
        "",
        f"- Audited: `{shadow['audited_runs']}/{shadow['expected_runs']}`",
        f"- Clean: `{shadow['clean_runs']}/{shadow['audited_runs']}`",
        f"- Flagged runs: `{shadow['flagged_runs']}`",
        f"- Manual review recommended: `{shadow['manual_review_recommended_runs']}`",
        "- Authority: non-authoritative; canonical scores and verdicts are unchanged.",
    ]
    if shadow["flagged_check_counts"]:
        lines.append(
            "- Flag counts: "
            + ", ".join(
                f"`{name}`={count}"
                for name, count in shadow["flagged_check_counts"].items()
            )
        )
    else:
        lines.append("- Flag counts: none.")
    lines += ["", "## Tool calls", ""]
    for tool, count in summary["tool_call_counts"].items():
        lines.append(f"- `{tool}`: {count}")
    lines += ["", "## Failed tasks", ""]
    if not summary["failures"]:
        lines.append("None.")
    else:
        for failure in summary["failures"]:
            lines.append(
                f"- `{failure['model']}` / `{failure['task_id']}`: status={failure['status']}, "
                f"verifier_ok={failure['verifier_ok']}, completion_ok={failure['completion_ok']}, "
                f"stop_reason={failure['stop_reason']}"
            )
    return "\n".join(lines) + "\n"


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    validate_execution_limits(args)
    models = validated_models(args.models)
    requested_out_root = Path(args.out_dir).expanduser()
    if requested_out_root.is_symlink():
        raise ValueError(f"stress output directory must not be a symlink: {requested_out_root}")
    out_root = requested_out_root.resolve()
    prepare_output_root(out_root, fresh=args.fresh)
    validators_root = out_root / "validators"
    if validators_root.is_symlink():
        raise ValueError(f"validators directory must not be a symlink: {validators_root}")
    validators_root.mkdir(exist_ok=True)
    if not validators_root.is_dir():
        raise ValueError(f"validators path must be a directory: {validators_root}")

    suite = task_suite()
    if args.task_ids:
        requested = set(args.task_ids)
        known = {task.task_id for task in suite}
        unknown = sorted(requested - known)
        if unknown:
            raise SystemExit(f"unknown --task-ids: {', '.join(unknown)}")
        tasks = [task for task in suite if task.task_id in requested]
    else:
        tasks = suite[: args.tasks_per_model]
        if len(tasks) != args.tasks_per_model:
            raise SystemExit(f"requested {args.tasks_per_model} tasks but suite has {len(tasks)}")

    for task in tasks:
        if task.hidden_validator:
            validator_path = validators_root / f"{task.task_id}.py"
            atomic_write_text(validator_path, task.hidden_validator)

    task_contract_payload = [
        {
            "task_id": task.task_id,
            "category": task.category,
            "instruction": task.instruction,
            "fixtures": sorted(task.fixtures),
            "required_artifacts": list(task.required_artifacts),
            "required_tools": list(task.required_tools),
            "uses_hidden_validator": bool(task.hidden_validator),
        }
        for task in tasks
    ]
    semantic_config = {
        "models": models,
        "tasks_sha256": hashlib.sha256(
            json.dumps(task_contract_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "taxonomy_version": TAXONOMY_VERSION,
        "feature_profile": "all_enabled",
        "max_iters": args.max_iters,
        "timeout_seconds": args.timeout_seconds,
    }
    execution_only_config = {
        "per_model_concurrency": args.per_model_concurrency,
        "global_concurrency": args.global_concurrency,
        "resume": bool(args.resume),
    }
    analysis_only_config = {
        "trajectory_shadow_schema": "harness-core-trajectory-shadow/v1",
        "latency_threshold_seconds": 120.0,
        "consecutive_tool_threshold": 3,
        "canonical_effect": "none",
    }
    semantic_sha256 = hashlib.sha256(
        json.dumps(semantic_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    execution_only_sha256 = hashlib.sha256(
        json.dumps(execution_only_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest_path = out_root / "manifest.json"
    results_path = out_root / "results.jsonl"
    invocation = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "execution_only": execution_only_config,
        "execution_only_sha256": execution_only_sha256,
    }
    manifest_payload: dict[str, Any] = {
            "schema_version": 1,
            "created_at": invocation["started_at"],
            "purpose": "HARNESS_CORE_STRESS_TESTING",
            "candidate": "AgentScope-Lab",
            "taxonomy_version": TAXONOMY_VERSION,
            "models": models,
            "tasks_per_model": len(tasks),
            "expected_runs": len(models) * len(tasks),
            "per_model_concurrency": args.per_model_concurrency,
            "global_concurrency": args.global_concurrency,
            "max_iters": args.max_iters,
            "timeout_seconds": args.timeout_seconds,
            "configuration_classification": {
                "semantic": semantic_config,
                "semantic_sha256": semantic_sha256,
                "execution_only": execution_only_config,
                "execution_only_sha256": execution_only_sha256,
                "analysis_only": analysis_only_config,
                "note": (
                    "Model, task, Feature, iteration, and timeout settings may alter agent behavior; "
                    "concurrency and resume orchestration do not define the evaluated condition."
                ),
            },
            "credentials": {
                "dashscope_key_present": bool(os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_API_KEY")),
                "base_url_present": bool(os.getenv("DASHSCOPE_BASE_URL") or os.getenv("ALIYUN_BASE_URL")),
            },
            "readiness_thresholds": {
                "overall_accept_rate": 0.90,
                "per_model_accept_rate": 0.80,
                "harness_exceptions": 0,
                "false_accepts": 0,
                "trace_incomplete": 0,
            },
            "execution_invocations": [invocation],
        }
    if manifest_path.exists():
        if manifest_path.is_symlink():
            raise ValueError(f"manifest must not be a symlink: {manifest_path}")
        try:
            prior_manifest = json.loads(
                read_text_no_follow(
                    manifest_path,
                    max_bytes=MAX_STRESS_MANIFEST_BYTES,
                ),
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_nonfinite_json_constant,
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"existing manifest is unreadable: {manifest_path}") from exc
        if not isinstance(prior_manifest, dict):
            raise ValueError(f"existing manifest must be a JSON object: {manifest_path}")
        if args.resume:
            prior_classification = prior_manifest.get("configuration_classification")
            prior_semantic_hash = (
                prior_classification.get("semantic_sha256")
                if isinstance(prior_classification, dict)
                else None
            )
            if prior_semantic_hash != semantic_sha256:
                raise ValueError(
                    "resume semantic configuration differs from the existing stress run; "
                    "use a new --out-dir or --fresh"
                )
            manifest_payload["created_at"] = prior_manifest.get("created_at", manifest_payload["created_at"])
            prior_invocations = prior_manifest.get("execution_invocations")
            if isinstance(prior_invocations, list):
                manifest_payload["execution_invocations"] = [*prior_invocations, invocation]
        elif results_path.exists():
            raise ValueError("existing results require --resume or --fresh")
    elif args.resume and results_path.exists():
        raise ValueError("cannot resume results without a matching manifest")
    atomic_json(manifest_path, manifest_payload)
    atomic_json(
        out_root / "tasks.json",
        task_contract_payload,
    )

    progress_events_path = out_root / "progress.jsonl"
    progress_path = out_root / "progress.json"
    existing = load_results(results_path) if args.resume else []
    expected_keys = {(model, task.task_id) for model in models for task in tasks}
    latest_existing: dict[tuple[str, str], dict[str, Any]] = {}
    for item in existing:
        key = validate_resume_record(item, expected_keys=expected_keys)
        latest_existing[key] = item
    tasks_by_id = {task.task_id: task for task in tasks}
    completed = {
        key
        for key, item in latest_existing.items()
        if resume_record_has_evidence(
            item,
            out_root=out_root,
            task=tasks_by_id[key[1]],
        )
    }
    write_lock = asyncio.Lock()
    global_semaphore = asyncio.Semaphore(args.global_concurrency)
    model_semaphores = {model: asyncio.Semaphore(args.per_model_concurrency) for model in models}
    selected_existing = [latest_existing[key] for key in sorted(completed)]
    progress = {
        "done": len(completed),
        "running": 0,
        "accepted": sum(bool(item.get("accepted")) for item in selected_existing),
        "exceptions": 0,
        "retryable_exceptions": 0,
        "resumed_completed": len(completed),
        "target": len(models) * len(tasks),
    }

    def emit_progress_event(event: str, *, status: str, **payload: Any) -> None:
        snapshot = progress_snapshot(
            target=progress["target"],
            completed=progress["done"],
            running=progress["running"],
            accepted=progress["accepted"],
            exceptions=progress["exceptions"],
            retryable_exceptions=progress["retryable_exceptions"],
            resumed_completed=progress["resumed_completed"],
            status=status,
        )
        event_payload = {
            "schema_version": "harness-core-stress-progress-event/v1",
            "event": event,
            "timestamp": snapshot["updated_at"],
            "status": status,
            "payload": payload,
            "snapshot": snapshot,
        }
        append_jsonl(progress_events_path, event_payload)
        atomic_json(progress_path, snapshot)

    emit_progress_event("run_started", status="running", resumed=bool(args.resume))

    async def run_one(model_name: str, task: StressTask) -> dict[str, Any] | None:
        key = (model_name, task.task_id)
        if key in completed:
            return None
        async with global_semaphore, model_semaphores[model_name]:
            run_root = safe_run_root(out_root, model_name, task.task_id)
            workspace = run_root / "workspace"
            trace_path = run_root / "trace.jsonl"
            async with write_lock:
                progress["running"] += 1
                emit_progress_event(
                    "task_started",
                    status="running",
                    model=model_name,
                    task_id=task.task_id,
                )
            started = time.monotonic()
            record: dict[str, Any]
            try:
                if run_root.exists():
                    shutil.rmtree(run_root)
                workspace.mkdir(parents=True)
                for rel_path, content in task.fixtures.items():
                    safe_write(workspace, rel_path, content)
                validator_command = None
                if task.hidden_validator:
                    validator_path = validators_root / f"{task.task_id}.py"
                    validator_command = f"{shlex.quote(sys.executable)} {shlex.quote(str(validator_path))}"
                spec = TaskSpec(
                    task_id=f"{model_slug(model_name)}__{task.task_id}",
                    instruction=task.instruction,
                    task_dir=workspace,
                    required_artifacts=list(task.required_artifacts),
                    required_tools=list(task.required_tools),
                    isolated_workspace=True,
                    test_command=validator_command,
                    hidden_contract={
                        "artifact_text": task.expected_text,
                        "validator_timeout_sec": 30,
                    },
                )
                config = FeatureConfig(
                    enabled=set(FeatureConfig.all_enabled().enabled),
                    runtime_timeout_seconds=float(args.timeout_seconds),
                )
                result = await run_task(
                    spec,
                    workspace_root=workspace,
                    trace_path=trace_path,
                    feature_config=config,
                    model=dashscope_model_from_env(model_name=model_name),
                    max_iters=args.max_iters,
                )
                rows = read_trace(trace_path)
                record = {
                    "model": model_name,
                    "task_id": task.task_id,
                    "category": task.category,
                    "status": "completed",
                    "accepted": result.accepted,
                    "completion_ok": result.completion_ok,
                    "verifier_ok": result.verifier.ok,
                    "verifier": result.verifier.model_dump(),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "event_count": result.event_count,
                    "trace_path": str(trace_path),
                    "workspace_root": str(workspace),
                    "trace_metrics": trace_metrics(rows),
                    "artifacts": artifact_hashes(workspace, task.required_artifacts),
                    "error": None,
                }
            except Exception as exc:
                try:
                    rows = read_trace(trace_path)
                except (OSError, ValueError):
                    rows = []
                safe_error = bounded_error_text(exc)
                classification = classify_bridge_error(
                    error_type=type(exc).__name__,
                    error=safe_error,
                ).model_dump(mode="json")
                record = {
                    "model": model_name,
                    "task_id": task.task_id,
                    "category": task.category,
                    "status": "exception",
                    "accepted": False,
                    "completion_ok": False,
                    "verifier_ok": False,
                    "verifier": None,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "event_count": len(rows),
                    "trace_path": str(trace_path),
                    "workspace_root": str(workspace),
                    "trace_metrics": trace_metrics(rows),
                    "artifacts": artifact_hashes(workspace, task.required_artifacts),
                    "error": f"{type(exc).__name__}: {safe_error}",
                    "error_classification": classification,
                }
            shadow_audit = analyze_native_trace(rows)
            shadow_path = run_root / "trajectory-shadow.json"
            atomic_json(shadow_path, shadow_audit)
            record["trajectory_shadow"] = {
                "path": str(shadow_path),
                "authority": shadow_audit["authority"],
                "summary": shadow_audit["summary"],
            }
            record["recorded_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            atomic_json(run_root / "result.json", record)
            async with write_lock:
                append_jsonl(results_path, record)
                progress["running"] -= 1
                progress["done"] += 1
                progress["accepted"] += int(bool(record["accepted"]))
                if record["status"] == "exception":
                    progress["exceptions"] += 1
                    progress["retryable_exceptions"] += int(
                        bool(record.get("error_classification", {}).get("retryable"))
                    )
                emit_progress_event(
                    "task_finished",
                    status="running",
                    model=model_name,
                    task_id=task.task_id,
                    accepted=record["accepted"],
                    result_status=record["status"],
                    retryable=record.get("error_classification", {}).get("retryable"),
                )
                print(
                    json.dumps(
                        {
                            "progress": f"{progress['done']}/{progress['target']}",
                            "model": model_name,
                            "task": task.task_id,
                            "accepted": record["accepted"],
                            "status": record["status"],
                            "seconds": record["duration_seconds"],
                            "error": record["error"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            return record

    await asyncio.gather(*(run_one(model, task) for model in models for task in tasks))
    records = load_results(results_path)
    summary = summarize(records, models, tasks)
    atomic_json(out_root / "summary.json", summary)
    atomic_write_text(out_root / "REPORT.md", render_report(summary, out_root))
    async with write_lock:
        emit_progress_event(
            "run_finished",
            status="completed" if summary["readiness"]["ready"] else "not_ready",
            readiness=summary["readiness"]["verdict"],
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a 3-model local AgentScope agentic readiness stress test.")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--tasks-per-model", type=int, default=20)
    parser.add_argument("--task-ids", nargs="+", help="Run only the named task ids (used for focused rechecks).")
    parser.add_argument("--per-model-concurrency", type=int, default=3)
    parser.add_argument("--global-concurrency", type=int, default=9)
    parser.add_argument("--max-iters", type=int, default=12)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.tasks_per_model <= 20:
        raise SystemExit("--tasks-per-model must be between 1 and 20")
    if args.per_model_concurrency < 1 or args.global_concurrency < 1:
        raise SystemExit("concurrency must be positive")
    if args.max_iters < 1:
        raise SystemExit("--max-iters must be positive")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be finite and positive")
    try:
        validated_models(args.models)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.fresh and args.resume:
        raise SystemExit("--fresh and --resume are mutually exclusive")
    try:
        summary = asyncio.run(execute(args))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["readiness"]["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
