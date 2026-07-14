# -*- coding: utf-8 -*-
"""Multi-agent (sub-agent / agent-team) configuration for Harbor harnesses.

PawBench normally runs each harness as a **single** agent process. This module
adds a harness-agnostic way to launch the same harness in its **multi-agent**
mode — the orchestrator + sub-agent / delegation feature each CLI ships:

* **claude-code** — *sub-agents* (``--agents '<json>'``, session-only definitions)
  and the experimental *agent teams* (``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1``).
  See https://code.claude.com/docs/en/sub-agents.
* **codex** — stable sub-agent delegation (``features.multi_agent`` +
  ``agents.max_threads`` / ``agents.max_depth``).
  See https://developers.openai.com/codex/subagents.
* **openclaw** — *sub-agents* via the ``sessions_spawn`` tool, which requires the
  ``coding`` tool profile and ``subagents.maxSpawnDepth`` in ``openclaw.json``.
  See https://docs.openclaw.ai/tools/subagents.

The rest of PawBench does not need to know any of these details: it hands a
single normalized :class:`MultiAgentConfig` to
:class:`~pawbench.agents.impl.harbor_bridge_agent.HarborBridgeAgent`, which calls
:func:`build_harbor_kwargs` to translate the normalized spec into the
harness-specific constructor kwargs / env vars.

Design
------
The normalized config is intentionally small and provider-neutral:

``enabled``      turn multi-agent mode on/off.
``mode``         high-level style: ``"auto"`` (harness default), ``"subagents"``,
                 ``"teams"`` (claude only), ``"delegation"`` (codex only).
``max_agents``   max concurrent sub-agents / worker threads.
``max_depth``    max nesting depth (orchestrator → worker → …).
``subagents``    optional list of named sub-agent definitions (claude-code
                 ``--agents`` JSON; each item: ``name``, ``description``,
                 ``prompt``, optional ``tools``, optional ``model``).
``raw``          optional per-harness escape hatch merged verbatim into the
                 translated kwargs (e.g. ``{"openclaw": {...}, "codex": {...}}``).

Grading is unaffected: a multi-agent run produces the same workspace + transcript
artifacts as a single-agent run, so all existing graders work unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Harness short-names (matching HARBOR_AGENT_REGISTRY keys) that support a
# multi-agent / sub-agent launch mode through this module.
SUPPORTED_MULTI_AGENT_HARNESSES: frozenset[str] = frozenset(
    {"claude-code", "codex", "openclaw"}
)


@dataclass
class SubAgentSpec:
    """A single named sub-agent definition (primarily for claude-code ``--agents``)."""

    name: str
    description: str
    prompt: str
    tools: list[str] | None = None
    model: str | None = None

    def to_claude_entry(self) -> dict[str, Any]:
        """Serialize to a claude-code ``--agents`` JSON value (keyed by name upstream)."""
        entry: dict[str, Any] = {
            "description": self.description,
            "prompt": self.prompt,
        }
        if self.tools:
            entry["tools"] = list(self.tools)
        if self.model:
            entry["model"] = self.model
        return entry

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubAgentSpec":
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Each subagent must have a non-empty 'name'.")
        description = str(data.get("description") or "").strip()
        prompt = str(data.get("prompt") or "").strip()
        if not description or not prompt:
            raise ValueError(
                f"Subagent '{name}' must define both 'description' and 'prompt'."
            )
        tools = data.get("tools")
        if tools is not None and not isinstance(tools, list):
            raise ValueError(f"Subagent '{name}' 'tools' must be a list of strings.")
        model = data.get("model")
        return cls(
            name=name,
            description=description,
            prompt=prompt,
            tools=[str(t) for t in tools] if tools else None,
            model=str(model) if model else None,
        )


@dataclass
class MultiAgentConfig:
    """Normalized, harness-agnostic multi-agent launch spec."""

    enabled: bool = False
    mode: str = "auto"
    max_agents: int = 4
    max_depth: int = 2
    subagents: list[SubAgentSpec] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    # ── constructors ──────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MultiAgentConfig":
        if not data:
            return cls()
        subagents = [
            SubAgentSpec.from_dict(s) for s in (data.get("subagents") or [])
        ]
        raw = data.get("raw") or {}
        if not isinstance(raw, dict):
            raise ValueError("Multi-agent 'raw' must be an object.")
        return cls(
            enabled=bool(data.get("enabled", True)),
            mode=str(data.get("mode") or "auto").strip().lower(),
            max_agents=int(data.get("max_agents", 4)),
            max_depth=int(data.get("max_depth", 2)),
            subagents=subagents,
            raw=raw,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "MultiAgentConfig":
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(
                f"Multi-agent config file {p} must contain a JSON object."
            )
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "max_agents": self.max_agents,
            "max_depth": self.max_depth,
            "subagents": [
                {
                    "name": s.name,
                    "description": s.description,
                    "prompt": s.prompt,
                    **({"tools": s.tools} if s.tools else {}),
                    **({"model": s.model} if s.model else {}),
                }
                for s in self.subagents
            ],
            "raw": self.raw,
        }


# ── translation to harbor-agent-specific kwargs / env ─────────────────────────


def build_harbor_kwargs(
    harbor_agent_name: str,
    cfg: MultiAgentConfig,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Translate a normalized :class:`MultiAgentConfig` into Harbor agent inputs.

    Returns
    -------
    ``(ctor_kwargs, extra_env)`` where

    * ``ctor_kwargs`` are forwarded to the Harbor ``BaseInstalledAgent`` subclass
      constructor (they map onto that agent's ``CLI_FLAGS`` / bespoke kwargs), and
    * ``extra_env`` are extra environment variables to merge into the agent's run
      environment.

    Unknown / unsupported harnesses return empty dicts (multi-agent is a no-op).
    """
    if not cfg.enabled:
        return {}, {}

    name = harbor_agent_name.strip().lower()
    if name == "claude-code":
        return _build_claude_code(cfg)
    if name == "codex":
        return _build_codex(cfg)
    if name == "openclaw":
        return _build_openclaw(cfg)
    return {}, {}


