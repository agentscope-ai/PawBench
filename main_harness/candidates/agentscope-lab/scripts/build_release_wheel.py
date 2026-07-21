#!/usr/bin/env python3
"""Build the standalone wheel twice and publish it only if bytes match."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DATE_EPOCH = 1_784_160_000  # 2026-07-16T00:00:00Z
RECEIPT_SCHEMA = "harness-core-wheel-build/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
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
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _build_once(output: Path, *, source_date_epoch: int) -> Path:
    source = output.parent / f"{output.name}-source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".pytest_cache",
            "*.egg-info",
            "__pycache__",
            "build",
            "dist",
            "tmp",
        ),
    )
    env = dict(os.environ)
    env.update(
        {
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": str(source_date_epoch),
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--disable-pip-version-check",
            "--wheel-dir",
            str(output),
        ],
        cwd=source,
        env=env,
        check=True,
        # Keep stdout machine-readable: build progress remains visible on
        # stderr and the final receipt is the sole stdout document.
        stdout=sys.stderr,
    )
    wheels = sorted(output.glob("pawbench_agentscope_harness-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel, found {len(wheels)}")
    return wheels[0]


def _inspect_wheel(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise RuntimeError("wheel contains duplicate member names")
        unsafe = [
            name
            for name in names
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
        ]
        if unsafe:
            raise RuntimeError(f"wheel contains unsafe member paths: {unsafe}")
        forbidden = [
            name
            for name in names
            if name.endswith((".pyc", ".pyo"))
            or "/__pycache__/" in f"/{name}"
            or PurePosixPath(name).name.startswith(".env")
        ]
        if forbidden:
            raise RuntimeError(f"wheel contains forbidden generated/secret-like members: {forbidden}")
        required_suffixes = {
            "pawbench_agentscope/feature_manifest.json",
            "pawbench_agentscope/domain_profiles.json",
            "pawbench_agentscope/_portable_security.py",
            "pawbench_agentscope/_portable_taxonomy.py",
            "pawbench_agentscope/_portable_attribution_bridge.py",
        }
        missing = sorted(required_suffixes - set(names))
        if missing:
            raise RuntimeError(f"wheel is missing required portable contracts: {missing}")
        member_hashes = {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in sorted(names)
            if not name.endswith("/")
        }
        wheel_metadata_name = next(name for name in names if name.endswith(".dist-info/WHEEL"))
        wheel_metadata = archive.read(wheel_metadata_name).decode("utf-8", errors="replace")
        generator = next(
            (line.split(":", 1)[1].strip() for line in wheel_metadata.splitlines() if line.startswith("Generator:")),
            "unknown",
        )
    return {
        "member_count": len(names),
        "member_sha256": member_hashes,
        "wheel_generator": generator,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--source-date-epoch", type=int, default=DEFAULT_SOURCE_DATE_EPOCH)
    args = parser.parse_args()
    if args.source_date_epoch < 315_532_800:
        parser.error("--source-date-epoch must be at or after 1980-01-01")

    with tempfile.TemporaryDirectory(prefix="harness-core-wheel-build-") as temporary_root:
        temporary_root_path = Path(temporary_root)
        first = _build_once(temporary_root_path / "first", source_date_epoch=args.source_date_epoch)
        second = _build_once(temporary_root_path / "second", source_date_epoch=args.source_date_epoch)
        first_hash = _sha256(first)
        second_hash = _sha256(second)
        if first.name != second.name or first_hash != second_hash or first.read_bytes() != second.read_bytes():
            raise RuntimeError(
                "wheel build is not reproducible: "
                f"first={first.name}:{first_hash} second={second.name}:{second_hash}"
            )
        inspection = _inspect_wheel(first)

        args.output.mkdir(parents=True, exist_ok=True)
        destination = args.output / first.name
        staged: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=args.output,
                prefix=f".{first.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle, first.open("rb") as source:
                staged = Path(handle.name)
                shutil.copyfileobj(source, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staged, destination)
            staged = None
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    build_requirements = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "build-system"
    ]["requires"]
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "wheel": destination.name,
        "sha256": first_hash,
        "size_bytes": destination.stat().st_size,
        "source_date_epoch": args.source_date_epoch,
        "reproducible_double_build": True,
        "python": sys.version.split()[0],
        "build_toolchain": {
            "frontend": f"pip {importlib.metadata.version('pip')}",
            "backend": inspection["wheel_generator"],
            "locked_build_requirements": build_requirements,
        },
        **inspection,
    }
    _atomic_text(args.output / "SHA256SUMS", f"{first_hash}  {destination.name}\n")
    _atomic_text(
        args.output / "build-receipt.json",
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
