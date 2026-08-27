#!/usr/bin/env python3
"""Validate final method delivery artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED = ["README.md", "reproduce.sh", "run_manifest.json", "benchmark_card.md", "method_card.md", "final_report.md"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--final-method", required=True)
    args = p.parse_args()
    root = Path(args.final_method)
    errors = []
    for rel in REQUIRED:
        path = root / rel
        if not path.exists():
            errors.append(f"missing {rel}")
        elif path.stat().st_size == 0:
            errors.append(f"empty {rel}")
    manifest = root / "run_manifest.json"
    if manifest.exists():
        try:
            json.loads(manifest.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"invalid run_manifest.json: {e}")
    if len([p for p in root.rglob("*") if p.is_file()]) > 10000:
        errors.append("final method exceeds 10000 files")
    if errors:
        print("delivery validation failed:")
        for e in errors:
            print(f"- {e}")
        raise SystemExit(1)
    print(f"delivery validation passed: {root}")


if __name__ == "__main__":
    main()
