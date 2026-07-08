#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pawbench runner  ·  unified entry point for running pawbench tasks

Quick start
-----------

  # Run all tasks with the qwenpaw agent (default):
  python run_bench.py --model openai/gpt-4o

  # Run with OpenClaw agent (copawbench-openclaw:latest):
  python run_bench.py --agents openclaw --model dashscope/qwen3.6-plus

  # Run with Hermes agent (hermes-qwenclawbench:latest):
  python run_bench.py --agents hermes --model dashscope/qwen3.6-plus

  # Compare all three agents on the same tasks:
  python run_bench.py --agents qwenpaw openclaw hermes --model dashscope/qwen3.6-plus --tasks T002_email_triage

  # Run a Harbor agent (harbor-framework must be installed; use pawbench-base image):
  python run_bench.py --agents harbor:hermes  --model anthropic/claude-sonnet-4-5
  python run_bench.py --agents harbor:codex   --model openai/o3
  python run_bench.py --agents harbor:aider   --model anthropic/claude-opus-4-5

  # Compare a built-in agent against its Harbor counterpart:
  python run_bench.py --agents hermes harbor:hermes --model anthropic/claude-sonnet-4-5

  # Run a specific subset of tasks:
  python run_bench.py --model openai/gpt-4o --tasks T001 T002

  # Run with a custom benchmark path:
  python run_bench.py --benchmark-path /my/pawbench --model openai/gpt-4o

  # Run multiple models sequentially:
  python run_bench.py --model openai/gpt-4o --model anthropic/claude-sonnet-4-6

  # Enable verbose output:
  python run_bench.py --model openai/gpt-4o --verbose

  Results are written under
  ``./results/<YYYYMMDD_HHMMSS>/pawbench/<model>/<agent>/``
  so all agents/models from the same invocation share a single run folder and repeated runs do not
  overwrite each other. Use ``--no-results-version-path`` to omit the timestamp prefix (re-runs
  overwrite previous results).

Environment variables
---------------------
  OPENAI_MODEL      Fallback model when --model is not specified
  OPENAI_API_KEY    API key (overridden by --api-key)
  OPENAI_BASE_URL   Base URL (overridden by --base-url)
  JUDGE_API_KEY     API key for the LLM judge (defaults to OPENAI_API_KEY)
  JUDGE_BASE_URL    Base URL for the LLM judge (defaults to OPENAI_BASE_URL)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Load .env from repo root before anything else so that JUDGE_API_KEY,
# JUDGE_BASE_URL, DASHSCOPE_API_KEY etc. are available to os.environ.
# Existing shell exports take priority (override=False).
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass

from pawbench import BenchmarkRunner, PawBenchBackend

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_BENCHMARK_PATH = _SCRIPT_DIR
_BENCHMARK_NAME = "pawbench"


