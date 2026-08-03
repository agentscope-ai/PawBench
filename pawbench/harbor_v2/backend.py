"""HarborV2Backend — run + grade Harbor-native (v2) tasks via Harbor's Trial.

This backend keeps pawbench's orchestration/reporting (``BenchmarkRunner`` →
pass@k / label report / anomaly detection / result JSON) while delegating the
*execution and grading* of each task to Harbor's own :class:`~harbor.trial.trial.Trial`:

    load_tasks()      → discover v2 task dirs (task.toml + instruction.md + tests/)
    run_and_grade()   → build a TrialConfig, run the Trial in-process, then map
                        TrialResult.verifier_result.rewards → TaskResult.score,
                        the ATIF trajectory → TaskResult.transcript, and
                        compute_token_cost_totals() → TaskResult.usage.

Runtime requirements (satisfied by ``pawbench-base:latest``):
    * Python ≥ 3.12 and ``harbor-framework`` importable.
    * Docker available (Harbor builds each task's ``environment/Dockerfile`` and
      runs a separate RewardKit verifier container).

The ``harbor`` import is deferred to :meth:`run_and_grade` so this module (and
task discovery) load fine on hosts without harbor installed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any

from pawbench.harbor_v2.delegation import evaluate_multi_agent_run
from pawbench.harbor_v2.multi_agent import (
    FORCED_DELEGATION_INSTRUCTION,
    MultiAgentConfig,
    resolve_for_harness,
)
from pawbench.backend import BenchmarkBackend, TaskResult
from pawbench.tools.enrich_trajectories import enrich_file
from pawbench.utils.anomalies import detect_anomalies

from .generative_user import (
    build_generative_user_image,
    generative_user_image_name,
    is_generative_user_task,
    materialize_generative_task,
)
from .scripted_user import (
    build_scripted_user_image,
    is_scripted_multi_turn,
    materialize_scripted_task,
    scripted_user_image_name,
)
from .task_loader import HarborV2Loader, HarborV2Task
from .verifier import materialize_openjudge_task, uses_openjudge

logger = logging.getLogger(__name__)


# Aliases: pawbench ``harbor:<name>`` values → Harbor AgentName registry names.
# Most names match verbatim; only the handful that diverge are listed here.
_AGENT_NAME_ALIASES: dict[str, str] = {
    "qwen-code": "qwen-coder",
}

# Fallback import paths for installed agents that ship in the image but are NOT
# registered in Harbor's ``AgentName`` enum (so the native Trial AgentFactory
# rejects them by name).  Passing a ``module:Class`` import path bypasses the
# enum check — see harbor.agents.factory.create_agent_from_config.
_AGENT_IMPORT_PATHS: dict[str, str] = {
    "claude-code": "harbor.agents.installed.claude_code:ClaudeCode",
    "hermes": "harbor.agents.installed.hermes:Hermes",
    "qwenpaw": "harbor.agents.installed.qwenpaw:QwenPaw",
}

_QWENPAW_SETUP_TIMEOUT_SECONDS = 1500.0
_HERMES_SETUP_TIMEOUT_SECONDS = 1200.0


class HarborV2Backend(BenchmarkBackend):
    """Run and grade Harbor-native (v2) tasks through Harbor's Trial runner."""

    DEFAULT_DATASET = "Pawbenchv2_task_0706"

    @property
    def name(self) -> str:
        return "pawbench"

    # ── task discovery ─────────────────────────────────────────────────────────

    def _dataset_root(self, dataset: str | None) -> Path:
        ds = dataset or self.DEFAULT_DATASET
        root = self.benchmark_path / "data" / ds
        if not root.exists():
            raise FileNotFoundError(
                f"Harbor v2 dataset directory not found: {root}\n"
                f"Expected: <benchmark_path>/data/{ds}/ containing task packages "
                f"(task.toml + instruction.md), optionally nested under data_v2/."
            )
        return root

    def load_tasks(
        self,
        task_filter: list[str] | None = None,
        dataset: str | None = None,
        **_kwargs: Any,
    ) -> list[Any]:
        loader = HarborV2Loader(self._dataset_root(dataset))
        tasks = loader.load_all_tasks()
        if task_filter:

            def _matches(t: HarborV2Task, filters: list[str]) -> bool:
                for f in filters:
                    if t.task_id == f or t.task_id.startswith(f):
                        return True
                return False

            tasks = [t for t in tasks if _matches(t, task_filter)]
        return tasks

    # ── run + grade ─────────────────────────────────────────────────────────────

    def run_and_grade(
        self,
        task: Any,
        agent_config: dict[str, Any],
    ) -> TaskResult:
        timeout_multiplier = float(agent_config.get("timeout_multiplier", 1.0))
        hard_limit = int(getattr(task, "timeout_seconds", 1200) * timeout_multiplier) + 600
        t0 = time.time()
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._run_trial_async(task, agent_config),
                    timeout=hard_limit,
                )
            )
        except TimeoutError:
            return self._error_result(
                task,
                f"Trial exceeded hard wall-clock limit of {hard_limit}s",
                elapsed=time.time() - t0,
                timed_out=True,
            )
        except Exception as exc:  # noqa: BLE001
            import traceback

            return self._error_result(
                task,
                f"{exc}\nTraceback:\n{traceback.format_exc()}",
                elapsed=time.time() - t0,
            )

    async def _run_trial_async(
        self,
        task: HarborV2Task,
        agent_config: dict[str, Any],
    ) -> TaskResult:
        # Deferred harbor import — only needed when actually running a trial.
        from harbor.models.agent.name import AgentName
        from harbor.models.trial.config import (
            AgentConfig,
            EnvironmentConfig,
            TaskConfig,
            TrialConfig,
            VerifierConfig,
        )
        from harbor.trial.trial import Trial

        short_agent_name = self._resolve_agent_name(agent_config.get("agent_type", ""))
        agent_name = short_agent_name
        # Names not in Harbor's AgentName enum must be passed as a module:Class
        # import path (e.g. the pawbench-bundled qwenpaw agent).
        if agent_name in _AGENT_IMPORT_PATHS:
            agent_name = _AGENT_IMPORT_PATHS[agent_name]
        elif agent_name not in AgentName.values():
            agent_name = _AGENT_IMPORT_PATHS.get(agent_name, agent_name)
        model = agent_config.get("model", "")
        verbose = bool(agent_config.get("verbose", False))
        raw_multi_agent = agent_config.get("multi_agent")
        multi_agent_cfg = resolve_for_harness(
            (
                raw_multi_agent
                if isinstance(raw_multi_agent, MultiAgentConfig)
                else MultiAgentConfig.from_dict(raw_multi_agent)
            ),
            short_agent_name,
        )
        if (
            multi_agent_cfg.requested_mode != "single"
            and multi_agent_cfg.effective_mode == "single"
        ):
            logger.warning(
                "Harbor-v2 agent '%s' does not support multi-agent execution; "
                "requested mode '%s' falls back to single.",
                short_agent_name,
                multi_agent_cfg.requested_mode,
            )
        agent_config = {
            **agent_config,
            "multi_agent": multi_agent_cfg.to_dict(),
        }
        requires_user_sim = self._requires_user_sim(task)
        self._validate_user_sim_wiring(task)

        # Extra kwargs forwarded to the native agent constructor via
        # AgentConfig.kwargs (harbor.agents.factory merges these into the
        # agent's __init__). OpenClaw's "thinking" CliFlag defaults to "high",
        # which non-reasoning models (e.g. DashScope qwen3.6-plus) reject
        # outright ("Thinking level 'high' is not supported ... Use one of:
        # off."), crashing the CLI with an empty transcript. Mirror
        # force "off" unless the caller explicitly passed
        # --thinking (agent_config["thinking_level"]).
        agent_kwargs: dict[str, Any] = {}
        if agent_config.get("version"):
            agent_kwargs["version"] = str(agent_config["version"])
        if short_agent_name == "openclaw":
            agent_kwargs["thinking"] = agent_config.get("thinking_level") or "off"
        multi_agent_kwargs, multi_agent_env = self._build_multi_agent_inputs(
            short_agent_name, agent_config
        )
        agent_kwargs.update(multi_agent_kwargs)

        # MUST be an absolute path: Harbor bind-mounts the trial's workspace /
        # artifacts dirs into the task & verifier containers, and under
        # docker-socket sharing (DinD) the host daemon resolves those mounts.
        # A relative trials_dir yields a non-host-visible mount source, so the
        # agent's declared artifacts (e.g. /app/summary_*.md) never reach the
        # separate verifier — silently scoring structure=0 / reward=0.
        trials_dir = Path(
            agent_config.get("trials_dir") or tempfile.mkdtemp(prefix=f"harborv2_{task.task_id}_")
        ).resolve()
        run_id = agent_config.get("run_id") or uuid.uuid4().hex[:8]
        # Docker Compose uses the trial name as its project name. Repeated or
        # concurrent benchmark runs commonly reuse ``<task>_r1``; a unique
        # suffix keeps every harness/mode/retry from sharing and tearing down
        # another trial's containers.
        trial_name = f"{task.task_id}__{run_id}__{uuid.uuid4().hex[:8]}"
        # Single source of truth for the agent-under-test's workspace path:
        # reused below for artifact collection and here so the cowork user-sim
        # sidecar mounts the shared workspace volume at the *same* absolute
        # path the agent sees, instead of a hardcoded "/workspace" that can
        # diverge from it and make authored patch hints look like they
        # "escape" the sidecar's workspace root.
        agent_workspace_path = str(agent_config.get("workspace_path") or "/home/node/workspace")
        runtime_task_dir = task.task_dir
        if self._uses_generative_user(task):
            trials_dir.mkdir(parents=True, exist_ok=True)
            runtime_task_dir, server_dir = materialize_generative_task(
                task,
                trials_dir / ".pawbench-runtime-tasks" / trial_name,
                workspace_path=agent_workspace_path,
            )
            image_name = generative_user_image_name(server_dir)
            await asyncio.to_thread(
                build_generative_user_image,
                server_dir,
                image_name,
            )
            logger.info(
                "Materialized generative multi-turn wrapper for %s at %s (image=%s)",
                task.task_id,
                runtime_task_dir,
                image_name,
            )
        elif self._uses_scripted_user(task):
            trials_dir.mkdir(parents=True, exist_ok=True)
            runtime_task_dir = materialize_scripted_task(
                task,
                trials_dir / ".pawbench-runtime-tasks" / trial_name,
            )
            image_name = scripted_user_image_name(runtime_task_dir)
            await asyncio.to_thread(
                build_scripted_user_image,
                runtime_task_dir,
                image_name,
            )
            logger.info(
                "Materialized scripted multi-turn wrapper for %s at %s (image=%s)",
                task.task_id,
                runtime_task_dir,
                image_name,
            )

        if multi_agent_cfg.effective_mode == "forced":
            runtime_task_dir = self._materialize_forced_task(
                runtime_task_dir,
                trials_dir,
                trial_name,
            )

        try:
            dataset_id = str(task.task_dir.parent.relative_to(self.benchmark_path / "data"))
        except ValueError:
            dataset_id = task.task_dir.parent.name
        run_provenance = {
            "schema_version": 1,
            "dataset": dataset_id,
            "task_id": task.task_id,
            "task_config_sha256": hashlib.sha256(
                json.dumps(
                    task.raw_config,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "run_id": str(run_id),
            "agent": {
                "name": short_agent_name,
                "model": model,
                "version": agent_config.get("version"),
            },
            "judge": {
                "framework": (
                    "openjudge" if uses_openjudge(runtime_task_dir) else "rewardkit"
                ),
                "model": agent_config.get("judge_model"),
                "api_format": agent_config.get("judge_api_format"),
            },
            "multi_agent_mode": multi_agent_cfg.effective_mode,
        }
        if uses_openjudge(runtime_task_dir):
            runtime_task_dir = materialize_openjudge_task(
                runtime_task_dir,
                trials_dir / ".pawbench-runtime-tasks" / f"{trial_name}-openjudge",
                provenance=run_provenance,
            )
            logger.info(
                "Injected centralized OpenJudge verifier for %s at %s",
                task.task_id,
                runtime_task_dir,
            )

        # Environment routing depends on the public harness name, not a custom
        # ``module:Class`` import path used by Harbor's AgentFactory.
        agent_env = self._build_agent_env(short_agent_name, model, agent_config)
        agent_env.update(multi_agent_env)
        verifier_env = self._build_verifier_env(agent_config)
        # Multi-turn tasks (Strategy A) run a user-sim MCP sidecar in the
        # environment; inject its dedicated USER_SIM_* credentials so the compose
        # sidecar service can reach its LLM. Fail-fast when a multi-turn task is
        # missing the dedicated creds — the user simulator must never silently
        # fall back to the agent-under-test / judge credentials.
        environment_env = self._build_environment_env(task, agent_config)

        timeout_multiplier = float(agent_config.get("timeout_multiplier", 1.0))

        artifacts = []
        if agent_config.get("save_workspace"):
            from harbor.models.task.config import ArtifactConfig

            artifacts.append(
                ArtifactConfig(
                    source=agent_workspace_path,
                    destination="workspace",
                )
            )

        keep_docker = agent_config.get("keep_docker", False)
        env_kwargs: dict[str, Any] = {}
        if keep_docker:
            env_kwargs["keep_containers"] = True
        config = TrialConfig(
            task=TaskConfig(path=runtime_task_dir),
            trial_name=trial_name,
            trials_dir=trials_dir,
            timeout_multiplier=timeout_multiplier,
            agent=AgentConfig(
                name=agent_name,
                model_name=model,
                env=agent_env,
                kwargs=agent_kwargs,
                # QwenPaw creates an isolated venv and may need to download its
                # package in task images that do not include it. Its own install
                # command allows 1200s, so Harbor's generic 360s outer setup
                # timeout must not terminate a healthy installation first.
                override_setup_timeout_sec=self._agent_setup_timeout_seconds(short_agent_name),
            ),
            environment=(
                EnvironmentConfig(env=environment_env, delete=not keep_docker, kwargs=env_kwargs)
                if environment_env
                else EnvironmentConfig(delete=not keep_docker, kwargs=env_kwargs)
            ),
            verifier=VerifierConfig(env=verifier_env) if verifier_env else VerifierConfig(),
            artifacts=artifacts,
        )

        t0 = time.time()
        trial = await Trial.create(config)
        result = await trial.run()
        elapsed = time.time() - t0
        self._save_run_provenance(trials_dir / trial_name, run_provenance)

        task_result = self._map_trial_result(
            task=task,
            result=result,
            trials_dir=trials_dir,
            trial_name=trial_name,
            agent_config=agent_config,
            elapsed=elapsed,
            verbose=verbose,
        )
        # Bundling trajectory/reward/workspace into the public results tree
        # (summary/) is BenchmarkRunner's job (pawbench/runner.py,
        # _save_task_bundle) — it runs after this method returns, which
        # matters for multi-turn (user-sim) tasks: _map_trial_result() above
        # just called _enrich_trajectory_file(), rewriting agent/trajectory.json
        # on disk to fuse in the user-sim dialogue, so the bundle picks up the
        # post-enrichment version instead of freezing in a pre-enrichment one.
        return task_result

    @staticmethod
    def _save_run_provenance(trial_dir: Path, provenance: dict[str, Any]) -> None:
        """Persist the immutable evaluation contract used for this trial."""
        trial_dir.mkdir(parents=True, exist_ok=True)
        (trial_dir / "provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _materialize_forced_task(
        task_dir: Path,
        trials_dir: Path,
        trial_name: str,
    ) -> Path:
        """Copy a Harbor task and prepend the strict delegation instruction."""
        parent = trials_dir / ".pawbench-runtime-tasks"
        parent.mkdir(parents=True, exist_ok=True)
        target = Path(tempfile.mkdtemp(prefix=f"{trial_name}-forced-", dir=parent))
        shutil.copytree(task_dir, target, dirs_exist_ok=True, symlinks=True)
        instruction_path = target / "instruction.md"
        existing = (
            instruction_path.read_text(encoding="utf-8") if instruction_path.is_file() else ""
        )
        instruction_path.write_text(
            FORCED_DELEGATION_INSTRUCTION + existing.lstrip(),
            encoding="utf-8",
        )
        return target

    @staticmethod
    def _agent_setup_timeout_seconds(agent_name: str) -> float | None:
        """Return per-agent setup overrides for cold task environments."""
        if agent_name == "qwenpaw":
            return _QWENPAW_SETUP_TIMEOUT_SECONDS
        if agent_name == "hermes":
            return _HERMES_SETUP_TIMEOUT_SECONDS
        return None

    @staticmethod
    def _build_multi_agent_inputs(
        short_agent_name: str,
        agent_config: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Translate normalized multi-agent config for native Harbor trials.

        Harbor constructs ``TrialConfig`` directly and therefore must forward
        the harness-specific constructor kwargs and environment itself.
        """
        raw_config = agent_config.get("multi_agent")
        if not raw_config:
            return {}, {}

        from pawbench.harbor_v2.multi_agent import (
            SUPPORTED_MULTI_AGENT_HARNESSES,
            MultiAgentConfig,
            build_harbor_kwargs,
        )

        if isinstance(raw_config, MultiAgentConfig):
            config = raw_config
        elif isinstance(raw_config, dict):
            config = MultiAgentConfig.from_dict(raw_config)
        else:
            raise TypeError("agent_config['multi_agent'] must be a mapping or MultiAgentConfig")

        kwargs, env = build_harbor_kwargs(short_agent_name, config)
        if config.enabled and short_agent_name not in SUPPORTED_MULTI_AGENT_HARNESSES:
            logger.warning(
                "Harbor-v2 agent '%s' does not support multi-agent mode; "
                "the configuration has no effect.",
                short_agent_name,
            )
        elif config.enabled:
            logger.info(
                "Harbor-v2 agent '%s': multi-agent mode enabled "
                "(mode=%s, max_agents=%d, max_depth=%d, subagents=%d).",
                short_agent_name,
                config.mode,
                config.max_agents,
                config.max_depth,
                len(config.subagents),
            )
        return kwargs, env

    # ── credential wiring ────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_agent_name(agent_type: str) -> str:
        name = agent_type.split(":", 1)[1] if agent_type.startswith("harbor:") else agent_type
        name = name or "qwenpaw"
        return _AGENT_NAME_ALIASES.get(name, name)

    def _build_agent_env(
        self,
        agent_name: str,
        model: str,
        agent_config: dict[str, Any],
    ) -> dict[str, str]:
        """Build the env dict handed to the Harbor agent (scoped to its container).

        Pick the provider-appropriate API-key/base-url vars from the model
        prefix so the installed agent can reach its LLM without extra
        configuration, plus a claude-code-specific adaptation for proxied
        Anthropic endpoints.
        """
        env: dict[str, str] = {}
        api_key = agent_config.get("api_key") or ""
        base_url = agent_config.get("base_url") or ""
        provider = model.split("/", 1)[0].lower() if "/" in model else ""

        if api_key:
            if provider == "anthropic":
                env["ANTHROPIC_API_KEY"] = api_key
            elif provider == "google":
                env["GOOGLE_API_KEY"] = api_key
            else:
                env["OPENAI_API_KEY"] = api_key
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
            # LiteLLM (used by controller-side agents like terminus-2) reads
            # OPENAI_API_BASE rather than the OpenAI-SDK name OPENAI_BASE_URL.
            env["OPENAI_API_BASE"] = base_url
            if provider == "anthropic":
                env["ANTHROPIC_BASE_URL"] = base_url

        # Pass through commonly-read provider vars already set on the host.
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
            "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY",
            "DASHSCOPE_API_KEY",
            "KIMI_API_KEY",
            "GLM_API_KEY",
        ):
            if key not in env and os.environ.get(key):
                env[key] = os.environ[key]

        if agent_name == "claude-code":
            self._apply_claude_code_proxy_env(env, model)
        return env

    @staticmethod
    def _apply_claude_code_proxy_env(env: dict[str, str], model: str) -> None:
        """Adapt claude-code for a proxied Anthropic (messages-format) endpoint.

        Harbor's native ClaudeCode agent, when a custom ``ANTHROPIC_BASE_URL`` is
        set, keeps the *full* ``provider/model`` string as ``ANTHROPIC_MODEL``.
        Most relay/proxy platforms only recognise the *bare* model id, so we override
        ``ANTHROPIC_MODEL`` and every claude-code model alias with the bare name
        via extra_env (which wins over the agent's own computed env).

        Unlike the bridge path we do NOT set up a socat TLS bridge / /etc/hosts
        override: the native agent routes all traffic (including auth) through
        ``ANTHROPIC_BASE_URL``, so a reachable relay is sufficient and
        ``api.anthropic.com`` is never contacted directly.  ``NODE_TLS_REJECT_UNAUTHORIZED``
        is only forwarded when the host explicitly sets it (e.g. for a
        self-signed relay cert).
        """
        anthropic_base = env.get("ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL", "")
        if not anthropic_base:
            return
        bare_model = model.split("/", 1)[-1] if "/" in model else model
        if not bare_model:
            return
        env["ANTHROPIC_MODEL"] = bare_model
        for alias in (
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "CLAUDE_CODE_SUBAGENT_MODEL",
        ):
            env[alias] = bare_model
        # Ensure the base url is present in extra_env too (belt & suspenders:
        # the agent reads it from os.environ, but forwarding it keeps the
        # container exec env consistent).
        env["ANTHROPIC_BASE_URL"] = anthropic_base
        if os.environ.get("NODE_TLS_REJECT_UNAUTHORIZED"):
            env["NODE_TLS_REJECT_UNAUTHORIZED"] = os.environ["NODE_TLS_REJECT_UNAUTHORIZED"]

    def _build_verifier_env(self, agent_config: dict[str, Any]) -> dict[str, str]:
        """Inject judge credentials for the (separate) verifier's LLM judge.

        The v2 task verifiers read a standardized env contract (see each task's
        ``tests/quality/verifier.py``)::

            MODEL           judge model id            (default claude-sonnet-4-6)
            LLM_API_KEY     API key                   (falls back to ANTHROPIC/OPENAI)
            LLM_BASE_URL    endpoint base             (falls back to ANTHROPIC/OPENAI)
            LLM_API_FORMAT  "anthropic" | "openai"    (default anthropic → /v1/messages)

        Harbor does NOT auto-forward the agent's credentials to a *separate*
        verifier container, and the default API format is ``anthropic`` — so for
        an OpenAI-compatible judge (e.g. ``qwen3.7-max`` via DashScope) we MUST
        set ``LLM_API_FORMAT=openai`` explicitly, otherwise the verifier POSTs to
        ``/v1/messages`` against an OpenAI endpoint and every judge call fails.

        These are injected via ``TrialConfig.verifier.env`` which overrides the
        task's ``[verifier.env]`` declarations at trial time.
        """
        env: dict[str, str] = {}
        judge_api_key = agent_config.get("judge_api_key")
        judge_base_url = agent_config.get("judge_base_url")
        judge_model_raw = agent_config.get("judge_model")

        # The verifiers issue OpenAI-/Anthropic-style calls with the model id
        # sent verbatim as ``"model": <id>`` against ``base_url``. A pawbench
        # ``provider/model`` identifier (e.g. ``openai/qwen3.7-max``) must have
        # its provider prefix stripped, otherwise DashScope-compatible endpoints
        # reject it with ``HTTP 404 model_not_found`` and every judge call fails
        # (scoring a spurious 0.0). Strip only known provider prefixes so real
        # namespaced model ids (e.g. ``meta-llama/…``) are left intact.
        judge_provider = ""
        judge_model = judge_model_raw or ""
        if judge_model and "/" in judge_model:
            prefix, rest = judge_model.split("/", 1)
            if prefix.lower() in {
                "openai",
                "dashscope",
                "anthropic",
                "google",
                "gemini",
                "custom",
                "deepseek",
                "azure",
                "qwen",
            }:
                judge_provider = prefix.lower()
                judge_model = rest

        if judge_model:
            env["MODEL"] = judge_model
        if judge_api_key:
            env["LLM_API_KEY"] = judge_api_key
        if judge_base_url:
            env["LLM_BASE_URL"] = judge_base_url

        api_format = agent_config.get("judge_api_format")
        if not api_format:
            model_l = judge_model.lower()
            base_l = (judge_base_url or "").lower()
            # Anthropic only for Claude models or an explicit Anthropic endpoint;
            # everything else (qwen, gpt, deepseek, …) is OpenAI-compatible.
            is_anthropic = model_l.startswith("claude") or "anthropic" in base_l
            api_format = "anthropic" if is_anthropic else "openai"
        env["LLM_API_FORMAT"] = api_format

        # ── JUDGE_* contract ──────────────────────────────────────────────────
        # Tasks converted from CoPaw (e.g. the ws-*/ua-* llm_judge graders) read
        # a different env contract: JUDGE_MODEL / JUDGE_BASE_URL / JUDGE_API_KEY
        # / JUDGE_PROVIDER. Without these the grader logs
        # "grader model/base_url/api_key not configured" and returns 0.0. Inject
        # them alongside the MODEL/LLM_* contract so both grader styles work.
        if judge_model:
            env["JUDGE_MODEL"] = judge_model
        if judge_base_url:
            env["JUDGE_BASE_URL"] = judge_base_url
        if judge_api_key:
            env["JUDGE_API_KEY"] = judge_api_key
        # JUDGE_PROVIDER reflects the endpoint, not the pawbench wire-format
        # prefix (``openai/`` just means OpenAI-compatible protocol).
        base_l = (judge_base_url or "").lower()
        if "dashscope" in base_l:
            env["JUDGE_PROVIDER"] = "dashscope"
        elif judge_provider:
            env["JUDGE_PROVIDER"] = judge_provider
        else:
            env["JUDGE_PROVIDER"] = "openai"

        # ── OpenJudge / Claude-Code agent-judge contract ──────────────────────
        # ``*-openjudge`` tasks shell out to a coding-agent CLI (default:
        # Claude Code). Those CLIs read ANTHROPIC_* / OPENAI_* directly, and
        # ``run_openjudge.py`` additionally honours REWARDKIT_JUDGE /
        # REWARDKIT_MODEL. Forward host / judge credentials so non-claude
        # agents-under-test still have a working judge harness.
        anthropic_key = (
            os.environ.get("ANTHROPIC_API_KEY") or judge_api_key or os.environ.get("OPENAI_API_KEY")
        )
        anthropic_base = (
            os.environ.get("ANTHROPIC_BASE_URL") or (judge_base_url or "").removesuffix("/v1") or ""
        )
        if anthropic_key:
            env.setdefault("ANTHROPIC_API_KEY", anthropic_key)
        if anthropic_base:
            env.setdefault("ANTHROPIC_BASE_URL", anthropic_base)
        if judge_model:
            env.setdefault("OPENJUDGE_MODEL", judge_model)
        for key in (
            "REWARDKIT_JUDGE",
            "REWARDKIT_MODEL",
            "OPENJUDGE_HARNESS",
            "OPENJUDGE_MODEL",
        ):
            value = os.environ.get(key)
            if value:
                env.setdefault(key, value)
        if judge_model and "REWARDKIT_MODEL" not in env:
            env["REWARDKIT_MODEL"] = judge_model
        return env

    # ── multi-turn / user-sim wiring ──────────────────────────────────────────────

    # task.toml [metadata].mode values that indicate a multi-turn user-sim task.
    _MULTI_TURN_MODES = {"multi-turn", "multi_turn", "multiturn", "cowork"}
    # substrings used to recognise a user-sim MCP server declaration by name/url.
    _USER_SIM_MCP_MARKERS = ("user-sim", "user_sim", "usersim")

    @staticmethod
    def _uses_generative_user(task: HarborV2Task) -> bool:
        """Persona-driven (generative) multi-turn: has ``user/persona.md`` + turns."""
        return is_generative_user_task(task)

    @classmethod
    def _uses_scripted_user(cls, task: HarborV2Task) -> bool:
        """Deterministic replay: multi-turn ``messages.jsonl`` but no persona.

        A persona directory routes the task to the *generative* simulator
        instead, so scripted replay is the fallback for persona-less multi-turn
        tasks only.
        """
        return is_scripted_multi_turn(task) and not cls._uses_generative_user(task)

    @classmethod
    def _uses_runtime_user_sim(cls, task: HarborV2Task) -> bool:
        """Whether PawBench materialises a user-sim sidecar for *task* at runtime."""
        return cls._uses_generative_user(task) or cls._uses_scripted_user(task)

    @classmethod
    def _requires_user_sim(cls, task: HarborV2Task) -> bool:
        """Whether a task actually wires a user-sim sidecar (needs ``USER_SIM_*``).

        Detection is deliberately **narrow** so that ``USER_SIM_*`` injection and
        the fail-fast only fire for tasks that genuinely run a user simulator. A
        task requires the user simulator when **either**:

        * ``[metadata].mode`` is one of the multi-turn modes (``multi-turn`` /
          ``cowork`` / …); **or**
        * a ``[[environment.mcp_servers]]`` entry names/points at a user-sim.
        * the task contains at least two authored user turns in
          ``messages.jsonl`` (wrapped at runtime by PawBench).

        ``[metadata].category`` (e.g. ``user_agent``) and the presence of a
        ``.user/`` / ``user/`` persona directory are intentionally **not** used
        here: existing single-turn ``user_agent`` tasks (e.g. ``ua-cw-wcag-4957``)
        ship a ``user/`` directory *without* any sidecar, so treating them as
        multi-turn would wrongly demand ``USER_SIM_*`` and fail-fast — breaking
        the default dataset. Those signals may still inform the *label* but must
        never drive the fail-fast gate (see design doc §4.4 / §7.2).
        """
        if cls._uses_runtime_user_sim(task):
            return True

        metadata = getattr(task, "metadata", {}) or {}
        mode = str(metadata.get("mode") or "").strip().lower()
        if mode in cls._MULTI_TURN_MODES:
            return True

        return cls._has_user_sim_mcp(task)

    @classmethod
    def _has_user_sim_mcp(cls, task: HarborV2Task) -> bool:
        """Whether the task already declares a user-sim MCP server."""
        raw = getattr(task, "raw_config", {}) or {}
        env_cfg = raw.get("environment", {}) or {}
        for server in env_cfg.get("mcp_servers", []) or []:
            if not isinstance(server, dict):
                continue
            haystack = f"{server.get('name', '')} {server.get('url', '')}".lower()
            if any(marker in haystack for marker in cls._USER_SIM_MCP_MARKERS):
                return True
        return False

    @classmethod
    def _validate_user_sim_wiring(cls, task: HarborV2Task) -> None:
        """Fail before running when metadata requests multi-turn without a sidecar.

        Runtime-wrapped tasks and task-authored user-sim MCP declarations are
        valid.  A metadata-only ``mode=multi-turn`` declaration used to run the
        full trial and fail later with a misleading missing-state violation.
        """
        if not cls._requires_user_sim(task):
            return
        if cls._uses_runtime_user_sim(task) or cls._has_user_sim_mcp(task):
            return
        raise ValueError(
            f"Multi-turn task {task.task_id!r} has no user-sim sidecar wiring. "
            "Provide at least two authored user turns in messages.jsonl so "
            "PawBench can materialize the sidecar, or declare a user-sim MCP "
            "server under [[environment.mcp_servers]]."
        )

    def _build_environment_env(
        self,
        task: HarborV2Task,
        agent_config: dict[str, Any],
    ) -> dict[str, str]:
        """Build the env injected into the environment (sidecar) container(s).

        Only populated for multi-turn tasks. Generative user simulators use a
        dedicated ``USER_SIM_*`` contract with **no** fallback to agent/judge
        credentials; a missing model/key raises. Deterministic ``scripted``
        simulators replay the task's authored ``messages.jsonl`` and therefore
        require no model credentials.
        """
        if not self._requires_user_sim(task):
            return {}

        if self._uses_scripted_user(task):
            env: dict[str, str] = {}
            max_turns = agent_config.get("user_sim_max_turns") or os.environ.get(
                "USER_SIM_MAX_TURNS"
            )
            if max_turns:
                env["USER_SIM_MAX_TURNS"] = str(max_turns)
            return env

        def _resolve(cfg_key: str, env_key: str) -> str:
            return str(agent_config.get(cfg_key) or os.environ.get(env_key, "") or "").strip()

        api_key = _resolve("user_sim_api_key", "USER_SIM_API_KEY")
        model = _resolve("user_sim_model", "USER_SIM_MODEL")
        base_url = _resolve("user_sim_base_url", "USER_SIM_BASE_URL")

        missing = [
            name
            for name, value in (("USER_SIM_API_KEY", api_key), ("USER_SIM_MODEL", model))
            if not value
        ]
        if missing:
            raise ValueError(
                f"Multi-turn task {task.task_id!r} requires the dedicated user-sim "
                f"credentials {missing} (set via agent_config user_sim_* or the "
                f"USER_SIM_* environment). The user simulator must not fall back to "
                f"the agent-under-test / judge credentials."
            )

        env: dict[str, str] = {
            "USER_SIM_API_KEY": api_key,
            "USER_SIM_MODEL": model,
        }
        if base_url:
            env["USER_SIM_BASE_URL"] = base_url

        max_turns = agent_config.get("user_sim_max_turns") or os.environ.get("USER_SIM_MAX_TURNS")
        if max_turns:
            env["USER_SIM_MAX_TURNS"] = str(max_turns)
        temperature = agent_config.get("user_sim_temperature")
        if temperature is None:
            temperature = os.environ.get("USER_SIM_TEMPERATURE")
        if temperature is not None and str(temperature).strip():
            env["USER_SIM_TEMPERATURE"] = str(temperature)
        return env

    # ── result mapping ───────────────────────────────────────────────────────────

    def _map_trial_result(
        self,
        *,
        task: HarborV2Task,
        result: Any,
        trials_dir: Path,
        trial_name: str,
        agent_config: dict[str, Any],
        elapsed: float,
        verbose: bool,
    ) -> TaskResult:
        rewards: dict[str, float] = {}
        vr = getattr(result, "verifier_result", None)
        if vr is not None and getattr(vr, "rewards", None):
            rewards = {str(k): float(v) for k, v in vr.rewards.items()}

        rewards = self._apply_reward_spec(
            rewards,
            (
                Path(task.task_dir) / "tests" / "reward.toml"
                if getattr(task, "task_dir", None)
                else None
            ),
        )
        score = self._score_from_rewards(rewards)

        # Token usage from the trial's aggregated totals.
        usage: dict[str, Any] = {}
        try:
            n_in, _n_cache, n_out, cost = result.compute_token_cost_totals()
            if n_in or n_out:
                usage = {
                    "prompt_tokens": int(n_in or 0),
                    "completion_tokens": int(n_out or 0),
                    "total_tokens": int((n_in or 0) + (n_out or 0)),
                }
                if cost:
                    usage["cost_usd"] = float(cost)
        except Exception:  # noqa: BLE001
            logger.debug("token/cost totals unavailable", exc_info=True)

        transcript = self._load_trajectory(trials_dir / trial_name)
        # For multi-turn tasks the agent-side ATIF only records user turns *inside*
        # the ``send_message_to_user`` tool results; the authoritative alternating
        # dialogue lives in the user-sim sidecar's persisted transcript. Merge it in
        # so the pawbench report / anomaly view sees the real conversation
        # (design doc §4.5/§4.6, approach #2). Additive & gated: single-turn tasks
        # never produce the state file, so they are unaffected.
        requires_user_sim = self._requires_user_sim(task)
        protocol_violation = ""
        if requires_user_sim:
            trial_dir = trials_dir / trial_name
            transcript.extend(self._load_user_sim_turns(trial_dir))
            protocol_complete, protocol_violation = self._multi_turn_protocol_complete(trial_dir)
            if protocol_complete:
                protocol_violation = ""
            self._enrich_trajectory_file(trial_dir)
        multi_agent_result = evaluate_multi_agent_run(
            agent_config.get("multi_agent"),
            agent_config.get("agent_type", "qwenpaw"),
            transcript,
            trials_dir / trial_name / "agent",
        )

        exception_info = getattr(result, "exception_info", None)
        status = "error" if exception_info is not None else "success"
        error_msg = ""
        if exception_info is not None:
            error_msg = str(getattr(exception_info, "message", exception_info))

        execution_result: dict[str, Any] = {
            "status": status,
            "transcript": transcript,
            "transcript_length": len(transcript),
            "usage": usage,
            "exit_code": 0 if status == "success" else 1,
            "timed_out": False,
            "execution_time": elapsed,
            "stderr": error_msg,
            "log_text": "",
        }
        anomaly = detect_anomalies(execution_result, "")
        forced_violation = multi_agent_result["forced_violation"]
        if forced_violation:
            anomaly = dict(anomaly or {})
            anomaly["multi_agent_forced_violation"] = True
        if protocol_violation:
            anomaly = dict(anomaly or {})
            anomaly["multi_turn_protocol_violation"] = protocol_violation

        if verbose:
            logger.info("Trial rewards for %s: %s → score=%.3f", task.task_id, rewards, score)

        breakdown = dict(rewards)
        if "reward" in breakdown and "pass_rate" not in breakdown:
            # Display-only alias: "reward" is Harbor's hardcoded 1-D reward key
            # (leaderboard/uploader/pass@k/viewer all read rewards["reward"]
            # directly, so reward.toml must keep that name). pawbench reports
            # surface the same binary value under the more descriptive
            # "pass_rate" label without touching the underlying reward.json.
            breakdown["pass_rate"] = breakdown["reward"]
        if multi_agent_result["effective_mode"] == "forced":
            breakdown["multi_agent_forced_compliance"] = 0.0 if forced_violation else 1.0
        if requires_user_sim:
            breakdown["multi_turn_protocol_compliance"] = 0.0 if protocol_violation else 1.0
        labels = dict(task.frontmatter)
        # Tag the run mode so the label report separates multi-turn user-sim
        # tasks from single-turn ones (non-regression visibility).
        labels.setdefault("mode", "multi-turn" if self._requires_user_sim(task) else "single-turn")
        return TaskResult(
            task_id=task.task_id,
            task_name=task.name,
            score=0.0 if forced_violation or protocol_violation else score,
            max_score=1.0,
            passed=(
                False
                if forced_violation or protocol_violation
                else (
                    float(rewards["reward"]) >= 1.0 - 1e-9
                    if "reward" in rewards
                    else score >= 1.0 - 1e-9
                )
            ),
            grading_type="harbor_rewardkit",
            breakdown=breakdown,
            notes=(
                ("; ".join(f"{k}={v}" for k, v in rewards.items()) if rewards else error_msg)
                + (
                    "; Forced multi-agent mode required at least one real delegation."
                    if forced_violation
                    else ""
                )
                + (
                    f"; Multi-turn protocol violation: {protocol_violation}"
                    if protocol_violation
                    else ""
                )
            ).strip("; "),
            execution_time=elapsed,
            status=status,
            usage=usage,
            transcript_length=len(transcript),
            timed_out=False,
            error=error_msg,
            transcript=transcript,
            anomaly=anomaly,
            labels=labels,
            multi_agent=multi_agent_result,
            trial_dir=str(trials_dir / trial_name),
        )

    @staticmethod
    def _score_from_rewards(rewards: dict[str, float]) -> float:
        """Collapse the RewardKit reward dict into a single 0..1 score.

        Preference order: an explicit continuous ``score`` key (tasks that
        define a second ``[[reward]]`` entry in ``reward.toml``, e.g.
        ``aggregation = "weighted_mean"``, so the continuous score survives
        alongside a separately-named binary pass/fail key such as
        ``pass_rate``), then Harbor's 1-D ``reward`` convention, then a
        common aggregation key, then the mean of all values.
        """
        if not rewards:
            return 0.0
        for key in ("score", "reward", "overall", "total", "all_pass"):
            if key in rewards:
                return float(rewards[key])
        vals = [float(v) for v in rewards.values()]
        return sum(vals) / len(vals) if vals else 0.0

    @staticmethod
    def _apply_reward_spec(
        rewards: dict[str, float],
        reward_toml: Path | None,
    ) -> dict[str, float]:
        """Recover top-level RewardKit aggregation when an old CLI omits it.

        Some task images invoke ``harbor-rewardkit@0.1`` versions that emit only
        dimension scores even though ``tests/reward.toml`` defines a top-level
        reward.  Reproduce RewardKit's collapse semantics so task-defined
        threshold/all-pass policy is not silently replaced by a plain mean.
        Explicit aggregate keys from RewardKit always win.
        """
        if not rewards or reward_toml is None or not reward_toml.is_file():
            return dict(rewards)
        try:
            config = tomllib.loads(reward_toml.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            logger.warning("Unable to parse reward aggregation spec: %s", reward_toml)
            return dict(rewards)

        specs = config.get("reward", [])
        if not isinstance(specs, list) or not specs:
            return dict(rewards)
        spec_names = {
            str(spec.get("name")) for spec in specs if isinstance(spec, dict) and spec.get("name")
        }
        dimensions = [float(value) for name, value in rewards.items() if name not in spec_names]
        if not dimensions:
            return dict(rewards)

        collapsed = dict(rewards)
        mean = sum(dimensions) / len(dimensions)
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or "").strip()
            if not name or name in collapsed:
                continue
            aggregation = str(spec.get("aggregation") or "weighted_mean")
            threshold = float(spec.get("threshold", 0.5))
            if aggregation == "all_pass" or aggregation == "required_pass":
                value = 1.0 if all(score > 0 for score in dimensions) else 0.0
            elif aggregation == "any_pass":
                value = 1.0 if any(score > 0 for score in dimensions) else 0.0
            elif aggregation == "threshold":
                value = 1.0 if mean >= threshold else 0.0
            elif aggregation == "weighted_mean":
                value = mean
            else:
                logger.warning(
                    "Unsupported reward aggregation %r in %s; leaving %r unset",
                    aggregation,
                    reward_toml,
                    name,
                )
                continue
            collapsed[name] = round(value, 4)
        return collapsed

    @staticmethod
    def _load_trajectory(trial_dir: Path) -> list[dict[str, Any]]:
        """Load the ATIF trajectory and convert it to pawbench transcript shape.

        pawbench's anomaly detection / transcript persistence expect a list of
        ``{"type": "message", "message": {"role", "content": [...]}}`` entries.
        We do a best-effort conversion of the ATIF ``steps`` array; failures are
        non-fatal (an empty transcript just surfaces as an anomaly).
        """
        traj_path = trial_dir / "agent" / "trajectory.json"
        if not traj_path.is_file():
            # QwenPaw (and other agentscope-based harnesses) persist a
            # qwenpaw-native session JSON instead of an ATIF trajectory.json.
            # Fall back to it so the transcript is populated and the
            # EMPTY_TRANSCRIPT anomaly does not spuriously fire.
            session_transcript = HarborV2Backend._load_qwenpaw_session(trial_dir)
            if session_transcript:
                return session_transcript
            return []
        try:
            data = json.loads(traj_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []

        steps = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(steps, list):
            return []

        transcript: list[dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            source = step.get("source", "agent")
            role = {"agent": "assistant", "user": "user", "system": "system"}.get(
                source, "assistant"
            )
            content: list[dict[str, Any]] = []
            message = step.get("message")
            if isinstance(message, str) and message:
                content.append({"type": "text", "text": message})
            elif isinstance(message, dict) and message.get("text"):
                content.append({"type": "text", "text": message["text"]})
            for call in step.get("tool_calls", []) or []:
                if isinstance(call, dict):
                    content.append(
                        {
                            "type": "toolCall",
                            "name": call.get("name", ""),
                            "arguments": call.get("arguments", {}),
                        }
                    )
            metrics = step.get("metrics") or {}
            usage = {}
            if isinstance(metrics, dict):
                usage = {
                    "prompt_tokens": metrics.get("prompt_tokens", 0),
                    "completion_tokens": metrics.get("completion_tokens", 0),
                }
            transcript.append(
                {
                    "type": "message",
                    "message": {"role": role, "content": content, "usage": usage},
                }
            )
        return transcript

    @staticmethod
    def _load_qwenpaw_session(trial_dir: Path) -> list[dict[str, Any]]:
        """Convert a QwenPaw-native ``qwenpaw.session.json`` to pawbench shape.

        QwenPaw (agentscope) does not emit an ATIF ``trajectory.json``; it
        persists its session under ``agent/qwenpaw.session.json`` where the
        conversation lives at ``agent.state.context`` as a list of messages.
        Each message has ``role`` and a ``content`` list whose parts are typed
        ``text`` / ``thinking`` / ``tool_call`` / ``tool_result``. We map those
        into the ``{"type": "message", "message": {...}}`` shape expected by
        pawbench anomaly detection and transcript persistence. Missing or
        malformed files are non-fatal (returns []).
        """
        session_path = trial_dir / "agent" / "qwenpaw.session.json"
        if not session_path.is_file():
            return []
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []

        try:
            context = data["agent"]["state"]["context"]
        except (KeyError, TypeError):
            return []
        if not isinstance(context, list):
            return []

        transcript: list[dict[str, Any]] = []

        def _emit(role: str, content: list[dict[str, Any]], usage: dict[str, Any]) -> None:
            transcript.append(
                {
                    "type": "message",
                    "message": {"role": role, "content": content, "usage": usage or {}},
                }
            )

        for msg in context:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or "assistant"
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            parts = msg.get("content")

            # Non-assistant turns (user/system) collapse into a single message.
            if role != "assistant":
                content: list[dict[str, Any]] = []
                if isinstance(parts, str) and parts:
                    content.append({"type": "text", "text": parts})
                elif isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, dict) and part.get("text"):
                            content.append({"type": "text", "text": part["text"]})
                _emit(role, content, usage)
                continue

            # The assistant turn packs the whole run (thinking / text / tool
            # calls / tool results) into one context entry. Expand each part
            # into its own transcript message so length/roles reflect reality
            # (and SHORT_TRANSCRIPT only fires when the agent truly did little).
            if isinstance(parts, str) and parts:
                _emit("assistant", [{"type": "text", "text": parts}], usage)
            elif isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    ptype = part.get("type")
                    if ptype == "text" and part.get("text"):
                        _emit("assistant", [{"type": "text", "text": part["text"]}], {})
                    elif ptype == "thinking" and part.get("thinking"):
                        _emit("assistant", [{"type": "text", "text": part["thinking"]}], {})
                    elif ptype == "tool_call":
                        _emit(
                            "assistant",
                            [
                                {
                                    "type": "toolCall",
                                    "name": part.get("name", ""),
                                    "arguments": part.get("input", part.get("arguments", "")),
                                }
                            ],
                            {},
                        )
                    elif ptype == "tool_result":
                        _emit(
                            "tool",
                            [
                                {
                                    "type": "toolResult",
                                    "name": part.get("name", ""),
                                    "output": part.get("output", ""),
                                }
                            ],
                            {},
                        )
        return transcript

    @staticmethod
    def _load_user_sim_state(trial_dir: Path) -> dict[str, Any] | None:
        state_path = trial_dir / "agent" / "user_sim_state.json"
        if not state_path.is_file():
            return None
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        return data if isinstance(data, dict) else None

    @classmethod
    def _multi_turn_protocol_complete(
        cls,
        trial_dir: Path,
    ) -> tuple[bool, str]:
        """Validate that the agent completed the user-sim dialogue lifecycle."""
        state = cls._load_user_sim_state(trial_dir)
        if state is None:
            return False, "user-sim state is missing or malformed"
        if not state.get("started"):
            return False, "start_conversation was never called"
        transcript = state.get("transcript")
        if not isinstance(transcript, list) or not any(
            isinstance(turn, dict) and turn.get("source") == "agent" for turn in transcript
        ):
            return False, "send_message_to_user was never called"
        if not state.get("done") or not state.get("termination_reason"):
            return False, "conversation ended before conversation_over=true"
        return True, ""

    @staticmethod
    def _enrich_trajectory_file(trial_dir: Path) -> None:
        """Best-effort: fuse the user-sim dialogue directly into ``agent/trajectory.json``.

        Decodes the user-sim dialogue that is already embedded (re-escaped) in
        the raw ATIF ``trajectory.json`` tool-call observations and inserts it
        in place (chronologically, right after the tool call that produced it),
        so the trial directory carries a single self-contained file with the
        full agent+user conversation instead of requiring readers to
        cross-reference ``agent/user_sim_state.json``.

        The pristine pre-enrichment ATIF is preserved once as
        ``agent/trajectory.raw.json`` (only written if absent, so retries never
        clobber the original). Any failure (missing file, unexpected shape,
        harness without one — e.g. QwenPaw's native session log) is swallowed
        so grading is never affected.
        """
        traj_path = trial_dir / "agent" / "trajectory.json"
        if not traj_path.is_file():
            return
        raw_backup = trial_dir / "agent" / "trajectory.raw.json"
        try:
            if not raw_backup.is_file():
                shutil.copyfile(traj_path, raw_backup)
            enrich_file(traj_path, traj_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("trajectory enrichment skipped for %s: %s", trial_dir, exc)

    @staticmethod
    def _load_user_sim_turns(trial_dir: Path) -> list[dict[str, Any]]:
        """Convert the user-sim sidecar transcript to pawbench transcript shape.

        Multi-turn tasks run the user simulator as a sidecar which persists the
        authoritative user/agent dialogue to ``agent/user_sim_state.json`` on the
        shared agent-logs volume (see :class:`UserSimRuntime.state_payload`). The
        state file's ``transcript`` is a list of ``{"source": "user"|"agent",
        "text": ...}`` turns. Missing / malformed files are non-fatal (returns []).
        """
        data = HarborV2Backend._load_user_sim_state(trial_dir)
        if data is None:
            return []
        turns = data.get("transcript") if isinstance(data, dict) else None
        if not isinstance(turns, list):
            return []

        out: list[dict[str, Any]] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            text = turn.get("text") or ""
            if not text:
                continue
            role = "user" if turn.get("source") == "user" else "assistant"
            out.append(
                {
                    "type": "message",
                    "message": {
                        "role": role,
                        "content": [{"type": "text", "text": text}],
                        "usage": {},
                        "source": "user_sim",
                    },
                }
            )
        return out

    # ── helpers ──────────────────────────────────────────────────────────────────

    def _error_result(
        self,
        task: Any,
        error: str,
        elapsed: float = 0.0,
        timed_out: bool = False,
    ) -> TaskResult:
        return TaskResult(
            task_id=task.task_id,
            task_name=getattr(task, "name", task.task_id),
            score=0.0,
            max_score=1.0,
            passed=False,
            grading_type="error",
            breakdown={},
            notes="",
            execution_time=elapsed,
            status="error",
            usage={},
            transcript_length=0,
            timed_out=timed_out,
            error=error,
            labels=dict(getattr(task, "frontmatter", {}) or {}),
        )