def _build_claude_code(cfg: MultiAgentConfig) -> tuple[dict[str, Any], dict[str, str]]:
    """claude-code: session-only ``--agents`` sub-agents + experimental agent teams.

    Everything is passed as constructor kwargs (not env) because the PawBench
    Harbor shim does not auto-propagate ``extra_env`` into each ``docker exec``;
    the Harbor Claude agent folds these into the command / run env itself.

    * ``mode="teams"`` (or ``"auto"``) enables the experimental *agent teams*
      (``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1``) in deterministic in-process mode.
    * Any provided ``subagents`` are injected as session-only ``--agents`` JSON.
    * ``mode="subagents"`` keeps teams off and relies on Task-tool delegation.
    """
    ctor: dict[str, Any] = {}
    env: dict[str, str] = {}

    if cfg.subagents:
        ctor["subagents"] = [
            {"name": s.name, **s.to_claude_entry()} for s in cfg.subagents
        ]

    if cfg.mode in ("auto", "teams"):
        ctor["agent_teams"] = True
        if cfg.max_agents:
            ctor["max_teammates"] = int(cfg.max_agents)

    _merge_raw(ctor, env, cfg.raw.get("claude-code"))
    return ctor, env


def _build_codex(cfg: MultiAgentConfig) -> tuple[dict[str, Any], dict[str, str]]:
    """codex: enable stable sub-agent delegation and bound fan-out.

    Codex CLI 0.144+ exposes the stable ``multi_agent`` feature and configures
    concurrency/recursion through ``agents.max_threads`` and
    ``agents.max_depth``. Headless runs still rely on the task prompt or project
    instructions to make the model delegate; enabling the feature only exposes
    the orchestration tools.
    """
    ctor: dict[str, Any] = {"multi_agent": True}
    env: dict[str, str] = {}
    if cfg.max_agents:
        ctor["multi_agent_max_threads"] = int(cfg.max_agents)
    if cfg.max_depth:
        ctor["multi_agent_max_depth"] = max(1, int(cfg.max_depth))

    _merge_raw(ctor, env, cfg.raw.get("codex"))
    return ctor, env


