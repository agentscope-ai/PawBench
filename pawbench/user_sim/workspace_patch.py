"""Deterministic cowork patch application for multi-turn user simulation.

Cowork tasks model a user changing files between assistant turns.  The authored
changes live in Markdown files with YAML front matter, for example::

    ---
    files:
      - path: workspace/index.html
        action: edit
        old: "<html>"
        new: '<html lang="zh-CN">'
    ---

This module applies those changes to a shared workspace with strict path
containment and atomic writes.  It is transport-agnostic: Harbor continues to
use MCP for dialogue, while ACP clients can use the same filesystem semantics.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "PatchApplyError",
    "WorkspacePatchApplier",
    "find_patch_dir",
    "has_cowork_patches",
]

_TURN_PATCH_RE = re.compile(r"^turn_(\d+)(?:_.*)?\.md$")
_ACTIONS = {"create", "overwrite", "append", "edit", "delete"}


class PatchApplyError(RuntimeError):
    """An authored workspace change could not be applied safely."""


def find_patch_dir(task_dir: Path | str) -> Path | None:
    """Return the first supported cowork patch directory under *task_dir*."""
    root = Path(task_dir)
    for relative in (".patch", ".user/patches", "user/patches"):
        candidate = root / relative
        if candidate.is_dir() and any(candidate.glob("turn_*.md")):
            return candidate
    return None


def has_cowork_patches(task: Any) -> bool:
    """Whether a task contains at least one authored turn patch."""
    task_dir = Path(getattr(task, "task_dir", task))
    return find_patch_dir(task_dir) is not None


@dataclass
class WorkspacePatchApplier:
    """Apply authored turn patches inside one contained workspace root."""

    task_dir: Path
    workspace_root: Path

    def __init__(
        self,
        task_dir: Path | str,
        workspace_root: Path | str,
    ) -> None:
        self.task_dir = Path(task_dir).resolve()
        self.workspace_root = Path(workspace_root).resolve()
        self.patch_dir = find_patch_dir(self.task_dir)
        self._applied_turns: set[int] = set()

    @property
    def enabled(self) -> bool:
        return self.patch_dir is not None

    def apply_turn(self, turn: int) -> list[dict[str, Any]]:
        """Apply *turn* once and return a structured operation log."""
        if turn < 1 or turn in self._applied_turns or self.patch_dir is None:
            return []
        patch_path = self._patch_for_turn(turn)
        self._applied_turns.add(turn)
        if patch_path is None:
            return []

        files = self._load_files(patch_path)
        events: list[dict[str, Any]] = []
        for entry in files:
            events.append(self._apply_entry(entry, patch_path=patch_path))
        return events

    def _patch_for_turn(self, turn: int) -> Path | None:
        assert self.patch_dir is not None
        matches: list[Path] = []
        for path in sorted(self.patch_dir.glob("turn_*.md")):
            match = _TURN_PATCH_RE.match(path.name)
            if match and int(match.group(1)) == turn:
                matches.append(path)
        if len(matches) > 1:
            raise PatchApplyError(
                f"multiple cowork patches found for turn {turn}: "
                + ", ".join(path.name for path in matches)
            )
        return matches[0] if matches else None

    @staticmethod
    def _load_files(patch_path: Path) -> list[dict[str, Any]]:
        text = patch_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return []
        lines = text.splitlines()
        try:
            closing = lines[1:].index("---") + 1
        except ValueError as exc:
            raise PatchApplyError(
                f"unterminated YAML front matter in {patch_path}"
            ) from exc
        try:
            metadata = yaml.safe_load("\n".join(lines[1:closing])) or {}
        except yaml.YAMLError as exc:
            raise PatchApplyError(f"invalid YAML in {patch_path}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise PatchApplyError(f"front matter must be a mapping in {patch_path}")
        files = metadata.get("files") or []
        if not isinstance(files, list):
            raise PatchApplyError(f"'files' must be a list in {patch_path}")
        if not all(isinstance(entry, dict) for entry in files):
            raise PatchApplyError(f"every files entry must be a mapping in {patch_path}")
        return files

    def _resolve(self, raw_path: str) -> Path:
        if not raw_path or not raw_path.strip():
            raise PatchApplyError("cowork patch path must not be empty")
        candidate = Path(raw_path)
        base = candidate if candidate.is_absolute() else self.workspace_root / candidate
        resolved = Path(os.path.realpath(base))
        root = Path(os.path.realpath(self.workspace_root))
        if resolved != root and root not in resolved.parents:
            raise PatchApplyError(
                f"cowork patch path {raw_path!r} escapes workspace {root}"
            )
        return resolved

    def _apply_entry(
        self,
        entry: dict[str, Any],
        *,
        patch_path: Path,
    ) -> dict[str, Any]:
        action = str(entry.get("action") or "").strip().lower()
        raw_path = str(entry.get("path") or "")
        if action not in _ACTIONS:
            raise PatchApplyError(
                f"unsupported action {action!r} for {raw_path!r} in {patch_path}"
            )
        target = self._resolve(raw_path)

        if action == "delete":
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
            return {"action": action, "path": raw_path, "status": "applied"}

        if action in {"create", "overwrite"}:
            content = str(entry.get("content") or "")
            self._atomic_write(target, content)
        elif action == "append":
            content = str(entry.get("content") or "")
            existing = (
                target.read_text(encoding="utf-8", errors="replace")
                if target.is_file()
                else ""
            )
            self._atomic_write(target, existing + content)
        elif action == "edit":
            if not target.is_file():
                raise PatchApplyError(f"edit target does not exist: {raw_path!r}")
            old = str(entry.get("old") or "")
            new = str(entry.get("new") or "")
            if not old:
                raise PatchApplyError(f"edit old text must not be empty: {raw_path!r}")
            text = target.read_text(encoding="utf-8", errors="replace")
            count = text.count(old)
            if count == 0:
                # Treat an already-applied replacement as idempotent.
                if new and new in text:
                    return {
                        "action": action,
                        "path": raw_path,
                        "status": "already_applied",
                    }
                fuzzy = self._replace_indentation_flexible(text, old, new)
                if fuzzy is None:
                    raise PatchApplyError(f"edit text not found in {raw_path!r}")
                self._atomic_write(target, fuzzy)
                return {"action": action, "path": raw_path, "status": "applied"}
            if count > 1:
                raise PatchApplyError(
                    f"edit text appears {count} times in {raw_path!r}; "
                    "the authored old text must be unique"
                )
            self._atomic_write(target, text.replace(old, new, 1))

        return {"action": action, "path": raw_path, "status": "applied"}

    @staticmethod
    def _replace_indentation_flexible(
        text: str,
        old: str,
        new: str,
    ) -> str | None:
        """Replace one multiline block while tolerating authored indent drift.

        Builder-generated YAML block scalars sometimes preserve relative
        indentation that differs from the final HTML file.  Content remains
        exact; only leading horizontal whitespace at line starts is flexible.
        Multiple matches are rejected to avoid editing the wrong block.
        """
        if "\n" not in old:
            return None
        old_lines = old.rstrip("\n").splitlines()
        new_lines = new.rstrip("\n").splitlines()
        if not old_lines or len(old_lines) != len(new_lines):
            return None

        actual_lines = text.splitlines(keepends=True)
        starts: list[int] = []
        for start in range(0, len(actual_lines) - len(old_lines) + 1):
            matched = True
            for offset, authored in enumerate(old_lines):
                actual_body = actual_lines[start + offset].rstrip("\r\n")
                # Prefix matching intentionally preserves any unmentioned
                # suffix on a real line (e.g. a builder patch ending at
                # ``id="name"`` while the HTML continues with style attrs).
                if not actual_body.lstrip().startswith(authored.lstrip()):
                    matched = False
                    break
            if matched:
                starts.append(start)
        if len(starts) != 1:
            return None

        start = starts[0]
        replacements: list[str] = []
        for offset, (authored_old, authored_new) in enumerate(
            zip(old_lines, new_lines, strict=True)
        ):
            actual = actual_lines[start + offset]
            body = actual.rstrip("\r\n")
            ending = actual[len(body) :]
            prefix_match = re.match(r"^[ \t]*", body)
            indent = prefix_match.group(0) if prefix_match else ""
            actual_content = body[len(indent) :]
            old_content = authored_old.lstrip()
            suffix = actual_content[len(old_content) :]
            replacements.append(indent + authored_new.lstrip() + suffix + ending)
        if not new.endswith("\n") and replacements:
            replacements[-1] = replacements[-1].rstrip("\r\n")
        return "".join(actual_lines[:start] + replacements + actual_lines[start + len(old_lines) :])

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp_name, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
