from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
MAIN_HARNESS_ROOT = CANDIDATE_ROOT.parents[1]
PROJECT_ROOT = MAIN_HARNESS_ROOT.parent
sys.path.insert(0, str(CANDIDATE_ROOT / "src"))

from pawbench_agentscope.features import FeatureConfig, TAXONOMY_VERSION  # noqa: E402
from pawbench_agentscope.models import TaskSpec  # noqa: E402
from pawbench_agentscope.runtime.agentscope_runner import dashscope_model_from_env, run_task  # noqa: E402


DEFAULT_MODELS = ("qwen3.7-max", "deepseek-v4-pro", "kimi-k2.6")
DEFAULT_OUT = PROJECT_ROOT / "harness_ablation_runs" / "HARNESS_CORE_STRESS_TESTING_20260714"


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temp.replace(path)


def model_slug(model_name: str) -> str:
    return model_name.replace("/", "__")


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def artifact_hashes(workspace: Path, artifacts: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for rel_path in artifacts:
        path = workspace / rel_path
        if path.is_file():
            data = path.read_bytes()
            values[rel_path] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
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


def load_results(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def summarize(records: list[dict[str, Any]], models: list[str], tasks: list[StressTask]) -> dict[str, Any]:
    expected_keys = {(model, task.task_id) for model in models for task in tasks}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        latest[(str(record.get("model")), str(record.get("task_id")))] = record
    selected = [latest[key] for key in sorted(expected_keys) if key in latest]

    def group_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
        durations = [float(item.get("duration_seconds", 0.0)) for item in items]
        accepted = sum(bool(item.get("accepted")) for item in items)
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
            "p95_duration_seconds": round(sorted(durations)[max(0, int(len(durations) * 0.95) - 1)], 3)
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
    out_root = Path(args.out_dir).expanduser().resolve()
    if args.fresh and out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "validators").mkdir(exist_ok=True)

    models = list(args.models)
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
            validator_path = out_root / "validators" / f"{task.task_id}.py"
            validator_path.write_text(task.hidden_validator, encoding="utf-8")

    atomic_json(
        out_root / "manifest.json",
        {
            "schema_version": 1,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "purpose": "HARNESS_CORE_STRESS_TESTING",
            "candidate": "AgentScope",
            "taxonomy_version": TAXONOMY_VERSION,
            "models": models,
            "tasks_per_model": len(tasks),
            "expected_runs": len(models) * len(tasks),
            "per_model_concurrency": args.per_model_concurrency,
            "global_concurrency": args.global_concurrency,
            "max_iters": args.max_iters,
            "timeout_seconds": args.timeout_seconds,
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
        },
    )
    atomic_json(
        out_root / "tasks.json",
        [
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
        ],
    )

    results_path = out_root / "results.jsonl"
    existing = load_results(results_path) if args.resume else []
    completed = {
        (str(item.get("model")), str(item.get("task_id")))
        for item in existing
        if item.get("status") != "exception"
    }
    write_lock = asyncio.Lock()
    global_semaphore = asyncio.Semaphore(args.global_concurrency)
    model_semaphores = {model: asyncio.Semaphore(args.per_model_concurrency) for model in models}
    progress = {"done": len(completed), "target": len(models) * len(tasks)}

    async def run_one(model_name: str, task: StressTask) -> dict[str, Any] | None:
        key = (model_name, task.task_id)
        if key in completed:
            return None
        async with global_semaphore, model_semaphores[model_name]:
            run_root = out_root / model_slug(model_name) / task.task_id
            workspace = run_root / "workspace"
            if run_root.exists():
                shutil.rmtree(run_root)
            workspace.mkdir(parents=True)
            for rel_path, content in task.fixtures.items():
                safe_write(workspace, rel_path, content)
            validator_command = None
            if task.hidden_validator:
                validator_path = out_root / "validators" / f"{task.task_id}.py"
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
            trace_path = run_root / "trace.jsonl"
            started = time.monotonic()
            record: dict[str, Any]
            try:
                config = FeatureConfig.all_enabled().model_copy(
                    update={"runtime_timeout_seconds": float(args.timeout_seconds)}
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
                rows = read_trace(trace_path)
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
                    "error": f"{type(exc).__name__}: {exc}",
                }
            record["recorded_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            atomic_json(run_root / "result.json", record)
            async with write_lock:
                with results_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                progress["done"] += 1
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
    (out_root / "REPORT.md").write_text(render_report(summary, out_root), encoding="utf-8")
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
    if args.fresh and args.resume:
        raise SystemExit("--fresh and --resume are mutually exclusive")
    summary = asyncio.run(execute(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["readiness"]["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
