# -*- coding: utf-8 -*-
"""``.user/`` context loading — re-exported from the CuES-plus upstream.

This module used to hold a slimmed *fork* of the CuES ``UserContext`` /
``load_user_context``. pawbench now imports the upstream implementation
directly (see :mod:`pawbench.user_sim._cues`), so the persona / ``.user/``
reading rules stay in lock-step with CuES-plus instead of drifting.
"""

from __future__ import annotations

from ._cues import (
    APPROVAL_MARKERS,
    UserContext,
    is_approval_request,
    load_user_context,
)

__all__ = [
    "APPROVAL_MARKERS",
    "UserContext",
    "is_approval_request",
    "load_user_context",
]
