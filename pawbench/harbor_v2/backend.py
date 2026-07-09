# -*- coding: utf-8 -*-
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
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from pawbench.backend import BenchmarkBackend, TaskResult
from pawbench.utils.anomalies import detect_anomalies

from .task_loader import HarborV2Loader, HarborV2Task


logger = logging.getLogger(__name__)


# Aliases: pawbench ``harbor:<name>`` values → Harbor AgentName registry names.
# Most names match verbatim; only the handful that diverge are listed here.
_AGENT_NAME_ALIASES: Dict[str, str] = {
    "qwen-code": "qwen-coder",
}

# Fallback import paths for installed agents that ship in the image but are NOT
# registered in Harbor's ``AgentName`` enum (so the native Trial AgentFactory
# rejects them by name).  Passing a ``module:Class`` import path bypasses the
# enum check — see harbor.agents.factory.create_agent_from_config.
_AGENT_IMPORT_PATHS: Dict[str, str] = {
    "qwenpaw": "harbor.agents.installed.qwenpaw:QwenPaw",
}


class HarborV2Backend(BenchmarkBackend):
    """Run and grade Harbor-native (v2) tasks through Harbor's Trial runner."""

    DEFAULT_DATASET = "Pawbenchv2_task_0706"

    @property
    def name(self) -> str:
        return "pawbench"

    # ── task discovery ─────────────────────────────────────────────────────────

    def _dataset_root(self, dataset: Optional[str]) -> Path:
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
        task_filter: Optional[List[str]] = None,
        dataset: Optional[str] = None,
        **_kwargs: Any,
    ) -> List[Any]:
        loader = HarborV2Loader(self._dataset_root(dataset))
        tasks = loader.load_all_tasks()
        if task_filter:
            def _matches(t: HarborV2Task, filters: List[str]) -> bool:
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
        agent_config: Dict[str, Any],
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
        except (asyncio.TimeoutError, TimeoutError):
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
        agent_config: Dict[str, Any],
    ) -> TaskResult:
        # Deferred harbor import — only needed when actually running a trial.
        from harbor.models.trial.config import (
            AgentConfig,
            EnvironmentConfig,
            TaskConfig,
            TrialConfig,
            VerifierConfig,
        )
        from harbor.trial.trial import Trial
        from harbor.models.agent.name import AgentName

        short_agent_name = self._resolve_agent_name(agent_config.get("agent_type", ""))
        agent_name = short_agent_name
        # Names not in Harbor's AgentName enum must be passed as a module:Class
        # import path (e.g. the pawbench-bundled qwenpaw agent).
        if agent_name not in AgentName.values():
            agent_name = _AGENT_IMPORT_PATHS.get(agent_name, agent_name)
        model = agent_config.get("model", "")
        verbose = bool(agent_config.get("verbose", False))

        # Extra kwargs forwarded to the native agent constructor via
        # AgentConfig.kwargs (harbor.agents.factory merges these into the
        # agent's __init__). OpenClaw's "thinking" CliFlag defaults to "high",
        # which non-reasoning models (e.g. DashScope qwen3.6-plus) reject
        # outright ("Thinking level 'high' is not supported ... Use one of:
        # off."), crashing the CLI with an empty transcript. Mirror
        # HarborBridgeAgent: force "off" unless the caller explicitly passed
        # --thinking (agent_config["thinking_level"]).
        agent_kwargs: Dict[str, Any] = {}
        if short_agent_name == "openclaw":
            agent_kwargs["thinking"] = agent_config.get("thinking_level") or "off"

        # MUST be an absolute path: Harbor bind-mounts the trial's workspace /
        # artifacts dirs into the task & verifier containers, and under
        # docker-socket sharing (DinD) the host daemon resolves those mounts.
        # A relative trials_dir yields a non-host-visible mount source, so the
        # agent's declared artifacts (e.g. /app/summary_*.md) never reach the
        # separate verifier — silently scoring structure=0 / reward=0.
        trials_dir = Path(
            agent_config.get("trials_dir")
            or tempfile.mkdtemp(prefix=f"harborv2_{task.task_id}_")
        ).resolve()
        run_id = agent_config.get("run_id") or uuid.uuid4().hex[:8]
        trial_name = f"{task.task_id}__{run_id}"

        agent_env = self._build_agent_env(agent_name, model, agent_config)
        verifier_env = self._build_verifier_env(agent_config)
        # Multi-turn tasks (Strategy A) run a user-sim MCP sidecar in the
        # environment; inject its dedicated USER_SIM_* credentials so the compose
        # sidecar service can reach its LLM. Fail-fast when a multi-turn task is
        # missing the dedicated creds — the user simulator must never silently
        # fall back to the agent-under-test / judge credentials.
        environment_env = self._build_environment_env(task, agent_config)

        timeout_multiplier = float(agent_config.get("timeout_multiplier", 1.0))

        config = TrialConfig(
            task=TaskConfig(path=task.task_dir),
            trial_name=trial_name,
            trials_dir=trials_dir,
            timeout_multiplier=timeout_multiplier,
            agent=AgentConfig(
                name=agent_name,
                model_name=model,
                env=agent_env,
                kwargs=agent_kwargs,
            ),
            environment=(
                EnvironmentConfig(env=environment_env)
                if environment_env
                else EnvironmentConfig()  # provider defaults to docker
            ),
            verifier=VerifierConfig(env=verifier_env) if verifier_env else VerifierConfig(),
        )

        t0 = time.time()
        trial = await Trial.create(config)
        result = await trial.run()
        elapsed = time.time() - t0

        return self._map_trial_result(
            task=task,
            result=result,
            trials_dir=trials_dir,
            trial_name=trial_name,
            agent_config=agent_config,
            elapsed=elapsed,
            verbose=verbose,
        )

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
        agent_config: Dict[str, Any],
    ) -> Dict[str, str]:
        """Build the env dict handed to the Harbor agent (scoped to its container).

        Mirrors HarborBridgeAgent._build_extra_env: pick the provider-appropriate
        API-key/base-url vars from the model prefix so the installed agent can
        reach its LLM without extra configuration, plus a claude-code-specific
        adaptation for proxied Anthropic endpoints.
        """
        env: Dict[str, str] = {}
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
            "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE",
            "GOOGLE_API_KEY", "OPENROUTER_API_KEY",
            "DASHSCOPE_API_KEY", "KIMI_API_KEY", "GLM_API_KEY",
        ):
            if key not in env and os.environ.get(key):
                env[key] = os.environ[key]

        if agent_name == "claude-code":
            self._apply_claude_code_proxy_env(env, model)
        return env

    @staticmethod
    def _apply_claude_code_proxy_env(env: Dict[str, str], model: str) -> None:
        """Adapt claude-code for a proxied Anthropic (messages-format) endpoint.

        Harbor's native ClaudeCode agent, when a custom ``ANTHROPIC_BASE_URL`` is
        set, keeps the *full* ``provider/model`` string as ``ANTHROPIC_MODEL``.
        Most relay/proxy platforms (the ones the PawBench HarborBridgeAgent path
        targets) only recognise the *bare* model id, so we override
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

    def _build_verifier_env(self, agent_config: Dict[str, Any]) -> Dict[str, str]:
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
        env: Dict[str, str] = {}
        judge_api_key = agent_config.get("judge_api_key")
        judge_base_url = agent_config.get("judge_base_url")
        judge_model = agent_config.get("judge_model")

        if judge_model:
            env["MODEL"] = judge_model
        if judge_api_key:
            env["LLM_API_KEY"] = judge_api_key
        if judge_base_url:
            env["LLM_BASE_URL"] = judge_base_url

        api_format = agent_config.get("judge_api_format")
        if not api_format:
            model_l = (judge_model or "").lower()
            base_l = (judge_base_url or "").lower()
            # Anthropic only for Claude models or an explicit Anthropic endpoint;
            # everything else (qwen, gpt, deepseek, …) is OpenAI-compatible.
            is_anthropic = model_l.startswith("claude") or "anthropic" in base_l
            api_format = "anthropic" if is_anthropic else "openai"
        env["LLM_API_FORMAT"] = api_format
        return env

    # ── multi-turn / user-sim wiring ──────────────────────────────────────────────

    # task.toml [metadata].mode values that indicate a multi-turn user-sim task.
    _MULTI_TURN_MODES = {"multi-turn", "multi_turn", "multiturn", "cowork"}
    # substrings used to recognise a user-sim MCP server declaration by name/url.
    _USER_SIM_MCP_MARKERS = ("user-sim", "user_sim", "usersim")

    @classmethod
    def _requires_user_sim(cls, task: HarborV2Task) -> bool:
        """Whether a task actually wires a user-sim sidecar (needs ``USER_SIM_*``).

        Detection is deliberately **narrow** so that ``USER_SIM_*`` injection and
        the fail-fast only fire for tasks that genuinely run a user simulator. A
        task requires the user simulator when **either**:

        * ``[metadata].mode`` is one of the multi-turn modes (``multi-turn`` /
          ``cowork`` / …); **or**
        * a ``[[environment.mcp_servers]]`` entry names/points at a user-sim.

        ``[metadata].category`` (e.g. ``user_agent``) and the presence of a
        ``.user/`` / ``user/`` persona directory are intentionally **not** used
        here: existing single-turn ``user_agent`` tasks (e.g. ``ua-cw-wcag-4957``)
        ship a ``user/`` directory *without* any sidecar, so treating them as
        multi-turn would wrongly demand ``USER_SIM_*`` and fail-fast — breaking
        the default dataset. Those signals may still inform the *label* but must
        never drive the fail-fast gate (see design doc §4.4 / §7.2).
        """
        metadata = getattr(task, "metadata", {}) or {}
        mode = str(metadata.get("mode") or "").strip().lower()
        if mode in cls._MULTI_TURN_MODES:
            return True

        raw = getattr(task, "raw_config", {}) or {}
        env_cfg = raw.get("environment", {}) or {}
        for server in env_cfg.get("mcp_servers", []) or []:
            if not isinstance(server, dict):
                continue
            haystack = f"{server.get('name', '')} {server.get('url', '')}".lower()
            if any(marker in haystack for marker in cls._USER_SIM_MCP_MARKERS):
                return True
        return False

    def _build_environment_env(
        self,
        task: HarborV2Task,
        agent_config: Dict[str, Any],
    ) -> Dict[str, str]:
        """Build the env injected into the environment (sidecar) container(s).

        Only populated for multi-turn tasks. The user simulator uses a dedicated
        ``USER_SIM_*`` contract with **no** fallback to agent/judge credentials;
        a missing model/key on a multi-turn task raises (surfaced as an error
        TaskResult by ``run_and_grade``).
        """
        if not self._requires_user_sim(task):
            return {}

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

        env: Dict[str, str] = {
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
        agent_config: Dict[str, Any],
        elapsed: float,
        verbose: bool,
    ) -> TaskResult:
        rewards: Dict[str, float] = {}
        vr = getattr(result, "verifier_result", None)
        if vr is not None and getattr(vr, "rewards", None):
            rewards = {str(k): float(v) for k, v in vr.rewards.items()}

        score = self._score_from_rewards(rewards)

        # Token usage from the trial's aggregated totals.
        usage: Dict[str, Any] = {}
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
        if self._requires_user_sim(task):
            transcript.extend(self._load_user_sim_turns(trials_dir / trial_name))

        exception_info = getattr(result, "exception_info", None)
        status = "error" if exception_info is not None else "success"
        error_msg = ""
        if exception_info is not None:
            error_msg = str(getattr(exception_info, "message", exception_info))

        execution_result: Dict[str, Any] = {
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

        if verbose:
            logger.info("Trial rewards for %s: %s → score=%.3f", task.task_id, rewards, score)

        breakdown = dict(rewards)
        labels = dict(task.frontmatter)
        # Tag the run mode so the label report separates multi-turn user-sim
        # tasks from single-turn ones (non-regression visibility).
        labels.setdefault(
            "mode", "multi-turn" if self._requires_user_sim(task) else "single-turn"
        )
        return TaskResult(
            task_id=task.task_id,
            task_name=task.name,
            score=score,
            max_score=1.0,
            passed=score >= 1.0 - 1e-9,
            grading_type="harbor_rewardkit",
            breakdown=breakdown,
            notes=("; ".join(f"{k}={v}" for k, v in rewards.items()) if rewards else error_msg),
            execution_time=elapsed,
            status=status,
            usage=usage,
            transcript_length=len(transcript),
            timed_out=False,
            error=error_msg,
            transcript=transcript,
            anomaly=anomaly,
            labels=labels,
        )

    @staticmethod
    def _score_from_rewards(rewards: Dict[str, float]) -> float:
        """Collapse the RewardKit reward dict into a single 0..1 score.

        Preference order: an explicit ``reward`` key (Harbor's 1-D convention),
        then a common aggregation key, then the mean of all values.
        """
        if not rewards:
            return 0.0
        for key in ("reward", "overall", "total", "all_pass"):
            if key in rewards:
                return float(rewards[key])
        vals = [float(v) for v in rewards.values()]
        return sum(vals) / len(vals) if vals else 0.0

    @staticmethod
    def _load_trajectory(trial_dir: Path) -> List[Dict[str, Any]]:
        """Load the ATIF trajectory and convert it to pawbench transcript shape.

        pawbench's anomaly detection / transcript persistence expect a list of
        ``{"type": "message", "message": {"role", "content": [...]}}`` entries.
        We do a best-effort conversion of the ATIF ``steps`` array; failures are
        non-fatal (an empty transcript just surfaces as an anomaly).
        """
        traj_path = trial_dir / "agent" / "trajectory.json"
        if not traj_path.is_file():
            return []
        try:
            data = json.loads(traj_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []

        steps = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(steps, list):
            return []

        transcript: List[Dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            source = step.get("source", "agent")
            role = {"agent": "assistant", "user": "user", "system": "system"}.get(source, "assistant")
            content: List[Dict[str, Any]] = []
            message = step.get("message")
            if isinstance(message, str) and message:
                content.append({"type": "text", "text": message})
            elif isinstance(message, dict) and message.get("text"):
                content.append({"type": "text", "text": message["text"]})
            for call in step.get("tool_calls", []) or []:
                if isinstance(call, dict):
                    content.append({
                        "type": "toolCall",
                        "name": call.get("name", ""),
                        "arguments": call.get("arguments", {}),
                    })
            metrics = step.get("metrics") or {}
            usage = {}
            if isinstance(metrics, dict):
                usage = {
                    "prompt_tokens": metrics.get("prompt_tokens", 0),
                    "completion_tokens": metrics.get("completion_tokens", 0),
                }
            transcript.append({
                "type": "message",
                "message": {"role": role, "content": content, "usage": usage},
            })
        return transcript

    @staticmethod
    def _load_user_sim_turns(trial_dir: Path) -> List[Dict[str, Any]]:
        """Convert the user-sim sidecar transcript to pawbench transcript shape.

        Multi-turn tasks run the user simulator as a sidecar which persists the
        authoritative user/agent dialogue to ``agent/user_sim_state.json`` on the
        shared agent-logs volume (see :class:`UserSimRuntime.state_payload`). The
        state file's ``transcript`` is a list of ``{"source": "user"|"agent",
        "text": ...}`` turns. Missing / malformed files are non-fatal (returns []).
        """
        state_path = trial_dir / "agent" / "user_sim_state.json"
        if not state_path.is_file():
            return []
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []
        turns = data.get("transcript") if isinstance(data, dict) else None
        if not isinstance(turns, list):
            return []

        out: List[Dict[str, Any]] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            text = turn.get("text") or ""
            if not text:
                continue
            role = "user" if turn.get("source") == "user" else "assistant"
            out.append({
                "type": "message",
                "message": {
                    "role": role,
                    "content": [{"type": "text", "text": text}],
                    "usage": {},
                    "source": "user_sim",
                },
            })
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
            score=0.0, max_score=1.0, passed=False,
            grading_type="error", breakdown={}, notes="",
            execution_time=elapsed,
            status="error", usage={}, transcript_length=0,
            timed_out=timed_out, error=error,
            labels=dict(getattr(task, "frontmatter", {}) or {}),
        )
