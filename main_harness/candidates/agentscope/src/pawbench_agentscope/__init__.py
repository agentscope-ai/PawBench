"""Public AgentScope harness exports.

The core runtime is intentionally checkout-backed: its taxonomy remains the
canonical ``main_harness/scripts/feature_taxonomy.py`` module.  Keep that
import lazy so the separately installed Feature-development command can show
its help and accept an explicit PawBench checkout without assuming the package
was installed in the source tree.
"""

from __future__ import annotations

from typing import Any

__all__ = ["FEATURE_IDS", "FeatureConfig", "TAXONOMY_VERSION"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from pawbench_agentscope.features import (
        FEATURE_IDS,
        TAXONOMY_VERSION,
        FeatureConfig,
    )

    return {
        "FEATURE_IDS": FEATURE_IDS,
        "FeatureConfig": FeatureConfig,
        "TAXONOMY_VERSION": TAXONOMY_VERSION,
    }[name]
