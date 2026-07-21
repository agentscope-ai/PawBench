# Runtime Diagnostics for OpenClaw

This skill provides a repeatable procedure for auditing the runtime health of an OpenClaw gateway. The audit includes cross-validating state files, parsing logs for warnings, and computing a health score.

## Procedure

1. **Cross-Validation of State Files**
   - Read `.openclaw/state/process.json`, `.openclaw/state/gateway.pid`, and `.openclaw/state/active-sessions.json`.
   - Cross-validate the PID and active session count across these files, flagging any discrepancies.

2. **Log Parsing**
   - Parse `.openclaw/logs/gateway.log` to count `WARN  memory` events and `WARN  session: Session file growing` events.

3. **Session File Inventory**
   - Count all `.jsonl` files in `sessions/` and break down the count by session type extracted from the filename (format: `YYYYMMDD_TYPE_ID.jsonl`).

4. **Health Score Calculation**
   - Compute the health score using the following formula:
     
     ```
     health_score = 100 - (memory_warn_count * 1) - (state_inconsistency_count * 15) - (oversized_session_warn_count * 1)
     ```
     where
     - `memory_warn_count` is the number of `WARN  memory` lines in `gateway.log`,
     - `state_inconsistency_count` is the total number of cross-file inconsistencies found,
     - `oversized_session_warn_count` is the number of `WARN  session: Session file growing` lines in `gateway.log`.

5. **Output Files**
   - Write the results to two files:
     - `runtime-audit.json`: A machine-readable JSON with specific top-level keys as described below.
     - `runtime-audit.md`: A human-readable report summarizing the findings, including a dedicated section for inconsistencies and a recovery procedure.

### Output Keys for `runtime-audit.json`
- `gateway_pid`
- `pid_in_pidfile`
- `pid_consistent`
- `version`
- `uptime`
- `memory_current_mb`
- `memory_warn_count`
- `memory_max_mb`
- `active_session_count_process_json`
- `active_session_count_in_file`
- `session_count_consistent`
- `total_session_files`
- `session_count_by_type`
- `oversized_session_warn_count`
- `state_inconsistency_count`
- `state_inconsistencies`
- `health_score`
- `recovery_command`