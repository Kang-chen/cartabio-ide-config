#!/usr/bin/env python3
"""PDF grounding gate: fail unless the rendered PDF actually contains the verbatim
anchor sentences for every grounded claim.

This is the enforcement teeth behind the skill's core promise. ``grounded_quotes.py``
produces the exact sentences that MUST appear; this script extracts text from the
finished PDF and checks that at least one verbatim anchor per grounded claim is
present in the rendered output. A report that paraphrased instead of quoting will
fail here.

Matching is whitespace-robust (PDF text extraction collapses/reflows whitespace and
may drop soft hyphens), and uses a longest-substring fallback so that a quote which
is split across a two-column line break still counts if a sufficiently long
contiguous run (default >= 40 collapsed chars, or the whole quote if shorter)
survives. This tolerates real PDF-extraction noise without letting paraphrases pass.

Usage:
    python verify_pdf_quotes.py --root <run-root> --pdf <path-to-report.pdf>
    # optional: --min-claim-coverage 1.0  (fraction of grounded claims that must
    #           have >=1 verbatim anchor present; default 1.0 = all of them)
    #           --min-run 40  (min contiguous collapsed chars for the fallback)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from support_policy import UNGROUNDED_STATES as ungrounded  # noqa: E402

_WS = re.compile(r"\s+")
_SOFT_HYPHEN = "\u00ad"


def _norm(text: str) -> str:
    """Collapse whitespace and strip soft hyphens for robust matching."""
    if not text:
        return ""
    text = text.replace(_SOFT_HYPHEN, "")
    # Join hyphenated line breaks: "clear-\nance" -> "clearance"
    text = re.sub(r"-\s+", "", text)
    return _WS.sub(" ", text).strip().lower()


def _longest_common_run(needle: str, haystack: str, min_run: int) -> bool:
    """True if a contiguous run of >= min_run chars of needle appears in haystack,
    or the whole (short) needle appears."""
    if not needle:
        return False
    if needle in haystack:
        return True
    if len(needle) <= min_run:
        return needle in haystack
    # Slide a window of size min_run; cheap and good enough for gate purposes.
    for i in range(0, len(needle) - min_run + 1):
        if needle[i : i + min_run] in haystack:
            return True
    return False


def extract_pdf_text(pdf_path: pathlib.Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"pypdf is required to verify the PDF: {exc}")
    reader = PdfReader(str(pdf_path), strict=True)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def verify(root: pathlib.Path, pdf_path: pathlib.Path,
           min_claim_coverage: float, min_run: int) -> tuple[list[str], list[str], dict]:
    failures: list[str] = []
    notes: list[str] = []

    gq_path = root / "deliverables" / "grounded_quotes.json"
    if not gq_path.exists():
        failures.append(
            f"missing {gq_path.name}; run grounded_quotes.py before the PDF gate"
        )
        return failures, notes, {}
    if not pdf_path.exists():
        failures.append(f"PDF not found: {pdf_path}")
        return failures, notes, {}

    data = json.loads(gq_path.read_text(encoding="utf-8"))
    pdf_norm = _norm(extract_pdf_text(pdf_path))
    if len(pdf_norm) < 200:
        failures.append("PDF text extraction yielded almost no text (image-only or corrupt)")

    # Only C_INSUFFICIENT is ungrounded. A C_REFUTED claim IS grounded — by its
    # contradicting quotes — and those quotes are the whole point of a
    # contradiction hunt, so they must appear verbatim in the report like any
    # other anchor. Exempting C_REFUTED here let a review paraphrase every
    # refuted finding and still pass the gate that exists to prevent exactly
    # that. Matches verify_review, verify_report_contract, and SKILL.md §7.
    from support_policy import UNGROUNDED_STATES as ungrounded
    grounded_claims = [e for e in data.values() if e.get("support_state") not in ungrounded]
    covered = 0
    per_claim: list[dict] = []
    for e in grounded_claims:
        cid = e["claim_id"]
        anchors = (e.get("supporting_anchors") or []) + (e.get("contradicting_anchors") or [])
        found_any = False
        found_quote = None
        for a in anchors:
            q = _norm(a.get("quote", ""))
            if q and _longest_common_run(q, pdf_norm, min_run):
                found_any = True
                found_quote = a.get("quote", "")[:80]
                break
        per_claim.append({"claim_id": cid, "grounded_anchor_in_pdf": found_any})
        if found_any:
            covered += 1
        else:
            failures.append(
                f"{cid}: no verbatim grounded anchor found in the PDF "
                "(claim appears paraphrased rather than quoted)"
            )

    n = len(grounded_claims)
    coverage = (covered / n) if n else 1.0
    if n and coverage < min_claim_coverage:
        failures.append(
            f"grounded-quote coverage {coverage:.2f} < required {min_claim_coverage:.2f} "
            f"({covered}/{n} grounded claims have a verbatim anchor in the PDF)"
        )

    stats = {
        "grounded_claims": n,
        "claims_with_verbatim_anchor_in_pdf": covered,
        "coverage": round(coverage, 4),
        "per_claim": per_claim,
    }
    return failures, notes, stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify verbatim grounded quotes are present in the PDF")
    parser.add_argument("--root", default=".")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--min-claim-coverage", type=float, default=1.0)
    parser.add_argument("--min-run", type=int, default=40)
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    pdf_path = pathlib.Path(args.pdf).resolve()

    failures, notes, stats = verify(root, pdf_path, args.min_claim_coverage, args.min_run)
    for note in notes:
        print(f"NOTE: {note}")
    if stats:
        print(
            f"PDF-QUOTES: grounded_claims={stats['grounded_claims']} "
            f"with_anchor={stats['claims_with_verbatim_anchor_in_pdf']} "
            f"coverage={stats['coverage']}"
        )
    for f in failures:
        print(f"FAIL: {f}")
    print(f"VERIFY-PDF-QUOTES: failures={len(failures)} result={'pass' if not failures else 'fail'}")
    return min(255, len(failures))


if __name__ == "__main__":
    sys.exit(main())
