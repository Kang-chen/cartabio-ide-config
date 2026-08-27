#!/usr/bin/env python3
"""
report_qc.py — Formatting helpers and LOUD pre-export gates for the liability report.

Everything here exists to make a defect *impossible or loudly detected* rather than fixed
once. The report step and figure generator use these; the pipeline fails before it
ever ships a PDF that a human would otherwise have to inspect and repair.

Contents
  english_ordinal(n)           correct English ordinals (1st, 2nd, 3rd, 11th-13th, 21st, 72nd, ...)
  fmt_prob(x, nd)              format a probability/float to nd decimals (never raw float precision)
  fmt_float(x, nd)             alias with a different default for general numbers
  agreement_state(sim, dp)     4-state engine-agreement label (Both / Similarity only / DTI only / Neither)
  normalize_prefname(s)        normalization key for ChEMBL pref_name (ortholog / primary matching)
  collapse_orthologs(df, ...)  collapse ortholog rows (shared pref_name) to one representative
  validate_report_data(rd)     GATE: refuse to build unless adaptive-split, agreement and primary
                               fields are present (item 1 & item 3 circularity guards)
  validate_pdf(path, ...)      GATE: refuse to ship a PDF with raw floats, near-blank pages, or too
                               few pages (item 4)
  assert_figure_ok(png, ...)   GATE: refuse a blank / degenerate figure (item 4)

No network, no heavy deps at import time (pdfplumber / PIL imported lazily).
"""
from __future__ import annotations


class ReportQCError(Exception):
    """Raised by any gate that must stop export."""


