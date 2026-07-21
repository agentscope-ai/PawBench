from __future__ import annotations

import json
import ast
from pathlib import Path
import tomllib

import pytest

from pawbench_agentscope._portable_attribution_bridge import (
    bridge_row as portable_bridge_row,
    load_manifests as load_portable_manifests,
)
from pawbench_agentscope.harbor_bridge import AGENT_VERSION
try:
    from scripts.bridge_attribution_to_harness_core import bridge_row as canonical_bridge_row
except ModuleNotFoundError:
    canonical_bridge_row = None


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
MAIN_HARNESS_ROOT = CANDIDATE_ROOT.parents[1]
PACKAGE_ROOT = CANDIDATE_ROOT / "src" / "pawbench_agentscope"


@pytest.mark.parametrize(
    ("canonical_name", "portable_name"),
    [
        ("security.py", "_portable_security.py"),
        ("feature_taxonomy.py", "_portable_taxonomy.py"),
    ],
)
def test_portable_contract_mirrors_are_byte_identical(
    canonical_name: str,
    portable_name: str,
) -> None:
    canonical = MAIN_HARNESS_ROOT / "scripts" / canonical_name
    portable = PACKAGE_ROOT / portable_name
    if not canonical.is_file():
        pytest.skip("canonical Harness-core source is not part of the standalone agent PR")
    assert portable.read_bytes() == canonical.read_bytes()


def test_packaged_feature_manifest_is_byte_identical() -> None:
    assert (PACKAGE_ROOT / "feature_manifest.json").read_bytes() == (
        CANDIDATE_ROOT / "feature_manifest.json"
    ).read_bytes()


def test_package_cli_and_reference_wrapper_versions_stay_aligned() -> None:
    project = tomllib.loads((CANDIDATE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == AGENT_VERSION
    wrapper_path = CANDIDATE_ROOT / "harbor_adapter" / "reference_harbor_agent.py"
    tree = ast.parse(wrapper_path.read_text(encoding="utf-8"))
    versions = [
        node.body[0].value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "version"
        and len(node.body) == 1
        and isinstance(node.body[0], ast.Return)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ]
    assert versions == [AGENT_VERSION]


def test_reference_wrapper_declares_harbor_capability_boundaries() -> None:
    wrapper_path = CANDIDATE_ROOT / "harbor_adapter" / "reference_harbor_agent.py"
    tree = ast.parse(wrapper_path.read_text(encoding="utf-8"))
    wrapper = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "HarnessCoreAgentScope"
    )
    constants = {
        target.id: statement.value.value
        for statement in wrapper.body
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance((target := statement.targets[0]), ast.Name)
        and isinstance(statement.value, ast.Constant)
    }

    assert constants["SUPPORTS_ATIF"] is True
    assert constants["SUPPORTS_RESUME"] is False
    assert constants["SUPPORTS_WINDOWS"] is False


def test_release_build_and_runtime_dependency_ranges_are_bounded() -> None:
    project = tomllib.loads((CANDIDATE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["build-system"]["requires"] == [
        "setuptools==83.0.0",
        "wheel==0.47.0",
    ]
    assert project["project"]["dependencies"] == [
        "agentscope>=2.0.4,<3",
        "pydantic>=2.13,<3",
    ]
    assert (CANDIDATE_ROOT / "scripts" / "build_release_wheel.py").is_file()


def test_package_runtime_has_no_repository_scripts_imports() -> None:
    offenders: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from scripts." in text or "import scripts." in text:
            offenders.append(str(path.relative_to(PACKAGE_ROOT)))
    assert offenders == []


def test_portable_manifest_loader_uses_packaged_contract() -> None:
    manifest = load_portable_manifests("agentscope")["agentscope"]
    assert manifest["candidate"] == "AgentScope-Lab"
    assert manifest["_candidate_dir"] == "agentscope"
    assert manifest["_manifest_path"].startswith("package:")
    assert len(manifest["features"]) == 15
    with pytest.raises(ValueError, match="No feature manifests"):
        load_portable_manifests("unknown")


def test_portable_bridge_matches_canonical_for_all_h_families() -> None:
    if canonical_bridge_row is None:
        pytest.skip("canonical Harness-core bridge is not part of the standalone agent PR")
    manifest = json.loads((CANDIDATE_ROOT / "feature_manifest.json").read_text(encoding="utf-8"))
    manifest["_candidate_dir"] = "agentscope"
    rows = [
        {
            "run_group": "parity",
            "harness": "AgentScope-Lab",
            "model": "test",
            "task_id": "ua-parity-001",
            "codes": ["H1", "Ex-3"],
            "evidence": "workspace cwd reset and permission policy failed",
            "features": {"hits": {"workspace": 1}, "anomaly": "mount"},
        },
        {
            "run_group": "parity",
            "harness": "AgentScope-Lab",
            "model": "test",
            "task_id": "ua-parity-002",
            "codes": ["H2"],
            "evidence": "tool registry unavailable and malformed error feedback",
        },
        {
            "run_group": "parity",
            "harness": "AgentScope-Lab",
            "model": "test",
            "task_id": "ws-parity-003",
            "codes": ["H3"],
            "evidence": "timeout budget cutoff prevented retry and resume",
        },
        {
            "run_group": "parity",
            "harness": "AgentScope-Lab",
            "model": "test",
            "task_id": "ws-parity-004",
            "codes": ["H4"],
            "evidence": "missing diagnostic trace and verifier false-success gate",
        },
        {
            "run_group": "parity",
            "harness": "AgentScope-Lab",
            "model": "test",
            "task_id": "ma-parity-005",
            "codes": ["H5"],
            "evidence": "context assembly lost persistent memory after compaction",
        },
        {
            "run_group": "parity",
            "harness": "AgentScope-Lab",
            "model": "test",
            "task_id": "ma-parity-006",
            "codes": ["Ex-1", "M4"],
            "evidence": "no harness evidence",
        },
    ]
    for row in rows:
        assert portable_bridge_row(row, manifest) == canonical_bridge_row(row, manifest)
