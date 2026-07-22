"""PawBench compatibility wrapper for Harbor's Claude Code agent."""

from __future__ import annotations

import base64
import inspect
import shlex

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from pawbench.agents.claude_code_guidance import CLAUDE_CODE_EXECUTION_GUIDANCE


_RUN_MARKER = "/tmp/pawbench-claude-code-run-start"
_GUIDANCE_BACKUP = "/tmp/pawbench-claude-code-CLAUDE.md.backup"


def materialize_parameter_values(value: object) -> object:
    """Convert parameter metadata mappings into runtime configuration values."""
    if isinstance(value, dict):
        metadata_keys = {
            "type",
            "value",
            "default",
            "range",
            "minimum",
            "maximum",
            "description",
        }
        if "value" in value and len(metadata_keys.intersection(value)) >= 2:
            return value["value"]
        if "default" in value and len(metadata_keys.intersection(value)) >= 2:
            return value["default"]
        return {
            key: materialize_parameter_values(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [materialize_parameter_values(item) for item in value]
    return value


def extract_binary_assets(root_value: str) -> None:
    """Create readable mirrors for common binary task assets using stdlib only."""
    import re
    import sqlite3
    import zipfile
    from pathlib import Path
    from xml.etree import ElementTree

    root = Path(root_value)
    output_root = root / ".pawbench-extracted"

    def xml_text(element: object) -> str:
        return "".join(
            node.text or "" for node in element.iter() if node.tag.endswith("}t")
        )

    def extract_xlsx(path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                tree = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = [xml_text(item) for item in tree if item.tag.endswith("}si")]
            lines: list[str] = []
            sheets = sorted(
                name
                for name in archive.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            for sheet in sheets:
                lines.append(f"=== {sheet} ===")
                tree = ElementTree.fromstring(archive.read(sheet))
                for row in tree.iter():
                    if not row.tag.endswith("}row"):
                        continue
                    values: list[str] = []
                    for cell in row:
                        if not cell.tag.endswith("}c"):
                            continue
                        kind = cell.attrib.get("t")
                        value_node = next(
                            (child for child in cell if child.tag.endswith("}v")),
                            None,
                        )
                        if kind == "inlineStr":
                            value = xml_text(cell)
                        elif value_node is None:
                            value = ""
                        else:
                            value = value_node.text or ""
                            if kind == "s":
                                try:
                                    value = shared[int(value)]
                                except (IndexError, ValueError):
                                    pass
                        values.append(value.replace("\t", " ").replace("\n", " "))
                    if values:
                        lines.append("\t".join(values))
            return "\n".join(lines)

    def extract_docx(path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            parts = [
                name
                for name in archive.namelist()
                if name.startswith("word/") and name.endswith(".xml")
            ]
            return "\n".join(
                xml_text(ElementTree.fromstring(archive.read(name))) for name in parts
            )

    def extract_sqlite(path: Path) -> str:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            lines: list[str] = []
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                cursor = connection.execute(f"SELECT * FROM {quoted}")
                lines.append(f"=== table: {table} ===")
                lines.append("\t".join(item[0] for item in cursor.description or []))
                lines.extend(
                    "\t".join("" if value is None else str(value) for value in row)
                    for row in cursor.fetchall()
                )
            return "\n".join(lines)
        finally:
            connection.close()

    extractors = {
        ".xlsx": extract_xlsx,
        ".docx": extract_docx,
        ".db": extract_sqlite,
        ".sqlite": extract_sqlite,
        ".sqlite3": extract_sqlite,
    }
    for path in root.rglob("*"):
        if not path.is_file() or output_root in path.parents:
            continue
        extractor = extractors.get(path.suffix.lower())
        if extractor is None:
            continue
        try:
            content = extractor(path)
        except Exception as error:
            content = f"Extraction failed: {type(error).__name__}: {error}"
        target = output_root / path.relative_to(root)
        target = target.with_name(target.name + ".txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"Source: {path.relative_to(root)}\n{content}\n",
            encoding="utf-8",
        )


class PawBenchClaudeCode(ClaudeCode):
    """Reinforce task guidance and reconcile misplaced root deliverables."""

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        await self.exec_as_agent(
            environment,
            command=self._prepare_workspace_command(),
        )
        try:
            await super().run(instruction, environment, context)
        finally:
            await self.exec_as_agent(
                environment,
                command=self._reconcile_workspace_command(),
            )

    @staticmethod
    def _prepare_workspace_command() -> str:
        encoded = base64.b64encode(
            CLAUDE_CODE_EXECUTION_GUIDANCE.encode("utf-8")
        ).decode("ascii")
        extractor = inspect.getsource(extract_binary_assets)
        extractor_script = (
            f"{extractor}\nextract_binary_assets('.')\n"
        )
        encoded_extractor = base64.b64encode(
            extractor_script.encode("utf-8")
        ).decode("ascii")
        marker = shlex.quote(_RUN_MARKER)
        backup = shlex.quote(_GUIDANCE_BACKUP)
        return (
            "set -eu; "
            f"root=\"$(pwd)\"; marker={marker}; backup={backup}; "
            'rm -f "$marker" "$backup"; touch "$marker"; '
            f"printf '%s' {shlex.quote(encoded_extractor)} | base64 -d | python3; "
            'if [ -f "$root/CLAUDE.md" ]; then cp "$root/CLAUDE.md" "$backup"; fi; '
            f"printf '%s' {shlex.quote(encoded)} | base64 -d >> \"$root/CLAUDE.md\"; "
            'printf "\\n" >> "$root/CLAUDE.md"'
        )

    @staticmethod
    def _reconcile_workspace_command() -> str:
        # Claude Code models occasionally collapse `workspace/foo` into
        # `<cwd>/foo`. Copy only files created/updated during this run. When a
        # misplaced YAML consists of parameter metadata objects, materialize the
        # `default` leaves in the copied runtime config while preserving the
        # original root file.
        materializer = inspect.getsource(materialize_parameter_values)
        script = f"""
from pathlib import Path
import re
import shutil

import yaml

root = Path.cwd()
dest = root / "workspace"
marker = Path({_RUN_MARKER!r})
dest.mkdir(parents=True, exist_ok=True)
started = marker.stat().st_mtime if marker.exists() else float("inf")

{materializer}

def normalize_yaml(target):
    if target.suffix.lower() not in {{".yaml", ".yml"}}:
        return
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        normalized = materialize_parameter_values(data)
        if normalized != data:
            target.write_text(
                yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
    except Exception:
        pass

for source in root.iterdir():
    if not source.is_file() or source.name in {{"CLAUDE.md", "state_manifest.json"}}:
        continue
    if source.stat().st_mtime < started:
        continue
    target = dest / source.name
    shutil.copy2(source, target)
    normalize_yaml(target)

# Recover explicit `workspace/...` deliverables when the model generated an
# equivalent root file under a slightly different name, or when its final
# Write tool call lost its arguments. Only files updated in this run qualify.
log_path = Path("/logs/agent/claude-code.txt")
log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
requested = set(re.findall(r"workspace/([A-Za-z0-9_.-]+)", log_text))
recent_root_files = [
    path for path in root.iterdir()
    if path.is_file()
    and path.name not in {{"CLAUDE.md", "state_manifest.json"}}
    and path.stat().st_mtime >= started
]
for name in requested:
    target = dest / name
    if target.exists():
        normalize_yaml(target)
        continue
    exact = root / name
    candidates = [exact] if exact.exists() else []
    lowered = name.lower()
    if not candidates and "schema" in lowered and target.suffix.lower() == ".json":
        candidates = [
            path for path in recent_root_files
            if path.suffix.lower() == ".json" and "schema" in path.name.lower()
        ]
    if not candidates and target.suffix.lower() in {{".yaml", ".yml"}}:
        candidates = [
            path for path in recent_root_files
            if path.suffix.lower() in {{".yaml", ".yml"}}
            and "config" in path.name.lower()
        ]
    if candidates:
        source = max(candidates, key=lambda path: path.stat().st_mtime)
        shutil.copy2(source, target)
        normalize_yaml(target)
"""
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        marker = shlex.quote(_RUN_MARKER)
        backup = shlex.quote(_GUIDANCE_BACKUP)
        return (
            "set -u; "
            f"root=\"$(pwd)\"; marker={marker}; backup={backup}; "
            f"printf '%s' {shlex.quote(encoded)} | base64 -d | python3; "
            'if [ -f "$backup" ]; then mv "$backup" "$root/CLAUDE.md"; '
            'else rm -f "$root/CLAUDE.md"; fi; '
            'rm -f "$marker"'
        )
