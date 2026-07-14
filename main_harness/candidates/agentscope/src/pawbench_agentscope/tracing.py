from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.security import redact_sensitive_value


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
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_events: list[dict[str, Any]] = []
        if append and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    existing_events.append(json.loads(line))
        if not append:
            self.path.write_text("", encoding="utf-8")
        self.task_id = task_id
        self.run_id = run_id or (existing_events[-1]["run_id"] if existing_events else uuid4().hex)
        self.event_index = max((int(event.get("event_index", 0)) for event in existing_events), default=0)
        self.last_event_id = existing_events[-1].get("event_id") if existing_events else None
        self.diagnostic_enabled = diagnostic_enabled

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        diagnostic: bool = False,
        parent_event_id: str | None = None,
    ) -> dict[str, Any]:
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
        self.event_index += 1
        event_id = f"{self.run_id}:{self.event_index}"
        safe_payload = redact_sensitive_value(payload)
        row = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "event_index": self.event_index,
            "event_id": event_id,
            "parent_event_id": parent_event_id if parent_event_id is not None else self.last_event_id,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "type": event_type,
            "status": str(safe_payload.get("status", "recorded")),
            "audit_class": "diagnostic" if diagnostic else "outer",
            "payload": safe_payload,
        }
        row = redact_sensitive_value(row)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self.last_event_id = event_id
        return row

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
