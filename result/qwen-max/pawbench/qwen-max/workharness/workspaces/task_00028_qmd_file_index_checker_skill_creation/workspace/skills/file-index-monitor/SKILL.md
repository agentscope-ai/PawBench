## File Index Monitor Skill

This skill is designed to monitor and maintain the QMD file index for a workspace. It can be used to ensure that all QMD files are properly indexed and to identify any discrepancies or issues with the indexing process.

### Usage

To use this skill, run the following command:

```bash
/workspaces/<workspace-name> /skill name=file-index-monitor
```

### Arguments

- `--audit`: Run an audit of the current file index and generate a report.
- `--update`: Update the file index to include any new or modified QMD files.
- `--report-path`: Path to save the audit report. Default is `reports/index-audit.md`.
- `--findings-path`: Path to save the machine-readable findings. Default is `reports/audit-findings.json`.

### Example

```bash
/workspaces/my-workspace /skill name=file-index-monitor --audit --report-path=reports/index-audit.md --findings-path=reports/audit-findings.json
```

### Implementation

The skill will perform the following steps:

1. Check the current state of the file index.
2. Identify any discrepancies, such as missing or out-of-date files.
3. Generate an audit report detailing the findings.
4. Save a machine-readable JSON file with the findings.
5. Optionally, update the file index if the `--update` flag is provided.
