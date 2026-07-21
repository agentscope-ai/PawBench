from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pawbench_agentscope._atomic_io import (
    append_text_durable,
    prepare_marked_output,
    read_text_no_follow,
)


def test_durable_append_handles_partial_os_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    real_write = os.write

    def partial_write(fd: int, data: bytes | memoryview) -> int:
        return real_write(fd, data[:3])

    monkeypatch.setattr(os, "write", partial_write)
    append_text_durable(path, '{"event":"complete"}\n')

    assert path.read_text(encoding="utf-8") == '{"event":"complete"}\n'


def test_durable_append_serializes_concurrent_threads(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"

    def emit(worker: int) -> None:
        for index in range(20):
            append_text_durable(
                path,
                json.dumps({"worker": worker, "index": index}) + "\n",
            )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(emit, range(16)))

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 320
    assert {(row["worker"], row["index"]) for row in rows} == {
        (worker, index) for worker in range(16) for index in range(20)
    }


def test_durable_append_refuses_symlink_target(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.write_text("unchanged\n", encoding="utf-8")
    link = tmp_path / "events.jsonl"
    link.symlink_to(external)

    with pytest.raises(ValueError, match="must not be a symlink"):
        append_text_durable(link, "malicious\n")

    assert external.read_text(encoding="utf-8") == "unchanged\n"


def test_marked_output_replaces_only_exact_owned_directory(tmp_path: Path) -> None:
    output = tmp_path / "output"
    marker_text = "owned/v1\n"
    prepare_marked_output(
        output,
        marker_name=".owned",
        marker_text=marker_text,
        replace=False,
    )
    (output / "old.txt").write_text("old", encoding="utf-8")

    prepare_marked_output(
        output,
        marker_name=".owned",
        marker_text=marker_text,
        replace=True,
    )

    assert not (output / "old.txt").exists()
    assert (output / ".owned").read_text(encoding="utf-8") == marker_text


def test_marked_output_refuses_marker_symlink(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    external = tmp_path / "external"
    external.write_text("owned/v1\n", encoding="utf-8")
    (output / ".owned").symlink_to(external)
    victim = output / "keep.txt"
    victim.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to replace unmarked"):
        prepare_marked_output(
            output,
            marker_name=".owned",
            marker_text="owned/v1\n",
            replace=True,
        )

    assert victim.read_text(encoding="utf-8") == "keep"


def test_bounded_read_rejects_oversized_regular_file(tmp_path: Path) -> None:
    source = tmp_path / "large.txt"
    source.write_text("abcdef", encoding="utf-8")

    with pytest.raises(ValueError, match="input exceeds 5 bytes"):
        read_text_no_follow(source, max_bytes=5)


def test_bounded_read_refuses_non_regular_file(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()

    with pytest.raises((IsADirectoryError, ValueError)):
        read_text_no_follow(directory, max_bytes=5)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_safe_io_refuses_fifo_without_blocking(tmp_path: Path) -> None:
    source = tmp_path / "input.fifo"
    os.mkfifo(source)
    with pytest.raises(ValueError, match="regular file"):
        read_text_no_follow(source, max_bytes=5)

    destination = tmp_path / "events.jsonl"
    os.mkfifo(destination)
    with pytest.raises(ValueError, match="append path must be a regular file"):
        append_text_durable(destination, "event\n")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_durable_append_refuses_fifo_lock_without_blocking(tmp_path: Path) -> None:
    destination = tmp_path / "events.jsonl"
    os.mkfifo(tmp_path / ".events.jsonl.lock")

    with pytest.raises(ValueError, match="lock path must be a regular file"):
        append_text_durable(destination, "event\n")

    assert not destination.exists()
