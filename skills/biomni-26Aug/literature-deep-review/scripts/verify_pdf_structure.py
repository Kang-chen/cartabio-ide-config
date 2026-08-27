#!/usr/bin/env python3
"""Fail when a PDF is only readable through tolerant repair heuristics."""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys


QPDF_TIMEOUT_SECONDS = 60


def verify(path: pathlib.Path) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    notes: list[str] = []
    if not path.exists() or path.stat().st_size == 0:
        return [f"PDF is missing or empty: {path}"], notes
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path), strict=True)
        for page in reader.pages:
            page.get("/Resources")
        notes.append(f"strict pypdf parse passed ({len(reader.pages)} pages)")
    except Exception as exc:  # noqa: BLE001 - parser errors are the gate result
        failures.append(f"strict PDF parse failed: {type(exc).__name__}: {exc}")

    qpdf = shutil.which("qpdf")
    if qpdf:
        try:
            result = subprocess.run(
                [qpdf, "--check", str(path)],
                capture_output=True,
                text=True,
                timeout=QPDF_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "qpdf check failed").strip()
                failures.append(f"qpdf --check failed: {detail[:1000]}")
            else:
                notes.append("qpdf --check passed")
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append(f"qpdf check could not run: {type(exc).__name__}: {exc}")
    else:
        notes.append("qpdf unavailable; strict pypdf validation was used")
    return failures, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True)
    args = parser.parse_args(argv)
    failures, notes = verify(pathlib.Path(args.pdf).resolve())
    for note in notes:
        print(f"NOTE: {note}")
    for failure in failures:
        print(f"FAIL: {failure}")
    print(
        f"VERIFY-PDF-STRUCTURE: failures={len(failures)} "
        f"result={'pass' if not failures else 'fail'}"
    )
    return min(255, len(failures))


if __name__ == "__main__":
    sys.exit(main())
