import ast
import re
from pathlib import Path

from pawbench.task_loader import TaskLoader
from unified_planning.io import PDDLReader
from unified_planning.shortcuts import OneshotPlanner, get_environment

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BLOCK_RE = re.compile(r"```python\s*\n(.*?)\n\s*```", re.DOTALL)
DISTRIBUTION_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def test_embedded_grader_dependencies_are_declared() -> None:
    requirements = _requirements_distributions(ROOT / "requirements.txt")
    tasks_dir = ROOT / "data" / "pawbench-v1.0" / "tasks"
    tasks = TaskLoader(tasks_dir).load_all_tasks()

    declared_by_graders = {
        package
        for task in tasks
        for package in _grader_python_packages(task.automated_checks or "")
    }

    assert declared_by_graders <= requirements, (
        "Embedded automated checkers use undeclared Python packages: "
        f"{sorted(declared_by_graders - requirements)}"
    )


def test_t136_pyperplan_dependency_can_solve_fixture() -> None:
    get_environment().credits_stream = None
    fixture_dir = ROOT / "data" / "pawbench-v1.0" / "assets" / "T136_skillsbench_pddl-tpp-planning" / "tpp"
    problem = PDDLReader().parse_problem(fixture_dir / "domain.pddl", fixture_dir / "task01.pddl")

    with OneshotPlanner(name="pyperplan") as planner:
        result = planner.solve(problem)

    assert result.plan is not None
    assert result.plan.actions


def _requirements_distributions(path: Path) -> set[str]:
    packages: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = DISTRIBUTION_RE.match(line)
        if match:
            packages.add(match.group(1))
    return packages


def _grader_python_packages(automated_checks: str) -> set[str]:
    match = PYTHON_BLOCK_RE.search(automated_checks)
    if not match:
        return set()
    tree = ast.parse(match.group(1))
    packages: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        command = node.args[0]
        if not isinstance(command, (ast.List, ast.Tuple)):
            continue
        values = [
            item.value
            for item in command.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        try:
            install_index = values.index("install")
        except ValueError:
            continue
        if "pip" not in values[:install_index]:
            continue
        packages.update(value for value in values[install_index + 1 :] if not value.startswith("-"))
    return packages
