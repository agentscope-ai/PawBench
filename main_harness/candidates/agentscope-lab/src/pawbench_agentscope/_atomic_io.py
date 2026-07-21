"""Small crash-safe output helpers for bridge and report artifacts."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:  # POSIX in Harbor/macOS; threads-only fallback keeps imports portable.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None  # type: ignore[assignment]


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def exclusive_path_lock(path: Path) -> Iterator[None]:
    """Serialize read-modify-write cycles across local threads and processes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    thread_lock = _path_lock(lock_path)
    with thread_lock:
        if lock_path.is_symlink():
            raise ValueError(f"lock path must not be a symlink: {lock_path}")
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | int(getattr(os, "O_NOFOLLOW", 0))
            | int(getattr(os, "O_NONBLOCK", 0))
        )
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            if lock_path.is_symlink():
                raise ValueError(f"lock path must not be a symlink: {lock_path}") from exc
            raise
        lock_acquired = False
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"lock path must be a regular file: {lock_path}")
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
                lock_acquired = True
            yield
        finally:
            if fcntl is not None and lock_acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def read_text_no_follow(
    path: Path,
    *,
    encoding: str = "utf-8",
    max_bytes: int | None = None,
) -> str:
    """Read one regular file without following its final symlink.

    ``max_bytes`` bounds both the pre-read file size and a file that grows while
    it is being read.  Opening first and checking that same descriptor avoids a
    path-stat/read race and prevents FIFOs or device files from blocking a
    batch run.
    """

    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")

    if path.is_symlink():
        raise ValueError(f"input path must not be a symlink: {path}")
    flags = (
        os.O_RDONLY
        | int(getattr(os, "O_NOFOLLOW", 0))
        | int(getattr(os, "O_NONBLOCK", 0))
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if path.is_symlink():
            raise ValueError(f"input path must not be a symlink: {path}") from exc
        raise
    with os.fdopen(fd, "r", encoding=encoding) as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"input path must be a regular file: {path}")
        if max_bytes is not None and metadata.st_size > max_bytes:
            raise ValueError(f"input exceeds {max_bytes} bytes: {path}")
        if max_bytes is None:
            return handle.read()
        text = handle.read(max_bytes + 1)
        if len(text.encode(encoding)) > max_bytes:
            raise ValueError(f"input exceeds {max_bytes} bytes: {path}")
        return text


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace *path* atomically without following a predictable temp symlink."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        # Persist the directory entry as well as the file contents. Some
        # filesystems can otherwise lose the rename after a sudden crash.
        directory_flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
        try:
            directory_fd = os.open(path.parent, directory_flags)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                # Directory fsync is not supported on every platform.
                pass
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def append_text_durable(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Append one payload under a cross-process lock and durably flush it.

    The destination must not be a symlink.  ``os.write`` is deliberately
    retried because a successful write is allowed to consume fewer bytes than
    requested, including on regular files under fault injection.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode(encoding)
    with exclusive_path_lock(path):
        if path.is_symlink():
            raise ValueError(f"append path must not be a symlink: {path}")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | int(getattr(os, "O_NOFOLLOW", 0))
            | int(getattr(os, "O_NONBLOCK", 0))
        )
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            if path.is_symlink():
                raise ValueError(f"append path must not be a symlink: {path}") from exc
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError:
                metadata = None
            if metadata is not None and not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"append path must be a regular file: {path}") from exc
            raise
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"append path must be a regular file: {path}")
            view = memoryview(data)
            offset = 0
            while offset < len(view):
                written = os.write(fd, view[offset:])
                if written <= 0:
                    raise OSError("durable append made no forward progress")
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)


def prepare_marked_output(
    root: Path,
    *,
    marker_name: str,
    marker_text: str,
    replace: bool,
) -> Path:
    """Create or replace only a directory carrying the exact owned marker."""

    if not marker_name or Path(marker_name).name != marker_name:
        raise ValueError("marker_name must be one file name")
    if root.is_symlink():
        raise ValueError(f"output directory must not be a symlink: {root}")
    marker = root / marker_name
    if root.exists():
        if not root.is_dir():
            raise ValueError(f"output path must be a directory: {root}")
        if (
            marker.is_symlink()
            or not marker.is_file()
            or read_text_no_follow(
                marker,
                max_bytes=max(4_096, len(marker_text.encode("utf-8")) + 1),
            )
            != marker_text
        ):
            action = "replace" if replace else "write into"
            raise ValueError(f"refusing to {action} unmarked output: {root}")
        if replace:
            shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(root / marker_name, marker_text)
    return root