def _build_openclaw(cfg: MultiAgentConfig) -> tuple[dict[str, Any], dict[str, str]]:
    """openclaw: expose ``sessions_spawn`` (coding profile) + nesting depth.

    Injects an ``openclaw_config`` overlay that is deep-merged into the uploaded
    ``openclaw.json``. The sub-agent knobs live under ``agents.defaults.subagents``
    (verified against OpenClaw's zod schema — a *top-level* ``subagents`` key is
    rejected as invalid):

    * ``tools.profile = "coding"`` — the profile that includes ``sessions_spawn``,
      ``sessions_yield`` and ``subagents`` (the messaging profile does not).
    * ``agents.defaults.subagents.maxSpawnDepth`` — 1 = main→worker,
      2 = main→orchestrator→worker (schema range 1..5).
    * ``...maxConcurrent`` — cap on concurrently running sub-agents.
    * ``...delegationMode`` — ``"prefer"`` (proactive) vs ``"suggest"`` (explicit).
    """
    depth = min(5, max(1, int(cfg.max_depth)))
    subagents: dict[str, Any] = {"maxSpawnDepth": depth}
    if cfg.max_agents:
        # 1..20 per schema; also used as the concurrency cap.
        subagents["maxConcurrent"] = min(20, max(1, int(cfg.max_agents)))
    if cfg.mode in ("auto", "delegation", "proactive", "teams"):
        subagents["delegationMode"] = "prefer"
    elif cfg.mode in ("subagents", "explicit", "explicit-request-only"):
        subagents["delegationMode"] = "suggest"

    overlay: dict[str, Any] = {
        "tools": {"profile": "coding"},
        "agents": {"defaults": {"subagents": subagents}},
    }
    # OpenClaw sub-agents can be told to prefer a cheaper/faster default model
    # (agents.defaults.subagents.model); by default they inherit the main model,
    # so we leave it untouched unless the caller supplied a `raw` override.
    raw_oc = cfg.raw.get("openclaw")
    if isinstance(raw_oc, dict):
        _deep_merge(overlay, raw_oc)

    ctor: dict[str, Any] = {"openclaw_config": overlay}
    return ctor, {}


# ── helpers ───────────────────────────────────────────────────────────────────


def _merge_raw(
    ctor: dict[str, Any],
    env: dict[str, str],
    raw: Any,
) -> None:
    """Merge a per-harness ``raw`` override into ctor/env.

    ``raw`` may contain ``{"ctor": {...}, "env": {...}}`` to target either bucket
    explicitly; a flat dict is treated as constructor kwargs.
    """
    if not isinstance(raw, dict):
        return
    if "ctor" in raw or "env" in raw:
        if isinstance(raw.get("ctor"), dict):
            ctor.update(raw["ctor"])
        if isinstance(raw.get("env"), dict):
            env.update({str(k): str(v) for k, v in raw["env"].items()})
    else:
        ctor.update(raw)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def default_multi_agent_config() -> MultiAgentConfig:
    """A sensible default that turns on multi-agent mode for every supported harness.

    * claude-code: agent teams enabled (in-process), no custom sub-agents.
    * codex: proactive delegation, up to 4 threads / depth 2.
    * openclaw: coding profile + maxSpawnDepth 2 (orchestrator pattern).
    """
    return MultiAgentConfig(enabled=True, mode="auto", max_agents=4, max_depth=2)