# ── argument parsing ───────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="pawbench — AI agent benchmark runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    bench_grp = p.add_argument_group("Benchmark selection")
    bench_grp.add_argument(
        "--benchmark-path",
        type=Path,
        default=None,
        metavar="DIR",
        help=f"Root directory of the benchmark data (default: {_DEFAULT_BENCHMARK_PATH}).",
    )
    bench_grp.add_argument(
        "--dataset",
        default=None,
        metavar="NAME",
        help=(
            "Dataset name inside <benchmark_path>/data/. "
            "Default: pawbench-v1.0 (pawbench backend) or Pawbenchv2_task_0706 "
            "(harbor-v2 backend)."
        ),
    )
    bench_grp.add_argument(
        "--backend",
        choices=("auto", "pawbench", "harbor-v2"),
        default="auto",
        help=(
            "Execution backend. 'pawbench' = legacy Markdown tasks graded on the "
            "host. 'harbor-v2' = Harbor-native task packages (task.toml + tests/) "
            "run via Harbor's Trial runner (per-task Docker env + RewardKit "
            "verifier). 'auto' (default) picks harbor-v2 when the dataset "
            "directory contains Harbor task packages, else pawbench."
        ),
    )

    run_grp = p.add_argument_group("Task & agent selection")
    run_grp.add_argument(
        "--agents",
        nargs="+",
        default=None,
        metavar="AGENT",
        help=(
            "Agent(s) to use. Built-in: qwenpaw (default), openclaw, hermes. "
            "Harbor agents: harbor:<name> — runs any Harbor BaseInstalledAgent "
            "inside the pawbench-base Docker image (requires Python 3.12 + harbor-framework). "
            "Examples: harbor:hermes, harbor:codex, harbor:aider, harbor:claude-code, "
            "harbor:gemini-cli, harbor:goose, harbor:qwen-code, harbor:openhands, "
            "harbor:swe-agent, harbor:nemo-agent. "
            "Multiple agents run sequentially: --agents qwenpaw harbor:hermes"
        ),
    )
    run_grp.add_argument(
        "--tasks",
        nargs="+",
        metavar="TASK_ID",
        help="Task IDs to run (default: all tasks in the dataset).",
    )

    exec_grp = p.add_argument_group("Execution options")
    exec_grp.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of tasks to run in parallel (default: 1).",
    )
    exec_grp.add_argument(
        "--runs",
        type=int,
        default=1,
        dest="runs_per_task",
        metavar="N",
        help=(
            "Number of times to run each task (default: 1). "
            "When N>1, results are aggregated (mean/std/min/max per task, pass@k). "
            "Mirrors the --runs option of the original QwenClawBench runner."
        ),
    )
    exec_grp.add_argument(
        "--max-retries",
        type=int,
        default=3,
        dest="max_retries",
        metavar="N",
        help="Maximum attempts per task on infrastructure failure (default: 3).",
    )
    exec_grp.add_argument(
        "--timeout-multiplier",
        type=float,
        default=1.0,
        dest="timeout_multiplier",
        help="Scale all task timeouts by this factor (default: 1.0).",
    )
    exec_grp.add_argument(
        "--docker-image",
        default=None,
        help=(
            "Docker image override. "
            "qwenpaw mode default: qwenclawbench-qwenpaw:latest (pre-built with qwenpaw); "
            "openclaw mode default: ghcr.io/openclaw/openclaw:main."
        ),
    )
    exec_grp.add_argument(
        "--thinking",
        default=None,
        metavar="LEVEL",
        help="[openclaw] Thinking level: low | medium | high | xhigh.",
    )
    exec_grp.add_argument(
        "--skip-bootstrap",
        action="store_true",
        default=False,
        dest="skip_bootstrap",
        help="[openclaw] Remove BOOTSTRAP.md / SOUL.md from the task workspace before execution.",
    )

    model_grp = p.add_argument_group("Model & API configuration")
    model_grp.add_argument(
        "--model",
        action="append",
        default=None,
        metavar="MODEL_ID",
        help=(
            "LLM model identifier, e.g. 'openai/gpt-4o', "
            "'anthropic/claude-sonnet-4-6', 'dashscope/qwen3.6-plus'. "
            "Can be passed multiple times to run multiple models sequentially. "
            "Falls back to $OPENAI_MODEL."
        ),
    )
    model_grp.add_argument(
        "--api-key",
        action="append",
        default=None,
        dest="api_key",
        help="API key (overrides $OPENAI_API_KEY).",
    )
    model_grp.add_argument(
        "--base-url",
        action="append",
        default=None,
        dest="base_url",
        help="Custom OpenAI-compatible API base URL (overrides $OPENAI_BASE_URL).",
    )

    judge_grp = p.add_argument_group("Judge configuration")
    judge_grp.add_argument(
        "--judge",
        default=None,
        metavar="MODEL_ID",
        help="Judge model identifier (default: $JUDGE_MODEL env var, then claude-opus-4-5-20251101).",
    )
    judge_grp.add_argument(
        "--judge-api-key",
        default=None,
        dest="judge_api_key",
        help="API key for the LLM judge (falls back to $JUDGE_API_KEY, then --api-key).",
    )
    judge_grp.add_argument(
        "--judge-base-url",
        default=None,
        dest="judge_base_url",
        help="Base URL for the LLM judge endpoint (falls back to $JUDGE_BASE_URL, then --base-url).",
    )
    judge_grp.add_argument(
        "--judge-timeout",
        type=float,
        default=300.0,
        dest="judge_timeout",
        help="HTTP timeout (seconds) for each LLM judge API call (default: 300).",
    )

    out_grp = p.add_argument_group("Results")
    out_grp.add_argument(
        "--results-dir",
        type=Path,
        default=Path("./results"),
        dest="results_dir",
        help=(
            "Base directory for results (default: ./results). "
            "Actual output: <dir>/<timestamp>/pawbench/<model>/<agent>/ "
            "unless --no-results-version-path is set."
        ),
    )
    out_grp.add_argument(
        "--no-results-version-path",
        action="store_true",
        dest="no_results_version_path",
        help=(
            "Write under <results-dir>/pawbench/<model>/<agent>/ only (no timestamp subfolder; "
            "re-runs overwrite previous results)."
        ),
    )
    out_grp.add_argument(
        "--save-workspace",
        action="store_true",
        default=False,
        dest="save_workspace",
        help=(
            "Save the agent workspace (AGENT_WORKSPACE container contents) to "
            "<results-dir>/workspaces/<task_id>/ after each task completes."
        ),
    )
    out_grp.add_argument(
        "--save-docker-image",
        action="store_true",
        default=False,
        dest="save_docker_image",
        help=(
            "After each task completes, commit the Docker container and export it as a "
            ".tar image to <results-dir>/docker_images/<task_id>.tar."
        ),
    )

    misc_grp = p.add_argument_group("Miscellaneous")
    misc_grp.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output from the benchmark runner.",
    )

    return p.parse_args()