# --------------------------------------------------------------------------- formatting
def english_ordinal(n) -> str:
    """Correct English ordinal for an integer (rounds floats first).

    11th/12th/13th are the classic exceptions; everything else follows the last digit.
    """
    n = int(round(float(n)))
    if 11 <= (abs(n) % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(abs(n) % 10, "th")
    return f"{n}{suffix}"


def fmt_prob(x, nd: int = 3) -> str:
    """Format a probability / model score to `nd` decimals. Never emits raw float precision.

    Returns 'n/a' for anything non-numeric (None, 'n/a', NaN)."""
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return "n/a"
    if xf != xf:  # NaN
        return "n/a"
    return f"{xf:.{nd}f}"


def fmt_float(x, nd: int = 2) -> str:
    """General numeric formatter (default 2 dp)."""
    return fmt_prob(x, nd)


# --------------------------------------------------------------------------- agreement
def agreement_state(sim_hit, dp_hit) -> str:
    """Explicit 2x2 engine-agreement label.

    'Similarity only' and 'DTI only' are the *disagreement* states; 'Both' is dual-engine
    support; 'Neither' means neither engine calls a hit. The two engines are NOT on a
    comparable scale (logistic-similarity probability vs min-max-normalized DeepPurpose),
    which is exactly why the blended consensus must never be shown without this label.
    """
    s, d = bool(sim_hit), bool(dp_hit)
    if s and d:
        return "Both"
    if s and not d:
        return "Similarity only"
    if d and not s:
        return "DTI only"
    return "Neither"


AGREEMENT_ORDER = {"Both": 0, "Similarity only": 1, "DTI only": 2, "Neither": 3}


# --------------------------------------------------------------------------- ortholog / primary keys
def normalize_prefname(name) -> str:
    """Normalization key used to match orthologs and the primary target across species.

    ChEMBL assigns the SAME pref_name to orthologous single-protein targets (e.g. human and
    rat 'HMG-CoA reductase'), so a normalized pref_name is a reliable cross-species key."""
    if name is None:
        return ""
    return " ".join(str(name).strip().lower().split())


def collapse_orthologs(df, prefname_col: str, by: str | None = None):
    """Collapse ortholog rows (rows sharing a normalized pref_name) to ONE representative.

    Keeps the representative with the largest `by` value (if given), adds an integer
    `n_orthologs` column, and preserves the original columns. Used to stop the two HMGCR
    orthologs from plotting as two colliding points on the benchmark scatter.
    """
    import pandas as pd  # local import keeps module import cheap

    if df is None or len(df) == 0:
        return df
    d = df.copy()
    d["_ortho_key"] = d[prefname_col].map(normalize_prefname)
    if by is not None and by in d.columns:
        d = d.sort_values(by, ascending=False, na_position="last")
    counts = d.groupby("_ortho_key", sort=False).size().rename("n_orthologs").reset_index()
    rep = d.drop_duplicates("_ortho_key", keep="first").merge(counts, on="_ortho_key")
    rep = rep.drop(columns=["_ortho_key"])
    return rep.reset_index(drop=True)


# --------------------------------------------------------------------------- report_data gate
def validate_report_data(rd: dict):
    """GATE: refuse to build the report unless the circularity guards are wired through.

    Enforces that (item 1) any similarity-hit count carries its core/adaptive split, that
    (item 3) engine-agreement is present, and that (item 2) primary-target handling reached
    the report layer. Raises ReportQCError listing every problem it found.
    """
    problems = []
    b = rd.get("benchmark", {}) or {}
    panel = rd.get("panel", {}) or {}

    if "n_sim_hits_core" not in b or "n_sim_hits_adaptive" not in b:
        problems.append(
            "similarity-hit count is not split into core/adaptive "
            "(benchmark.n_sim_hits_core / n_sim_hits_adaptive missing) — an independent-"
            "evidence count must be core-only")
    if "n_primary" not in panel:
        problems.append("panel.n_primary missing (primary-target handling not wired)")
    if "primary_resolved" not in b:
        problems.append("benchmark.primary_resolved flag missing")
    if "agreement_counts" not in b:
        problems.append("benchmark.agreement_counts missing (item 3 agreement not carried)")

    tops = rd.get("top_predictions", []) or []
    for i, r in enumerate(tops):
        missing = [k for k in ("agreement", "source", "P_sim") if k not in r]
        if missing:
            problems.append(f"top_predictions[{i}] missing {missing}")
            break

    if problems:
        raise ReportQCError("report_data failed QC: " + "; ".join(problems))
    return True


# --------------------------------------------------------------------------- pdf gate
import re as _re

# Standalone number with >= N decimals, but NOT part of a DOI / version / identifier token
# (negative look-around on word chars, dot and slash). Catches leaked raw floats like
# "0.9953125" while ignoring "10.1093/nar..." DOIs and "3.10.5" versions.
_RAW_FLOAT_RE = _re.compile(r"(?<![\w./])\d+\.\d{4,}(?![\w./])")


def validate_pdf(path, min_pages: int = 2, min_bytes: int = 5000,
                 min_page_chars: int = 150):
    """GATE: refuse to ship a PDF that a human would have to repair.

    Fails on: too few pages, too small, first page without extractable text, any numeric
    field printed at raw float precision (>= 4 decimals, DOI-safe), or any near-blank page
    (< min_page_chars of text AND no embedded image). Raises ReportQCError.
    """
    import os
    import pdfplumber

    problems = []
    size = os.path.getsize(path)
    if size <= min_bytes:
        problems.append(f"file too small ({size} bytes)")

    page_texts = []
    with pdfplumber.open(path) as pdf:
        npages = len(pdf.pages)
        if npages < min_pages:
            problems.append(f"only {npages} page(s)")
        for i, page in enumerate(pdf.pages, 1):
            txt = page.extract_text() or ""
            page_texts.append(txt)
            n_img = len(page.images or [])
            if len(txt.strip()) < min_page_chars and n_img == 0:
                problems.append(
                    f"page {i} near-blank ({len(txt.strip())} chars, no image)")

    if not (page_texts and page_texts[0].strip()):
        problems.append("first page has no extractable text")

    alltext = "\n".join(page_texts)
    raw = sorted({m.group(0) for m in _RAW_FLOAT_RE.finditer(alltext)})
    if raw:
        problems.append(
            "raw float(s) printed at >= 4 decimals: " + ", ".join(raw[:8]))

    if problems:
        raise ReportQCError("PDF failed QC: " + "; ".join(problems))
    return {"status": "ok", "pages": npages, "bytes": size}


# --------------------------------------------------------------------------- figure gate
def assert_figure_ok(png_path, min_w: int = 200, min_h: int = 150, min_std: float = 2.0):
    """GATE: refuse a blank / degenerate figure.

    A failed matplotlib render is typically a near-uniform white PNG. We check dimensions
    and pixel variance. (True label *truncation* cannot occur here: figures are saved with
    bbox_inches='tight', which grows the canvas to include every text artist — so the fix
    for 'truncated labels' is to avoid hard string slicing in tables, not to police the
    figure canvas.)
    """
    from PIL import Image
    import numpy as np

    im = Image.open(png_path).convert("L")
    w, h = im.size
    if w < min_w or h < min_h:
        raise ReportQCError(f"figure {png_path} too small ({w}x{h})")
    std = float(np.asarray(im, dtype="float32").std())
    if std < min_std:
        raise ReportQCError(f"figure {png_path} appears blank (pixel std {std:.2f})")
    return {"status": "ok", "w": w, "h": h, "std": round(std, 2)}


if __name__ == "__main__":
    # tiny self-demo (also exercised by assets/eval/test_report_qc.py)
    for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 72, 101, 111):
        print(n, "->", english_ordinal(n))
