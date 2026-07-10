# -*- coding: utf-8 -*-
"""Bridge to the CuES-plus user agent (``examples/CuES-plus/src``).

Instead of maintaining a slimmed *fork* of the CuES user simulator, pawbench
reuses the upstream implementation directly:

* ``UserAgent`` dialogue + approval logic   (``src/client/user_agent.py``)
* ``UserContext`` / ``load_user_context``    (same module)
* the user-agent prompt builders            (``src/runtime/prompts.py``)

Two things are handled here so the lightweight user-sim sidecar can import the
leaf module without dragging in the heavy CuES data-generation stack:

1. **Locate the source tree.** On the host it lives at
   ``<repo>/examples/CuES-plus``; inside the sidecar image it is vendored and
   its location is given by ``$CUES_PLUS_ROOT`` (a directory that contains the
   ``src`` package, i.e. ``$CUES_PLUS_ROOT/src/client/user_agent.py`` exists).
2. **Shadow the heavy package ``__init__``.** ``src.client.__init__`` eagerly
   imports the OSS / E2B / QwenPaw runner stack (``user_client`` /
   ``task_source``). We register a lightweight synthetic ``src.client`` package
   in ``sys.modules`` *before* importing ``src.client.user_agent`` so that heavy
   ``__init__`` never runs; only the pure ``user_agent`` leaf module and its
   light ``src.runtime`` / ``src.defaults`` dependencies are loaded.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path

__all__ = [
    "UpstreamUserAgent",
    "UserContext",
    "load_user_context",
    "is_approval_request",
    "APPROVAL_MARKERS",
    "build_user_agent_system_prompt",
    "build_user_agent_approval_prompt",
    "cues_root",
]

# Relative location of the marker file that proves a directory is a CuES root.
_MARKER = ("src", "client", "user_agent.py")


def _candidate_roots() -> list[Path]:
    """Return candidate directories that may contain the ``src`` package."""
    roots: list[Path] = []
    env = os.environ.get("CUES_PLUS_ROOT")
    if env:
        roots.append(Path(env))
    here = Path(__file__).resolve()
    for parent in here.parents:
        roots.append(parent / "examples" / "CuES-plus")
    return roots


def _locate_root() -> Path:
    for root in _candidate_roots():
        try:
            if root.joinpath(*_MARKER).is_file():
                return root
        except OSError:
            continue
    raise ImportError(
        "Could not locate the CuES-plus source tree. Set $CUES_PLUS_ROOT to a "
        "directory that contains the 'src' package (with "
        "src/client/user_agent.py)."
    )


def _install_client_shim(root: Path) -> None:
    """Register a lightweight ``src.client`` package to skip its heavy __init__."""
    if "src.client" in sys.modules:
        return
    client_dir = root / "src" / "client"
    shim = types.ModuleType("src.client")
    shim.__path__ = [str(client_dir)]  # type: ignore[attr-defined]
    shim.__package__ = "src.client"
    sys.modules["src.client"] = shim


cues_root = _locate_root()
if str(cues_root) not in sys.path:
    sys.path.insert(0, str(cues_root))

# Import the (light) top-level package, then shadow the heavy client subpackage
# before importing the leaf module we actually need.
importlib.import_module("src")
_install_client_shim(cues_root)

_ua = importlib.import_module("src.client.user_agent")
_prompts = importlib.import_module("src.runtime.prompts")

UpstreamUserAgent = _ua.UserAgent
UserContext = _ua.UserContext
load_user_context = _ua.load_user_context
is_approval_request = _ua.is_approval_request
APPROVAL_MARKERS = _ua.APPROVAL_MARKERS
build_user_agent_system_prompt = _prompts.build_user_agent_system_prompt
build_user_agent_approval_prompt = _prompts.build_user_agent_approval_prompt