# ── helpers ────────────────────────────────────────────────────────────────────

def _sanitize_model_id(model_id: str) -> str:
    safe = []
    for ch in model_id.strip():
        if ch.isalnum() or ch in ("-", "_", ".", "+"):
            safe.append(ch)
        elif ch in ("/", ":", "@"):
            safe.append("-")
        else:
            safe.append("_")
    return "".join(safe).strip("-_") or "model"


def _filesystem_model_label(model_id: str) -> str:
    """Strip ``provider/model`` → ``model`` for directory names."""
    s = model_id.strip()
    if "/" in s:
        s = s.split("/", 1)[1]
    return _sanitize_model_id(s)


def _run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _looks_like_harbor_v2(dataset_root: Path) -> bool:
    """True when *dataset_root* contains Harbor-native task packages.

    Detects a ``task.toml`` marker up to two levels deep (handles both
    ``<dataset>/<task>/`` and ``<dataset>/data_v2/<task>/`` layouts).
    """
    if not dataset_root.is_dir():
        return False
    for child in dataset_root.iterdir():
        if not child.is_dir():
            continue
        if (child / "task.toml").is_file():
            return True
        for grandchild in child.iterdir():
            if grandchild.is_dir() and (grandchild / "task.toml").is_file():
                return True
    return False


def _select_backend(backend_choice: str, benchmark_path: Path, dataset: str | None):
    """Instantiate the requested (or auto-detected) benchmark backend.

    Returns a tuple ``(backend, load_kwargs)`` where ``load_kwargs`` carries the
    resolved dataset name for ``runner.run(**load_kwargs)``.
    """
    from pawbench import get_harbor_v2_backend

    choice = backend_choice
    if choice == "auto":
        # Probe the explicit dataset first; fall back to the harbor-v2 default.
        HarborV2 = get_harbor_v2_backend()
        probe_ds = dataset or HarborV2.DEFAULT_DATASET
        if _looks_like_harbor_v2(benchmark_path / "data" / probe_ds):
            choice = "harbor-v2"
        else:
            choice = "pawbench"

    load_kwargs: dict = {}
    if choice == "harbor-v2":
        HarborV2 = get_harbor_v2_backend()
        backend = HarborV2(benchmark_path)
        load_kwargs["dataset"] = dataset or HarborV2.DEFAULT_DATASET
    else:
        backend = PawBenchBackend(benchmark_path)
        if dataset:
            load_kwargs["dataset"] = dataset
    return backend, load_kwargs, choice


