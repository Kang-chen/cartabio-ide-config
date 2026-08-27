#!/usr/bin/env python3
"""Create a deterministic ZIP while excluding caches and excessive transient files."""
from __future__ import annotations

import argparse
import hashlib
import os
import zipfile
from pathlib import Path

EXCLUDE_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log"}


def should_include(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in EXCLUDE_PARTS for part in rel.parts):
        return False
    if path.suffix in EXCLUDE_SUFFIXES:
        return False
    return path.is_file()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--zip", required=True)
    p.add_argument("--prefix", default="")
    p.add_argument("--max-files", type=int, default=10000)
    args = p.parse_args()
    root = Path(args.root).resolve()
    files = [p for p in sorted(root.rglob("*")) if should_include(p, root)]
    if len(files) > args.max_files:
        raise SystemExit(f"Refusing to zip {len(files)} files; limit is {args.max_files}")
    out = Path(args.zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix.strip("/")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(root).as_posix()
            arc = f"{prefix}/{rel}" if prefix else rel
            info = zipfile.ZipInfo(arc)
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.external_attr = (0o755 if os.access(path, os.X_OK) else 0o644) << 16
            with path.open("rb") as f:
                zf.writestr(info, f.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    print(f"{out} {sha256(out)} {len(files)} files")


if __name__ == "__main__":
    main()
