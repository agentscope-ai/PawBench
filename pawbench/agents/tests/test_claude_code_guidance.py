from __future__ import annotations

import base64
import shlex
import zipfile

from pawbench.agents.claude_code_guidance import (
    CLAUDE_CODE_EXECUTION_GUIDANCE,
    merge_claude_code_guidance,
    quoted_claude_code_guidance,
)


def test_guidance_covers_observed_failure_modes() -> None:
    guidance = CLAUDE_CODE_EXECUTION_GUIDANCE

    assert "`workspace/foo`" in guidance
    assert "<cwd>/workspace/foo" in guidance
    assert "XLSX" in guidance
    assert "never guess" in guidance
    assert "directly consumable values" in guidance
    assert "accompanying schema" in guidance


def test_merge_preserves_caller_prompt() -> None:
    merged = merge_claude_code_guidance("Keep the user's terminology.")

    assert merged.startswith("Keep the user's terminology.")
    assert merged.count("PawBench task execution rules:") == 1


def test_harbor_claude_code_accepts_guidance_flag(tmp_path) -> None:
    from harbor.agents.installed.claude_code import ClaudeCode

    guidance = merge_claude_code_guidance()
    agent = ClaudeCode(
        logs_dir=tmp_path,
        model_name="qwen3.6-plus",
        append_system_prompt=quoted_claude_code_guidance(),
    )

    flags = agent.build_cli_flags()
    assert "--append-system-prompt" in flags
    assert guidance in shlex.split(flags)


def test_pawbench_wrapper_prepares_and_reconciles_workspace() -> None:
    from pawbench.agents.impl.pawbench_claude_code import (
        PawBenchClaudeCode,
        materialize_parameter_values,
    )

    prepare = PawBenchClaudeCode._prepare_workspace_command()
    reconcile = PawBenchClaudeCode._reconcile_workspace_command()

    assert 'root="$(pwd)"' in prepare
    assert "CLAUDE.md" in prepare
    assert "pawbench-claude-code-run-start" in prepare
    assert 'root="$(pwd)"' in reconcile
    assert "python3" in reconcile
    assert "base64 -d | python3" in reconcile
    encoded_script = max(shlex.split(reconcile), key=len)
    script = base64.b64decode(encoded_script).decode("utf-8")
    compile(script, "<reconcile-workspace>", "exec")
    assert 'dest = root / "workspace"' in script
    assert "requested = set(re.findall" in script
    assert '"schema" in lowered' in script

    metadata_config = {
        "algorithm": {
            "learning_rate": {
                "value": 0.0003,
                "type": "float",
                "minimum": 1e-6,
                "maximum": 0.01,
                "description": "Optimizer learning rate",
            },
            "batch_size": {
                "default": 256,
                "type": "integer",
                "range": {"minimum": 32, "maximum": 1024},
                "description": "Minibatch size",
            },
        }
    }
    assert materialize_parameter_values(metadata_config) == {
        "algorithm": {"learning_rate": 0.0003, "batch_size": 256}
    }


def test_binary_asset_extraction_creates_readable_xlsx_mirror(tmp_path) -> None:
    from pawbench.agents.impl.pawbench_claude_code import extract_binary_assets

    source = tmp_path / "workspace" / "scores.xlsx"
    source.parent.mkdir()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<si><t>Vendor</t></si><si><t>GlobalSync</t></si></sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
            '<c r="B1"><v>91.5</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>1</v></c></row></sheetData></worksheet>',
        )

    extract_binary_assets(str(tmp_path))

    mirror = tmp_path / ".pawbench-extracted" / "workspace" / "scores.xlsx.txt"
    assert mirror.exists()
    assert "Vendor\t91.5" in mirror.read_text(encoding="utf-8")
    assert "GlobalSync" in mirror.read_text(encoding="utf-8")