def _default_base_url_for_model(model: str | None) -> str:
    """Return the best default base URL for *model* based on its provider type.

    For ``custom`` provider models (e.g. ``custom/openai.claude-opus-4-6``)
    the ``CUSTOM_BASE_URL`` env var takes precedence over ``OPENAI_BASE_URL``
    so that custom endpoints can coexist with DashScope models in the same
    benchmark run without requiring ``--base-url`` on every invocation.

    For built-in providers, return the provider-specific default URL parsed by
    ``ModelConfigManager`` (e.g. Anthropic, Gemini, DashScope) instead of
    always falling back to ``OPENAI_BASE_URL``.
    """
    _OPENAI_DEFAULT = "https://api.openai.com/v1"
    if model:
        try:
            from pawbench.llm.model_config import ModelConfigManager, ProviderType
            cfg = ModelConfigManager.parse_model_identifier(model)
            if cfg.provider == ProviderType.CUSTOM:
                return (
                    os.environ.get("CUSTOM_BASE_URL")
                    or os.environ.get("OPENAI_BASE_URL", _OPENAI_DEFAULT)
                )
            if cfg.provider == ProviderType.ANTHROPIC:
                # Prefer ANTHROPIC_BASE_URL from env (e.g. a relay/proxy) over the
                # hardcoded api.anthropic.com default so that proxy setups work
                # without passing --base-url on every invocation.
                return (
                    os.environ.get("ANTHROPIC_BASE_URL")
                    or cfg.base_url
                )
            if cfg.base_url and cfg.base_url != _OPENAI_DEFAULT:
                # Use the provider-specific URL only when it's not the generic
                # OpenAI default.  When it IS the default, prefer $OPENAI_BASE_URL
                # so DashScope-compatible endpoints (openai/qwen*) work correctly
                # without requiring --base-url on every invocation.
                return cfg.base_url
        except Exception:
            pass
    return os.environ.get("OPENAI_BASE_URL", _OPENAI_DEFAULT)


def _default_api_key_for_model(model: str | None) -> str | None:
    """Return the best default API key for *model* based on its provider type.

    For ``custom`` provider models the ``CUSTOM_API_KEY`` env var takes
    precedence, allowing a different key without ``--api-key`` every time.
    For ``anthropic`` provider models, ``ANTHROPIC_API_KEY`` is preferred so
    that proxy/relay configurations work without explicit ``--api-key``.
    """
    if model:
        try:
            from pawbench.llm.model_config import ModelConfigManager, ProviderType
            cfg = ModelConfigManager.parse_model_identifier(model)
            if cfg.provider == ProviderType.CUSTOM:
                return (
                    os.environ.get("CUSTOM_API_KEY")
                    or os.environ.get("OPENAI_API_KEY")
                )
            if cfg.provider == ProviderType.ANTHROPIC:
                return (
                    os.environ.get("ANTHROPIC_API_KEY")
                    or os.environ.get("OPENAI_API_KEY")
                )
        except Exception:
            pass
    return os.environ.get("OPENAI_API_KEY")


def _resolve_per_model_values(
    models: list[str | None],
    api_keys: list[str] | None,
    base_urls: list[str] | None,
) -> tuple[list[str | None], list[str | None], list[str]]:
    n = len(models)

    def normalize(values: list[str] | None, name: str) -> list[str | None]:
        if not values:
            return [None] * n
        if len(values) == 1:
            return [values[0]] * n
        if len(values) == n:
            return list(values)
        raise ValueError(
            f"{name} must be provided either once or {n} times to match --model entries."
        )

    norm_keys = normalize(api_keys, "--api-key")
    norm_base_urls_raw = normalize(base_urls, "--base-url")
    norm_base_urls: list[str] = [
        bu or _default_base_url_for_model(m)
        for m, bu in zip(models, norm_base_urls_raw)
    ]
    return models, norm_keys, norm_base_urls


# ── main ───────────────────────────────────────────────────────────────────────

