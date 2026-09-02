#!/usr/bin/env python3
"""Validate a generated PDF report for integrity and correctness.

Usage:
    python3 validate_pdf.py <report.pdf> [expected_figures_count]

Checks:
    1. PDF file exists and is non-empty
    2. PDF has more than one page
    3. Text is extractable (not image-only)
    4. Page size is US Letter (612 x 792 pt)
    5. All declared figures are embedded (by image count)
    6. No blank pages (each page has extractable text or images)

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def validate_pdf(pdf_path: str, expected_figures: int = 0) -> list[str]:
    """Run all validation checks on a PDF file.

    Returns a list of failure messages. Empty list means all checks passed.
    """
    failures: list[str] = []
    path = Path(pdf_path)

    # Check 1: file exists and is non-empty
    if not path.exists():
        failures.append(f"FAIL: PDF file does not exist: {pdf_path}")
        return failures
    if path.stat().st_size < 1000:
        failures.append(
            f"FAIL: PDF file is too small ({path.stat().st_size} bytes) — likely a rendering failure"
        )
        return failures

    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            failures.append(
                "WARN: pypdf not available — cannot run text extraction, page count, or page size checks"
            )
            return failures

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        failures.append(f"FAIL: cannot read PDF: {exc}")
        return failures

    # Check 2: more than one page
    n_pages = len(reader.pages)
    if n_pages < 2:
        failures.append(
            f"FAIL: PDF has only {n_pages} page(s) — a multi-step analysis report must be >1 page"
        )

    # Check 3: text is extractable
    total_text = ""
    for i, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        total_text += page_text

    if len(total_text.strip()) < 50:
        failures.append(
            "FAIL: PDF text is not extractable (too little text extracted) — "
            "text may be rendered as images"
        )

    # Check 4: page size is US Letter
    for i, page in enumerate(reader.pages):
        try:
            box = page.mediabox
            width = float(box.width)
            height = float(box.height)
            # US Letter is 612 x 792 pt; allow small tolerance
            if abs(width - 612) > 5 or abs(height - 792) > 5:
                failures.append(
                    f"FAIL: Page {i + 1} has size {width:.0f} x {height:.0f} pt — "
                    f"expected US Letter (612 x 792 pt)"
                )
                break
        except Exception:
            # Some PDFs may not expose mediabox cleanly; skip if unavailable
            pass

    # Check 5: figure embedding (count images across all pages)
    if expected_figures > 0:
        total_images = 0
        for page in reader.pages:
            try:
                resources = page.get("/Resources")
                if resources:
                    xobjects = resources.get("/XObject")
                    if xobjects:
                        xobj_dict = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
                        for key in xobj_dict:
                            obj = xobj_dict[key]
                            obj_resolved = obj.get_object() if hasattr(obj, "get_object") else obj
                            subtype = obj_resolved.get("/Subtype")
                            if subtype == "/Image":
                                total_images += 1
            except Exception:
                pass
        if total_images < expected_figures:
            failures.append(
                f"FAIL: PDF contains {total_images} embedded image(s) — "
                f"expected at least {expected_figures} figure(s)"
            )

    # Check 6: no blank pages (each page should have text or images)
    for i, page in enumerate(reader.pages):
        try:
            page_text = (page.extract_text() or "").strip()
        except Exception:
            page_text = ""
        has_image = False
        try:
            resources = page.get("/Resources")
            if resources:
                xobjects = resources.get("/XObject")
                if xobjects:
                    xobj_dict = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
                    for key in xobj_dict:
                        obj = xobj_dict[key]
                        obj_resolved = obj.get_object() if hasattr(obj, "get_object") else obj
                        if obj_resolved.get("/Subtype") == "/Image":
                            has_image = True
                            break
        except Exception:
            pass
        # Skip the cover page (page 1) from the blank-page check — it may have
        # only the title text which some extractors handle poorly
        if i > 0 and not page_text and not has_image:
            failures.append(
                f"FAIL: Page {i + 1} appears blank (no extractable text and no images)"
            )

    return failures


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate_pdf.py <report.pdf> [expected_figures_count]", file=sys.stderr)
        sys.exit(1)
    pdf_path = sys.argv[1]
    expected_figs = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    failures = validate_pdf(pdf_path, expected_figs)

    if not failures:
        print("PASS: All PDF validation checks passed", file=sys.stderr)
        sys.exit(0)
    else:
        for msg in failures:
            print(msg, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
