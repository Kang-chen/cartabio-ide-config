#!/usr/bin/env python3
"""
validate_report.py — Stage 7 (verify-before-trust) of signature-response-enrichment.

Automated half of the mandatory validation gate:
  1. NUMBER RECONCILIATION - every value in `--expect` (JSON) must appear in the PDF text.
  2. TABLE-NUMBERING INTEGRITY - tables contiguous 1..N, no duplicates; every in-text
     "Table k" reference resolves to a table that exists.
  3. FIGURE PAGE RENDER - render each page that contains an embedded image to PNG, so the
     agent can run the visual media-output-check on them and regenerate on failure.

It does NOT itself judge figure quality - that is the agent's media-check step (the
`Read` tool in media_output_check mode) on the PNGs this script emits. Failing #1 or #2
exits non-zero.

Usage:
  python validate_report.py --pdf report.pdf \
      --expect expected_numbers.json \
      --render-dir /workspace/_qa --dpi 140

`expected_numbers.json` example (values re-read from the source CSVs, NOT memory):
  {"Fisher JAK1": "0.048", "refine endpoint dGSVA": "+0.312", "split": "16 R / 9 NR"}
"""
import argparse
import json
import re
import sys


def load_pdf_text(pdf_path):
    from pypdf import PdfReader
    r = PdfReader(pdf_path)
    pages = [p.extract_text() or "" for p in r.pages]
    return r, pages, "\n".join(pages)


def check_numbers(txt, expect):
    print("== (1) number reconciliation ==")
    ok = True
    for name, val in expect.items():
        present = str(val) in txt
        ok &= present
        print(f"  {'PASS' if present else 'FAIL'}: {name} -> '{val}'")
    return ok


def check_table_numbering(txt):
    print("== (2) table-numbering integrity ==")
    # Table *definitions*: 'Table N.' or 'Table N ' at caption start
    defs = sorted({int(n) for n in re.findall(r"Table (\d+)\s*[.\u2014:]", txt)})
    # All in-text references to a table number
    refs = sorted({int(n) for n in re.findall(r"Table (\d+)", txt)})
    print(f"  table definitions found: {defs}")
    print(f"  table numbers referenced: {refs}")
    ok = True
    if defs:
        expected = list(range(1, max(defs) + 1))
        if defs != expected:
            ok = False
            print(f"  FAIL: definitions not contiguous 1..{max(defs)} (got {defs})")
        else:
            print(f"  PASS: definitions contiguous 1..{max(defs)}")
    # every referenced number must have a definition
    missing = [r for r in refs if r not in defs]
    if missing:
        ok = False
        print(f"  FAIL: in-text refs with no matching table: {missing}")
    else:
        print("  PASS: all in-text Table refs resolve")
    return ok


def render_figure_pages(pdf_path, render_dir, dpi):
    print("== (3) figure-page render (for media-check) ==")
    import os
    import fitz  # pymupdf
    os.makedirs(render_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    out = []
    for i in range(doc.page_count):
        page = doc[i]
        xobjs = page.get_images(full=True)  # images embedded on this page
        if xobjs:
            fp = os.path.join(render_dir, f"page_{i+1}.png")
            page.get_pixmap(dpi=dpi).save(fp)
            out.append((i + 1, fp))
            print(f"  page {i+1}: {len(xobjs)} image(s) -> {fp}")
    if not out:
        print("  (no embedded-image pages detected)")
    print("  NEXT (agent): run the media-output-check on each PNG above; "
          "regenerate any figure that is blank/clipped/has glyph artifacts, "
          "rebuild the PDF, and re-run this validator.")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--expect", help="JSON of {name: expected_value_string}")
    ap.add_argument("--render-dir", default="/workspace/_qa")
    ap.add_argument("--dpi", type=int, default=140)
    args = ap.parse_args()

    reader, pages, txt = load_pdf_text(args.pdf)
    print(f"PDF: {args.pdf}  ({len(pages)} pages)\n")

    ok = True
    if args.expect:
        with open(args.expect) as fh:
            expect = json.load(fh)
        ok &= check_numbers(txt, expect)
        print()
    ok &= check_table_numbering(txt)
    print()
    render_figure_pages(args.pdf, args.render_dir, args.dpi)

    print(f"\nAUTOMATED CHECKS: {'ALL PASS' if ok else 'FAILURES PRESENT'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