async def main() -> int:
    args = parse_args()

    models: list[str | None]
    if args.model:
        models = list(args.model)
    else:
        models = [os.environ.get("OPENAI_MODEL")]

    try:
        models, api_keys, base_urls = _resolve_per_model_values(
            models=models, api_keys=args.api_key, base_urls=args.base_url,
        )
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    env_api_key = os.environ.get("OPENAI_API_KEY")
    api_keys = [k if k is not None else _default_api_key_for_model(m) for k, m in zip(api_keys, models)]

    benchmark_path = args.benchmark_path or _DEFAULT_BENCHMARK_PATH
    if not benchmark_path.exists():
        print(
            f"Error: benchmark directory not found: {benchmark_path}"
        )
        return 1

    if any(m is None for m in models):
        print("Error: --model is required (or set $OPENAI_MODEL).")
        return 1

    base_results_dir = Path(args.results_dir)
    agents = args.agents or ["qwenpaw"]

    run_ts = _run_timestamp()

    # Build full run matrix: models × agents
    run_matrix = [
        (model, api_key, base_url, agent)
        for (model, api_key, base_url) in zip(models, api_keys, base_urls, strict=True)
        for agent in agents
    ]
    total = len(run_matrix)

    for idx, (model, api_key, base_url, agent_label) in enumerate(run_matrix, start=1):
        model_label = _filesystem_model_label(model or "default")
        if args.no_results_version_path:
            run_results_dir = base_results_dir / _BENCHMARK_NAME / model_label / agent_label
        else:
            run_results_dir = (
                base_results_dir / run_ts / _BENCHMARK_NAME / model_label / agent_label
            )
        run_results_dir.mkdir(parents=True, exist_ok=True)

        print("\n" + "─" * 80)
        print(f"[{idx}/{total}] Model: {model}  Agent: {agent_label}")
        print(f"Results dir: {run_results_dir}")
        print("─" * 80 + "\n")

        run_args = argparse.Namespace(**vars(args))
        run_args.results_dir = run_results_dir

        rc = await _run_benchmark(run_args, model, api_key, base_url, benchmark_path, agent_label)
        if rc != 0:
            return rc

    return 0


async def _run_benchmark(
    args: argparse.Namespace,
    model: str,
    api_key: str | None,
    base_url: str,
    benchmark_path: Path,
    agent_label: str = "qwenpaw",
) -> int:
    print(f"Benchmark : {_BENCHMARK_NAME}")
    print(f"Path      : {benchmark_path}")
    print(f"Model     : {model}")
    print(f"Agent     : {agent_label}")

    backend, load_kwargs, backend_choice = _select_backend(
        getattr(args, "backend", "auto"), benchmark_path, args.dataset,
    )
    print(f"Backend   : {backend_choice}")

    agent_config: dict = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "timeout_multiplier": args.timeout_multiplier,
        "verbose": args.verbose,
        "docker_image": args.docker_image,
        "agent_type": agent_label,
    }

    if args.judge:
        agent_config["judge_model"] = args.judge
    elif os.environ.get("JUDGE_MODEL"):
        agent_config["judge_model"] = os.environ["JUDGE_MODEL"]

    resolved_judge_api_key = (
        getattr(args, "judge_api_key", None)
        or os.environ.get("JUDGE_API_KEY")
        or api_key
    )
    resolved_judge_base_url = (
        getattr(args, "judge_base_url", None)
        or os.environ.get("JUDGE_BASE_URL")
        or base_url
    )
    if resolved_judge_api_key:
        agent_config["judge_api_key"] = resolved_judge_api_key
    if resolved_judge_base_url:
        agent_config["judge_base_url"] = resolved_judge_base_url

    agent_config["judge_timeout_seconds"] = getattr(args, "judge_timeout", 300.0)

    if args.thinking:
        agent_config["thinking_level"] = args.thinking
    # Resolved dataset comes from the backend selector (handles harbor-v2 default).
    resolved_dataset = load_kwargs.get("dataset") or args.dataset
    if resolved_dataset:
        agent_config["dataset"] = resolved_dataset
    api_model_name = os.environ.get("BENCH_API_MODEL_NAME")
    if api_model_name:
        agent_config["api_model_name"] = api_model_name
    if getattr(args, "skip_bootstrap", False):
        agent_config["skip_bootstrap"] = True
    if getattr(args, "save_workspace", False):
        agent_config["save_workspace"] = True
    if getattr(args, "save_docker_image", False):
        agent_config["save_docker_image"] = True
    if backend_choice == "harbor-v2":
        # Harbor bind-mounts the trial's agent-logs dir into the task container.
        # Under docker-socket sharing the path must exist on the host, so anchor
        # trials under the (host-visible) results dir rather than a container tmp.
        agent_config["trials_dir"] = str(Path(args.results_dir) / "trials")

    runner = BenchmarkRunner(
        backend=backend,
        results_dir=args.results_dir,
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        runs_per_task=getattr(args, "runs_per_task", 1),
    )
    results = await runner.run(
        agent_config=agent_config,
        task_filter=args.tasks,
        **load_kwargs,
    )
    print(f"\nDone. {len(results)} result(s) written to {args.results_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
