#!/usr/bin/env python3
"""Create or verify SHA-256 hashes for protected files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(paths: list[str]) -> dict[str, str]:
    out = {}
    for s in paths:
        p = Path(s)
        if p.is_dir():
            for child in sorted(x for x in p.rglob("*") if x.is_file()):
                out[str(child)] = sha256(child)
        elif p.is_file():
            out[str(p)] = sha256(p)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(required=True)
    c = sub.add_parser("create")
    c.add_argument("--out", required=True)
    c.add_argument("paths", nargs="+")
    v = sub.add_parser("verify")
    v.add_argument("--manifest", required=True)
    args = p.parse_args()
    if hasattr(args, "out"):
        manifest = collect(args.paths)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"written": args.out, "files": len(manifest)}))
    else:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        mismatches = []
        missing = []
        for s, expected in manifest.items():
            pth = Path(s)
            if not pth.exists():
                missing.append(s)
            else:
                got = sha256(pth)
                if got != expected:
                    mismatches.append({"path": s, "expected": expected, "got": got})
        ok = not missing and not mismatches
        print(json.dumps({"ok": ok, "missing": missing, "mismatches": mismatches}, indent=2))
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
