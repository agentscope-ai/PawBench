from __future__ import annotations

import json
import os
import stat
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pawbench_agentscope._portable_security import redact_sensitive_value


MAX_TRACE_BYTES = 64 * 1024 * 1024


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate trace JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite trace JSON constant: {value}")


def _loads_trace_row(line: str) -> Any:
    return json.loads(
        line,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )


class TraceWriter:
    def __init__(
        self,
        path: Path,
        *,
        task_id: str,
        run_id: str | None = None,
        append: bool = False,
        diagnostic_enabled: bool = True,
    ):
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("trace task_id must be a non-empty string")
        if run_id is not None and (not isinstance(run_id, str) or not run_id):
            raise ValueError("trace run_id must be a non-empty string when provided")
        if not isinstance(diagnostic_enabled, bool):
            raise ValueError("diagnostic_enabled must be boolean")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        existing_events: list[dict[str, Any]] = []
        if append and self.path.exists():
            previous_event_id: str | None = None
            expected_index = 1
            existing_run_id: str | None = None
            observed_event_ids: set[str] = set()
            for line_number, line in enumerate(self._read_text_no_follow().splitlines(), start=1):
                if line.strip():
                    value = _loads_trace_row(line)
                    if not isinstance(value, dict):
                        raise ValueError(f"trace row must be an object at line {line_number}")
                    if value.get("task_id") != task_id:
                        raise ValueError(f"trace task_id mismatch at line {line_number}")
                    observed_run_id = value.get("run_id")
                    if not isinstance(observed_run_id, str) or not observed_run_id:
                        raise ValueError(f"trace run_id is invalid at line {line_number}")
                    if existing_run_id is None:
                        existing_run_id = observed_run_id
                    elif observed_run_id != existing_run_id:
                        raise ValueError(f"trace run_id mismatch at line {line_number}")
                    if value.get("event_index") != expected_index:
                        raise ValueError(f"trace event_index is invalid at line {line_number}")
                    event_id = value.get("event_id")
                    if not isinstance(event_id, str) or not event_id:
                        raise ValueError(f"trace event_id is invalid at line {line_number}")
                    if event_id in observed_event_ids:
                        raise ValueError(f"trace event_id is duplicated at line {line_number}")
                    if event_id != f"{observed_run_id}:{expected_index}":
                        raise ValueError(f"trace event_id is inconsistent at line {line_number}")
                    if value.get("parent_event_id") != previous_event_id:
                        raise ValueError(f"trace parent chain is invalid at line {line_number}")
                    if not isinstance(value.get("type"), str) or not value["type"]:
                        raise ValueError(f"trace event type is invalid at line {line_number}")
                    if not isinstance(value.get("payload"), dict):
                        raise ValueError(f"trace payload is invalid at line {line_number}")
                    if not isinstance(value.get("timestamp"), str) or not value["timestamp"]:
                        raise ValueError(f"trace timestamp is invalid at line {line_number}")
                    try:
                        datetime.fromisoformat(value["timestamp"].replace("Z", "+00:00"))
                    except ValueError as exc:
                        raise ValueError(
                            f"trace timestamp is invalid at line {line_number}"
                        ) from exc
                    previous_event_id = event_id
                    observed_event_ids.add(event_id)
                    expected_index += 1
                    existing_events.append(value)
            if run_id is not None and existing_run_id is not None and run_id != existing_run_id:
                raise ValueError("requested run_id does not match appended trace")
        if not append:
            self._truncate_no_follow()
        self.task_id = task_id
        self.run_id = run_id or (existing_events[-1]["run_id"] if existing_events else uuid4().hex)
        self.event_index = max((int(event.get("event_index", 0)) for event in existing_events), default=0)
        self.last_event_id = existing_events[-1].get("event_id") if existing_events else None
        self.diagnostic_enabled = diagnostic_enabled

    @staticmethod
    def _no_follow_flag() -> int:
        return int(getattr(os, "O_NOFOLLOW", 0))

    def _open_fd(self, flags: int) -> int:
        if self.path.is_symlink():
            raise ValueError(f"trace path must not be a symlink: {self.path}")
        try:
            fd = os.open(
                self.path,
                flags | self._no_follow_flag() | int(getattr(os, "O_NONBLOCK", 0)),
                0o600,
            )
        except OSError as exc:
            if self.path.is_symlink():
                raise ValueError(f"trace path must not be a symlink: {self.path}") from exc
            raise
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(fd)
            raise ValueError(f"trace path must be a regular file: {self.path}")
        return fd

    def _truncate_no_follow(self) -> None:
        fd = self._open_fd(os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        os.close(fd)

    def _read_text_no_follow(self) -> str:
        fd = self._open_fd(os.O_RDONLY)
        try:
            metadata = os.fstat(fd)
            if metadata.st_size > MAX_TRACE_BYTES:
                raise ValueError(f"trace exceeds {MAX_TRACE_BYTES} bytes: {self.path}")
            chunks: list[bytes] = []
            remaining = MAX_TRACE_BYTES + 1
            while remaining > 0:
                chunk = os.read(fd, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > MAX_TRACE_BYTES:
                raise ValueError(f"trace exceeds {MAX_TRACE_BYTES} bytes: {self.path}")
            return data.decode("utf-8")
        finally:
            os.close(fd)

    def _append_line_no_follow(self, line: str) -> None:
        encoded = line.encode("utf-8")
        fd = self._open_fd(os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            current_size = os.fstat(fd).st_size
            if current_size + len(encoded) > MAX_TRACE_BYTES:
                raise ValueError(f"trace exceeds {MAX_TRACE_BYTES} bytes: {self.path}")
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("trace append made no forward progress")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        diagnostic: bool = False,
        parent_event_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("trace event_type must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("trace payload must be an object")
        if diagnostic and not self.diagnostic_enabled:
            return redact_sensitive_value(
                {
                    "run_id": self.run_id,
                    "task_id": self.task_id,
                    "type": event_type,
                    "omitted": True,
                    "reason": "F4.1_controlled_off",
                }
            )
        with self._lock:
            next_index = self.event_index + 1
            event_id = f"{self.run_id}:{next_index}"
            safe_payload = redact_sensitive_value(payload)
            row = {
                "run_id": self.run_id,
                "task_id": self.task_id,
                "event_index": next_index,
                "event_id": event_id,
                "parent_event_id": parent_event_id if parent_event_id is not None else self.last_event_id,
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "type": event_type,
                "status": str(safe_payload.get("status", "recorded")),
                "audit_class": "diagnostic" if diagnostic else "outer",
                "payload": safe_payload,
            }
            row = redact_sensitive_value(row)
            self._append_line_no_follow(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    default=str,
                    allow_nan=False,
                )
                + "\n"
            )
            self.event_index = next_index
            self.last_event_id = event_id
            return row

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            _loads_trace_row(line)
            for line in self._read_text_no_follow().splitlines()
            if line.strip()
        ]
