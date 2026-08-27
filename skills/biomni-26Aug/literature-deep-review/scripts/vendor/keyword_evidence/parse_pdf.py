"""PDF parsing for literature keyword evidence.

Primary path: pypdfium2 (page rendering) + pdfplumber (text + bbox + font sizes)
              + pypdf (metadata; available, unused in default path).

Why this stack (not PyMuPDF)
----------------------------
PyMuPDF is dual-licensed AGPL-3.0 / commercial. We deliberately avoid it so this
skill stays Apache/MIT/BSD-clean and matches Biomni's distribution license.
The replacement stack is:

  - pdfplumber (MIT, built on pdfminer.six) → text with bbox + font sizes per word
  - pypdfium2 (Apache-2.0 / BSD-3, wraps Google PDFium) → page rasterization for
    figure crops (and as a fallback for OCR'd pages)
  - pypdf (BSD-3) → already in Biomni; kept available for metadata / passthrough

This loses two PyMuPDF conveniences and we restore them:
  (a) PyMuPDF returns text "blocks" with bbox; pdfplumber returns "words".
      We cluster words → lines → blocks in `_words_to_blocks()`.
  (b) PyMuPDF crops a page region directly to PNG via `get_pixmap(clip=...)`.
      We render the full page with pypdfium2 at the requested DPI, then crop
      the PIL image to the same region in pixel space.

Optional high-quality path: Marker (`--quality high`) — unchanged from prior version.
~30-60s/page CPU; opt-in only.

Output payload (cached as JSON), identical schema to the prior implementation
so downstream `match.py` / `report.py` / `ocr_figures.py` don't change:
{
  "paper_id": str,
  "parser": "pypdfium2" | "marker",
  "n_pages": int,
  "sections": [{"title": str, "page_start": int, "page_end": int}],
  "sentences": [{"sentence_id": int, "text": str, "section": str, "page": int,
                 "bbox": [x0,y0,x1,y1]}],
  "figures": [{"figure_id": str, "page": int, "caption": str,
               "caption_bbox": [...], "figure_bbox": [...], "image_path": str}],
  "parser_version": str,
  "n_captions_detected": int,
  "n_crop_attempted": int,
  "n_crop_ok": int,
  "crop_failure_reasons": {str: int}
}

Coordinate convention (same as PyMuPDF, same as pdfplumber):
  - Origin top-left, in PDF points (1 pt = 1/72 in).
  - bbox = [x0, y0, x1, y1] with y0 < y1 (y0 = top, y1 = bottom).
"""
from __future__ import annotations
import json
import hashlib
import pathlib
import re
import sys
import time
from typing import Any

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pysbd
from PIL import Image

# Block-level repair for extraction damage. It lives in the skill's own scripts/
# directory, one level above vendor/, and is shared with the acceptance and
# display layers so all three agree on what counts as damaged.
_SKILL_SCRIPTS = str(pathlib.Path(__file__).resolve().parents[2])
if _SKILL_SCRIPTS not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS)
from quote_integrity import repair as repair_extraction_damage  # noqa: E402
from section_labels import is_prose as _is_prose  # noqa: E402
from section_labels import section_for_page  # noqa: E402
from section_labels import split_run_in_headings  # noqa: E402


# Parser code version. This is part of the parse cache key (see `parse_pdf`) so
# that a behavioral change to this module invalidates warm caches. Without it
# the only staleness guard is the `parser` tag ("pypdfium2" vs "marker"), which
# distinguishes the two code paths but not two versions of the same path — every
# fix below would be invisible on a re-run against an existing cache.
# BUMP THIS whenever parsing output changes.
# 3: _clean_text rejoins split fi/fl ligatures; abstract-section detection no
#    longer leaves abstract sentences labelled "Front matter".
# 4: a short section's label expires N pages after its heading instead of owning
#    every block to the end of the document (section_labels.section_for_page).
# 5: figure crops stop widening into the neighbouring text column. Cached parses
#    from 4 hold crop paths whose images contain the article's running prose.
# 6: the same clip on the text-walk fallback (5 fixed only the image-union path),
#    and no crop for Table captions, whose content sits BELOW the caption so
#    every above-the-caption rule here captures the wrong region.
# 7: line-numbered preprint captions are recognized instead of falling through
#    to dozens of unlabeled embedded raster fragments.
# 8: adjacent raster panels in a captionless PDF are reassembled into one
#    page-level figure candidate before OCR and selection.
# 9: image-union crops keep a real edge margin and may reach the physical page
#    top; the prior hard 30-point top clamp clipped structural figures.
_PARSER_VERSION = "9"

# Caption regex: matches "Figure 1", "Fig. 2", "Fig 3a", "Table 1" at the start of
# a block, optionally behind a "Supplementary" / "Extended Data" qualifier.
# Tolerates the weird intra-word whitespace some PMC PDFs introduce.
_CAPTION_RE = re.compile(
    r"^(?:\d{1,4}\s+)?"
    r"(?P<label>(?:(?:Supplementary|Supplemental|Suppl\.?|Extended\s+Data)\s+)?"
    r"(?:Figure|Fig\.?|Table)\s*\d+[a-zA-Z]?)\s*[.:\-]?",
    re.IGNORECASE,
)

# A "Figure 10 continued" block is the tail of a caption that spilled onto the
# next page/column, not a new figure. Matching it emits a duplicate figure record
# (and a bogus crop) for a figure already captured.
_CAPTION_CONTINUED_RE = re.compile(
    r"^\s*[(\[]?\s*(?:continued|cont(?:inued)?\.?|cont'?d\.?)\b",
    re.IGNORECASE,
)

# Minimum figure region size (in PDF points) for crop emission.
# Lowered from 40 -> 20 pt per PLAN.md to recover small panels and inline
# figures that were silently dropped. Single-line captions are 10-15 pt high,
# so 20 pt still excludes pure caption-only crops while catching small panels.
_MIN_FIGURE_DIM_PT = 20

# How much of a text block's height must fall inside the figure's vertical band
# before it counts as sitting beside the figure. A caption line clipped by a
# point or two is not a neighbouring column.
_SIDE_TEXT_OVERLAP = 0.35

# Breathing room left between the crop edge and neighbouring body text.
_SIDE_TEXT_MARGIN_PT = 6

# Edge labels, panel letters and protein names can sit just outside a raster
# tile. Two points was too small at ordinary journal resolution and the 30-point
# page-top clamp then removed the top of figures near the page boundary.
_FIGURE_EDGE_PADDING_PT = 6.0
_FIGURE_CAPTION_GAP_PT = 2.0

# Maximum blank gap between embedded raster tiles that still belong to one
# multi-panel figure. Forty-eight PDF points is two-thirds of an inch: wide
# enough for normal panel gutters and too small to bridge separate figures on a
# journal page.
_EMBEDDED_PANEL_GAP_PT = 48.0


def _group_embedded_images(
    boxes: list[tuple[float, float, float, float]],
) -> list[list[tuple[float, float, float, float]]]:
    """Connected components of adjacent raster tiles, in page order."""
    boxes = sorted(boxes, key=lambda box: (box[1], box[0], box[3], box[2]))
    if not boxes:
        return []

    def adjacent(a, b) -> bool:
        x_gap = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
        y_gap = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
        return x_gap <= _EMBEDDED_PANEL_GAP_PT and y_gap <= _EMBEDDED_PANEL_GAP_PT

    pending = set(range(len(boxes)))
    groups: list[list[tuple[float, float, float, float]]] = []
    while pending:
        seed = min(pending)
        pending.remove(seed)
        component = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            neighbours = [
                index for index in sorted(pending)
                if adjacent(boxes[current], boxes[index])
            ]
            for index in neighbours:
                pending.remove(index)
                component.add(index)
                frontier.append(index)
        groups.append([boxes[index] for index in sorted(component)])
    return groups


def _text_free_span(blocks, caption_block, fig_bbox, page_w):
    """(left, right) x-bounds the crop may widen to without hitting body text.

    A figure that occupies one column of a two-column page has the article's
    running prose immediately beside it. Widening to the page margins for
    "visual context" then bakes that prose into the reproduced figure — which is
    how a delivered report shipped a crop containing two columns of unrelated
    text, annotated with provenance boxes as though it were part of the figure.

    Only blocks that genuinely straddle the figure's vertical band and sit
    entirely to one side of it constrain the widening; anything overlapping the
    figure horizontally is in-figure text and must not clip it.
    """
    ix0, iy0, ix1, iy1 = fig_bbox
    height = max(1.0, iy1 - iy0)
    left, right = 0.0, float(page_w)
    for bb in blocks or []:
        if bb is caption_block:
            continue
        bx0, by0, bx1, by1 = bb["bbox"]
        overlap = min(by1, iy1) - max(by0, iy0)
        if overlap <= 0 or (overlap / height) < _SIDE_TEXT_OVERLAP:
            continue
        if bx1 <= ix0:                      # wholly left of the figure
            left = max(left, bx1 + _SIDE_TEXT_MARGIN_PT)
        elif bx0 >= ix1:                    # wholly right of the figure
            right = min(right, bx0 - _SIDE_TEXT_MARGIN_PT)
    return left, right


def _padded_image_bbox(image_bbox, caption_top: float):
    """Add safe vertical context without clipping at an arbitrary page inset."""
    x0, y0, x1, y1 = image_bbox
    return (
        x0,
        max(0.0, y0 - _FIGURE_EDGE_PADDING_PT),
        x1,
        min(y1 + _FIGURE_EDGE_PADDING_PT,
            caption_top - _FIGURE_CAPTION_GAP_PT),
    )

# Reject "blank" crops (Strategy B fallback over a pure-whitespace region produces
# uniform-color PNGs with std=0). Real figures observed in the validation corpus
# all have std > 20; this threshold sits inside an empty band so it's safe.
_MIN_CROP_STD = 5.0

# Section heading detector: short, mostly-uppercase or title-case at the start of a block.
_SECTION_NAMES = {
    "abstract", "introduction", "background", "methods", "materials and methods",
    "methods and materials", "materials & methods", "results", "discussion",
    "conclusion", "conclusions", "references", "acknowledgments",
    "acknowledgements", "supplementary", "supplementary materials",
    "data availability", "author contributions", "funding",
    "competing interests", "ethics", "limitations",
}
# Sorted longest-first so multi-word names are matched before their single-word
# prefixes. Ties are broken alphabetically (`sorted` is stable over a sorted
# input), so iteration order is identical across processes — iterating the raw
# `set` instead makes section detection depend on PYTHONHASHSEED.
_SECTION_NAMES_SORTED = sorted(sorted(_SECTION_NAMES), key=len, reverse=True)

# How much longer than the canonical name a heading may run and still collapse
# onto it ("Methods and materials section" -> "Methods And Materials"). Without
# a cap, any block merely STARTING with a section word ("Results in the AAV-Grn
# cohort were consistent with prior work") is read as a heading and relabels the
# rest of the document.
_SECTION_PREFIX_SLACK = 12

# Placeholder section for blocks that precede the first detected heading (title,
# authors, affiliations, journal furniture). Never emit the literal "Unknown":
# templates/report_contract.json forbids it as a locator value.
_FRONT_MATTER = "Front matter"

# Minimum length of the first body-size run on page 1 for it to be called the
# abstract. Titles/authors/affiliations are far shorter; real abstracts run
# 150-300 words.
_ABSTRACT_MIN_CHARS = 200

# Evidence locators carry the section label and the report contract rejects one
# longer than this (templates/report_contract.json: max_section_chars).
_MAX_SECTION_CHARS = 60
_TRUNC_MARK = "..."


def _n_sentences(segmenter, text: str) -> int:
    """How many sentences a block will emit, so ids can be reserved for it."""
    try:
        return max(1, len([s for s in segmenter.segment(text) if str(s).strip()]))
    except Exception:  # noqa: BLE001 - segmentation is best-effort here
        return 1


def _canon_section_title(raw: str) -> str:
    """A section label fit for an evidence locator.

    Collapses onto a canonical `_SECTION_NAMES` entry when the heading opens
    with one; otherwise strips any numeric prefix and caps the result at
    `_MAX_SECTION_CHARS` on a word boundary. Free-text headings are otherwise
    unbounded (Marker hands back whole markdown headings verbatim), and a
    30-word heading in a locator is useless and fails the contract.
    """
    t = re.sub(r"\s+", " ", str(raw or "")).strip()
    t = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", t).strip()
    norm = t.lower().rstrip(":.").strip()
    if norm in _SECTION_NAMES:
        return norm.title()
    for canon in _SECTION_NAMES_SORTED:
        if norm.startswith(canon) and len(norm) <= len(canon) + _SECTION_PREFIX_SLACK:
            return canon.title()
    t = t.rstrip(":").strip()
    if not t:
        return _FRONT_MATTER
    if len(t) <= _MAX_SECTION_CHARS:
        return t
    cut = t[:_MAX_SECTION_CHARS - len(_TRUNC_MARK)].rstrip()
    if " " in cut:
        cut = cut[:cut.rindex(" ")].rstrip()
    return cut.rstrip(",;:-") + _TRUNC_MARK


def _push_section(sections: list[dict], title: str, pno: int) -> None:
    """Append `title` as a new section starting on page `pno+1`, closing out the
    previous one. No-op when it merely repeats the current section."""
    if sections and sections[-1]["title"] == title:
        return
    if sections:
        sections[-1]["page_end"] = pno + 1
    sections.append({"title": title, "page_start": pno + 1, "page_end": pno + 1})


def _find_heading_prefix(text: str) -> tuple[str | None, str | None]:
    """If `text` begins with a known section heading (optionally preceded by a
    numeric prefix like "1." or "2 "), peel it off and return
    `(heading, remainder)`. Otherwise return `(None, None)`.

    This exists because pdfplumber's word stream concatenates a heading and the
    following body paragraph into the same block (no line-break preservation
    for adjacent same-size lines). PyMuPDF kept them as separate blocks; we
    restore the same behavior here so section detection works downstream.
    """
    if not text:
        return None, None
    s = text.strip()
    # Try multi-word names FIRST (longest first); a single-word match would
    # otherwise steal a multi-word heading's prefix.
    for name in _SECTION_NAMES_SORTED:
        if " " not in name:
            continue
        pat = re.match(r"^(\d+\.?\s+)?" + re.escape(name) + r"(?=[\s:.\-]|$)",
                       s, re.IGNORECASE)
        if pat:
            end = pat.end()
            while end < len(s) and s[end] in ".:- \t":
                end += 1
            return s[:end].strip(), s[end:].strip()
    # Then single-word names.
    m = re.match(r"^(\d+\.?\s+)?([A-Za-z]+)(?=[\s:.\-]|$)", s)
    if not m:
        return None, None
    candidate = m.group(2).strip().lower()
    if candidate in _SECTION_NAMES:
        end = m.end()
        while end < len(s) and s[end] in ".:- \t":
            end += 1
        return s[:end].strip(), s[end:].strip()
    return None, None


# Structured-abstract handling and the abstract-prose test live in
# scripts/section_labels.py, next to the rest of the skill's own logic and
# testable without the PDF stack. See that module for the two shipped locator
# defects they fix.
def _split_blocks_on_run_in_headings(blocks: list[dict]) -> list[dict]:
    """Expand any structured-abstract block into one block per section.

    The bbox and font size of the parent are carried onto every segment. That is
    approximate — a segment's true bbox is a sub-region — but a locator's
    granularity is page plus section, and a correct section with an approximate
    bbox is strictly better than the reverse.
    """
    out: list[dict] = []
    for block in blocks:
        segments = split_run_in_headings(block.get("text", ""))
        if len(segments) <= 1:
            out.append(block)
            continue
        for segment in segments:
            out.append({**block, "text": segment})
    return out


# End-of-line hyphenation: a hyphen (ASCII, soft U+00AD, or the Unicode hyphens
# U+2010/U+2011) followed by whitespace, between two alphanumerics.
_LINEBREAK_HYPHEN_RE = re.compile(
    r"([A-Za-z0-9])[-\u00ad\u2010\u2011]\s+([A-Za-z0-9])"
)


def _rejoin_hyphen(m: "re.Match[str]") -> str:
    """Close up an end-of-line hyphen split, keeping the hyphen unless both sides
    are lowercase.

    ``dis- ease`` is a typesetter's hyphenation of one word and rejoins to
    ``disease``. But ``PGRN- deficient`` / ``TDP- 43`` / ``AAV- Grn`` are real
    hyphenated compounds broken at their own hyphen: dropping it invents a token
    (``PGRNdeficient``) that appears nowhere in the source PDF, which silently
    defeats verbatim quote verification downstream. An uppercase letter or a
    digit on either side is the reliable signal for a genuine compound.
    """
    a, b = m.group(1), m.group(2)
    if a.islower() and b.islower():
        return a + b
    return f"{a}-{b}"


def _clean_text(s: str) -> str:
    """Collapse PMC's weird multi-space layout artifacts and repair end-of-line
    hyphenation. A word split across a line break renders as ``dis- ease`` (or,
    across a column gutter, ``progra- nulin``); rejoin ``<lower>- <lower>`` into a
    single token so grounded quotes are real words. Where either side carries an
    uppercase letter or a digit (``PGRN- deficient``, ``TDP- 43``, ``AAV- Grn``)
    the hyphen is part of the compound and is preserved, only the stray space is
    removed. Hyphenated compounds such as ``early-onset`` / ``loss-of-function``
    have no space after the hyphen and are therefore left intact.

    Split presentation ligatures are rejoined here too. pdfminer emits U+FB01 as
    " fi " when the font's ToUnicode map decomposes it, so "significantly" arrives
    as "signi fi cantly" — which shipped, inside quotation marks, in a report
    whose whole promise is that a quote is the sentence the paper contains.
    Repairing at the BLOCK is what keeps that promise checkable: the quote stays
    an exact substring of the block it came from, so every downstream locator and
    quote gate still resolves. Repairing at display time would have left the
    stored evidence damaged and the rendered text different from it.
    """
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = _LINEBREAK_HYPHEN_RE.sub(_rejoin_hyphen, s)
    s = repair_extraction_damage(s)[0]
    return s


def detect_column_boundaries(words: list[dict], min_col_frac: float = 0.12,
                             min_gutter_pt: float = 12.0) -> list[float]:
    """Detect vertical column gutters from the word horizontal-coverage profile.

    Returns sorted x positions that separate columns (empty => single column).
    A gutter is an interior vertical band that no word's x-range covers, at least
    ``min_gutter_pt`` wide, and not within ``min_col_frac`` of the text edges (so
    margins are not mistaken for gutters). This is what makes two-column reading
    order correct: without it, lines from the left and right columns share a
    ``top`` band and get interleaved row-by-row (producing garbled sentences such
    as ``GRN mutations cause dis- demonstrated target engagement ... ease with``).
    """
    if not words:
        return []
    x_min = min(w["x0"] for w in words)
    x_max = max(w["x1"] for w in words)
    span = x_max - x_min
    if span <= 0:
        return []
    bin_w = 2.0
    nbins = int(span / bin_w) + 1
    cover = [0] * nbins
    for w in words:
        a = int((w["x0"] - x_min) / bin_w)
        b = int((w["x1"] - x_min) / bin_w)
        for i in range(max(0, a), min(nbins, b + 1)):
            cover[i] += 1
    min_gutter_bins = max(1, int(min_gutter_pt / bin_w))
    edge_guard = int(span * min_col_frac / bin_w)
    cuts: list[float] = []
    i = 0
    while i < nbins:
        if cover[i] == 0:
            j = i
            while j < nbins and cover[j] == 0:
                j += 1
            run = j - i
            center_bin = (i + j) // 2
            if run >= min_gutter_bins and edge_guard < center_bin < (nbins - edge_guard):
                cuts.append(x_min + center_bin * bin_w)
            i = j
        else:
            i += 1
    return cuts


def _split_line_by_columns(line_words: list[dict], gap_threshold: float = 30.0,
                           cuts: list[float] | None = None) -> list[list[dict]]:
    """Split top-aligned words into sub-lines at column boundaries.

    Splits wherever (a) a detected page gutter in ``cuts`` lies between two
    consecutive words, OR (b) the horizontal gap exceeds ``gap_threshold``.
    The gutter split is essential for journals with a *tight* gutter (~12-15 pt,
    smaller than a safe generic ``gap_threshold`` of 30 pt): pdfplumber glues the
    left- and right-column words of one visual row into a single line, and only a
    cut at the known gutter x separates them. Single-column papers have no
    detected gutter and fall back to the generic gap rule, so they are untouched.
    """
    if not line_words:
        return []
    cuts = cuts or []
    ws = sorted(line_words, key=lambda w: w["x0"])
    sub_lines = [[ws[0]]]
    for prev, w in zip(ws, ws[1:]):
        gap = w["x0"] - prev["x1"]
        crosses_gutter = any(prev["x1"] <= c <= w["x0"] for c in cuts)
        if crosses_gutter or gap > gap_threshold:
            sub_lines.append([w])
        else:
            sub_lines[-1].append(w)
    return sub_lines


def _words_to_blocks(words: list[dict],
                     line_tol: float = 2.0,
                     block_gap_factor: float = 1.6,
                     size_change_threshold: float = 0.15,
                     column_gap: float = 30.0) -> list[dict]:
    """Cluster pdfplumber words into reading-order blocks.

    Replaces PyMuPDF's `page.get_text("dict")["blocks"]`. Returns a list of
    dicts shaped like:
        {"bbox": [x0, y0, x1, y1], "text": "...", "font_size": <float>}

    Algorithm:
      1. Cluster words by `top` coordinate (within `line_tol` points) into row
         bands. Each row band may span multiple columns.
      2. Split each row band into column-aware sub-lines wherever a horizontal
         gap > `column_gap` appears (typical column gutter ~30-60 pt).
      3. Re-sort sub-lines into reading order (top, then x).
      4. Walk sub-lines top to bottom; start a new BLOCK when:
           - vertical gap to the previous sub-line > median_line_height * block_gap_factor,
           - mean font size changes by more than size_change_threshold, OR
           - the sub-line's left edge jumps by > 50 pt (column transition).
    """
    if not words:
        return []
    # Step 0: detect column gutters for the whole page (empty => single column).
    cuts = detect_column_boundaries(words)
    # Step 1: row bands by `top`.
    ws = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    bands: list[list[dict]] = []
    cur = [ws[0]]
    for w in ws[1:]:
        if abs(w["top"] - cur[0]["top"]) <= line_tol:
            cur.append(w)
        else:
            bands.append(cur)
            cur = [w]
    if cur:
        bands.append(cur)
    # Step 2: split each band into column-aware sub-lines (split at detected
    # gutters as well as large generic gaps).
    lines: list[list[dict]] = []
    for band in bands:
        for sub in _split_line_by_columns(band, gap_threshold=column_gap, cuts=cuts):
            lines.append(sorted(sub, key=lambda w: w["x0"]))
    if not lines:
        return []
    # Step 3: sort sub-lines into TRUE reading order. For a multi-column page,
    # read each column fully top-to-bottom before the next: sort by
    # (column_index, top). Assign a line's column from its x-center relative to
    # the detected gutters. Single-column pages have no cuts => column 0 for all,
    # which reduces to the original (top, x) ordering.
    def _col_of(ln: list[dict]) -> int:
        xc = (min(w["x0"] for w in ln) + max(w["x1"] for w in ln)) / 2.0
        idx = 0
        for c in cuts:
            if xc > c:
                idx += 1
            else:
                break
        return idx
    lines.sort(key=lambda ln: (_col_of(ln),
                               round(min(w["top"] for w in ln), 1),
                               min(w["x0"] for w in ln)))
    # Step 4: block clustering.
    heights = [max(w["bottom"] - w["top"] for w in ln) for ln in lines]
    heights_sorted = sorted(heights)
    med_h = heights_sorted[len(heights_sorted) // 2] if heights_sorted else 12.0
    if med_h <= 0:
        med_h = 12.0
    blocks_of_lines: list[list[list[dict]]] = []
    cur_lines: list[list[dict]] = [lines[0]]
    for prev, ln in zip(lines, lines[1:]):
        prev_bottom = max(w["bottom"] for w in prev)
        cur_top = min(w["top"] for w in ln)
        gap = cur_top - prev_bottom
        prev_size = sum(w.get("size", 10.0) for w in prev) / len(prev)
        cur_size = sum(w.get("size", 10.0) for w in ln) / len(ln)
        size_change = abs(cur_size - prev_size) / max(prev_size, 1e-6)
        prev_x0 = min(w["x0"] for w in prev)
        cur_x0 = min(w["x0"] for w in ln)
        col_change = abs(cur_x0 - prev_x0) > 50.0
        if (gap > med_h * block_gap_factor
                or size_change > size_change_threshold
                or col_change):
            blocks_of_lines.append(cur_lines)
            cur_lines = [ln]
        else:
            cur_lines.append(ln)
    if cur_lines:
        blocks_of_lines.append(cur_lines)
    # Build block dicts.
    out: list[dict] = []
    for blk in blocks_of_lines:
        all_words = [w for ln in blk for w in ln]
        text = _clean_text(" ".join(w["text"] for ln in blk for w in ln))
        if not text:
            continue
        x0 = min(w["x0"] for w in all_words)
        x1 = max(w["x1"] for w in all_words)
        y0 = min(w["top"] for w in all_words)
        y1 = max(w["bottom"] for w in all_words)
        sizes = [w.get("size", 10.0) for w in all_words if w.get("size", 0) > 0]
        fs = sum(sizes) / len(sizes) if sizes else 0.0
        out.append({"bbox": [x0, y0, x1, y1], "text": text, "font_size": fs})
    return out


def _detect_section(text: str, font_size: float, body_font_size: float,
                    x0: float | None = None) -> str | None:
    """Return a canonical section name if this block looks like a section heading.

    `x0` (when provided) is the block's left edge in PDF points. A real section
    heading starts a new column flow, so it lives at a column-start x position
    (left margin or right-column start) — not at the far right of a multi-column
    layout. Table column headers like "References" appearing in the rightmost
    column would otherwise be mistaken for a Section heading; we reject any
    candidate that sits beyond a typical right-column-start (~350 pt for A4).
    """
    if not text or len(text) > 100:
        return None
    norm = text.lower().rstrip(":.").strip()
    # A short block whose text is EXACTLY a section name is a heading regardless
    # of font size: many journals set headings bold at body size, so the 1.05x
    # gate below rejected the single commonest case ("Abstract", "Introduction",
    # "Results") and left the whole document labelled with the placeholder.
    is_exact_name = norm in _SECTION_NAMES and len(text) <= 30
    if not is_exact_name and font_size < body_font_size * 1.05:
        return None
    if x0 is not None and x0 > 350:
        # Right-edge text (typical table column header position) is not a
        # section heading in normal scientific layouts.
        return None
    if norm in _SECTION_NAMES:
        return norm.title()
    # Iterate the SORTED names: the raw set's iteration order varies with
    # PYTHONHASHSEED, which made "conclusions and future directions" resolve to
    # Conclusion or Conclusions depending on the process. The length cap keeps
    # this branch to genuine headings — without it a body sentence that merely
    # opens with a section word relabels everything that follows it.
    for canon in _SECTION_NAMES_SORTED:
        if norm.startswith(canon) and len(norm) <= len(canon) + _SECTION_PREFIX_SLACK:
            return canon.title()
    m = re.match(r"^\d+\.?\s+([A-Za-z &]+)$", text)
    if m and m.group(1).strip().lower() in _SECTION_NAMES:
        return m.group(1).strip().title()
    return None


def _match_caption(text: str, font_size: float, body_font_size: float):
    """Return the `_CAPTION_RE` match if `text` opens a genuine figure/table
    caption, else None.

    Two guards beyond the bare regex:
      - Captions are set at or below body size (typically smaller). A block in a
        larger face that happens to start "Figure 3 ..." is a heading or a
        display line, not a caption.
      - "Figure 10 continued" is a caption spill, not a new figure.
    """
    if not text:
        return None
    if body_font_size > 0 and font_size > body_font_size * 1.02:
        return None
    m = _CAPTION_RE.match(text)
    if not m:
        return None
    if _CAPTION_CONTINUED_RE.match(text[m.end():]):
        return None
    return m


def _safe_pid(paper_id: str) -> str:
    """Filename-safe paper id. Critically, paper_id is often a DOI containing
    '/' (e.g. '10.1523/jneurosci.3081-17.2018'); using it raw in a crop filename
    creates a non-existent subdirectory and the PNG save fails (silently, since
    the crop wraps errors). Replace path separators and other unsafe chars."""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(paper_id))


def _crop_pypdfium2(pdf, pno: int, bbox: tuple[float, float, float, float],
                   dpi: int, out_path: pathlib.Path,
                   pdf_path: "str | pathlib.Path | None" = None,
                   reasons: dict[str, int] | None = None) -> bool:
    """Render a page region to PNG using pypdfium2 + PIL crop.

    bbox is [x0, y0, x1, y1] in PDF points with origin top-left (same convention
    as pdfplumber and PyMuPDF). pypdfium2 renders the whole page; we crop in
    pixel space.

    Robustness note: rendering is done from a FRESH ``PdfDocument`` opened from
    ``pdf_path`` for the duration of the crop, not from the long-lived ``pdf``
    object shared with text extraction. The shared object can enter a state
    (observed when pdfplumber holds the same file open concurrently) where
    ``page.render()`` fails silently, which previously caused *all* figure crops
    to be dropped even though figures were detected. A per-crop fresh document
    renders deterministically. ``pdf_path`` is preferred; if it is not provided
    we fall back to the passed ``pdf`` object (legacy behavior).

    Returns True on success. ``reasons`` (when supplied) is a histogram that
    every failure path increments with a specific cause. Without it, all five
    ways a crop can fail collapse to the same bare False, and a run where every
    crop failed looks identical to a paper with only vector figures.
    """
    own_doc = None

    def _fail(reason: str) -> bool:
        if reasons is not None:
            reasons[reason] = reasons.get(reason, 0) + 1
        return False

    try:
        if pdf_path is not None:
            own_doc = pdfium.PdfDocument(str(pdf_path))
            src = own_doc
        else:
            src = pdf
        if src is None:
            return _fail("no_document")
        page = src[pno]
        scale = dpi / 72.0
        try:
            pil_full = page.render(scale=scale).to_pil()
        except Exception:
            return _fail("render_error")
        x0, y0, x1, y1 = bbox
        px = (int(round(x0 * scale)),
              int(round(y0 * scale)),
              int(round(x1 * scale)),
              int(round(y1 * scale)))
        # Clamp to image bounds and require non-zero area.
        w, h = pil_full.size
        px = (max(0, px[0]), max(0, px[1]), min(w, px[2]), min(h, px[3]))
        if px[2] - px[0] < 10 or px[3] - px[1] < 10:
            return _fail("region_too_small")
        crop = pil_full.crop(px)
        # Reject crops with no content (Strategy B can build bboxes over pure
        # whitespace bands when no embedded image lies above the caption).
        arr = np.array(crop)
        if arr.std() <= _MIN_CROP_STD:
            return _fail("blank_crop")
        try:
            # Ensure the destination directory exists (defensive: some callers
            # build out_path from a paper_id that may still contain a separator).
            pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            crop.save(str(out_path))
        except Exception:
            return _fail("save_failed")
        return True
    except Exception:
        return _fail("open_error")
    finally:
        if own_doc is not None:
            try:
                own_doc.close()
            except Exception:
                pass


def parse_pdf_pypdfium2(
    pdf_path: str | pathlib.Path,
    paper_id: str,
    figures_dir: str | pathlib.Path,
    figure_dpi: int = 200,
) -> dict[str, Any]:
    """Parse a PDF with pdfplumber (text) + pypdfium2 (rendering) and return
    the structured payload. Drop-in replacement for the prior PyMuPDF parser.
    """
    pdf_path = pathlib.Path(pdf_path)
    figures_dir = pathlib.Path(figures_dir)
    figures_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # Open with both libraries: pdfplumber for text/layout, pypdfium2 for rendering.
    plumber = pdfplumber.open(str(pdf_path))
    pdf = pdfium.PdfDocument(str(pdf_path))

    try:
        n_pages = len(plumber.pages)

        # Pass 1: collect text blocks across all pages with font sizes.
        page_blocks: list[list[dict]] = []
        page_sizes: list[tuple[float, float]] = []  # (width, height) in points
        page_images: list[list[tuple[float, float, float, float]]] = []
        all_sizes: list[float] = []
        for pno in range(n_pages):
            page = plumber.pages[pno]
            page_sizes.append((page.width, page.height))
            # pdfplumber's default x_tolerance (3.0) collapses tight-kerned
            # word boundaries in PMC-style captions ("Figure1:" instead of
            # "Figure 1:"). Lowering to 1.5 restores the correct boundaries
            # without over-segmenting paragraphs.
            words = page.extract_words(extra_attrs=["size", "fontname"],
                                       x_tolerance=1.5)
            tblocks = _words_to_blocks(words)
            for b in tblocks:
                if b["font_size"] > 0:
                    all_sizes.append(b["font_size"])
            # Already reading-order from _words_to_blocks (sorted top->bottom, left->right).
            page_blocks.append(tblocks)
            # Embedded image bboxes (used as a stronger anchor than nearest-text
            # for figure cropping when caption-relative text-walk fails).
            imgs_bb = []
            for img in page.images:
                try:
                    bb = (float(img.get("x0", 0)), float(img.get("top", 0)),
                          float(img.get("x1", 0)), float(img.get("bottom", 0)))
                    if bb[2] - bb[0] > 5 and bb[3] - bb[1] > 5:
                        imgs_bb.append(bb)
                except Exception:
                    pass
            page_images.append(imgs_bb)

        # Estimate body-text font size as the central tendency of block sizes.
        body_font_size = 10.0
        if all_sizes:
            all_sizes_sorted = sorted(all_sizes)
            lo = int(len(all_sizes_sorted) * 0.2)
            hi = int(len(all_sizes_sorted) * 0.8)
            sample = all_sizes_sorted[lo:hi] or all_sizes_sorted
            body_font_size = sum(sample) / len(sample)

        # Pass 2: walk blocks, assign sections, split sentences, find captions.
        segmenter = pysbd.Segmenter(language="en", clean=False)
        sentences: list[dict] = []
        sections: list[dict] = []
        figures: list[dict] = []
        current_section = _FRONT_MATTER
        # The page the current label was set on. A short section's label
        # expires a bounded number of pages later (see
        # section_labels.section_for_page); without this a heading found in
        # page-1 furniture owned every block to the end of the document.
        section_page = 0
        seen_heading = False
        # The consecutive body-size prose run on page 1 that is a candidate
        # abstract, and the sentence ids emitted from it, so the label can be
        # applied once the run is long enough to be one.
        abstract_run: list[str] = []
        abstract_sentence_ids: list[int] = []
        sentence_id = 0
        figure_counter = 0
        n_captions_detected = 0
        n_crop_attempted = 0
        n_crop_ok = 0
        crop_failure_reasons: dict[str, int] = {}

        for pno, blocks in enumerate(page_blocks):
            page_w, page_h = page_sizes[pno]
            for b in _split_blocks_on_run_in_headings(blocks):
                text = b["text"]
                font_size = b["font_size"]
                # Caption first so "Figure 1." is never confused with a section
                # heading, even when pdfplumber emits captions in body-sized font.
                # Matched ONCE, against the block's original text: re-matching
                # after the heading peel below turns "Results Figure 3 shows..."
                # into a phantom figure and drops the real sentence.
                cm = _match_caption(text, font_size, body_font_size)
                if cm is None:
                    # Section heading? Two cases:
                    #   (a) the whole block is a heading (PyMuPDF behavior).
                    #   (b) the block STARTS with a heading then runs into the
                    #       body paragraph (pdfplumber merges these together).
                    # For (b) we peel off the heading and treat the remainder as
                    # a body block under the new section.
                    sec_whole = _detect_section(text, font_size,
                                                body_font_size, x0=b["bbox"][0])
                    if sec_whole:
                        _push_section(sections, sec_whole, pno)
                        current_section = sec_whole
                        section_page = pno
                        seen_heading = True
                        continue
                    heading, remainder = _find_heading_prefix(text)
                    # `if heading` (not `heading and remainder`): a block whose
                    # text is EXACTLY a heading has an empty remainder, and that
                    # is the common case this branch used to skip entirely.
                    if heading:
                        h_norm = _canon_section_title(heading)
                        _push_section(sections, h_norm, pno)
                        current_section = h_norm
                        section_page = pno
                        seen_heading = True
                        if not remainder:
                            continue
                        # Replace `text` with the remainder so it flows through
                        # the body-sentence path below.
                        text = remainder
                    elif (not seen_heading and pno == 0
                            and current_section == _FRONT_MATTER
                            and font_size <= body_font_size * 1.05
                            and _is_prose(text)):
                        # No heading yet, and this is body-size prose on page 1:
                        # the abstract, whether or not the journal printed the
                        # word "Abstract".
                        #
                        # The length test used to be per BLOCK (>= 200 chars),
                        # which is why ten abstract sentences shipped with the
                        # locator "page 1 · Front matter": Nature-family PDFs
                        # break the abstract into several short blocks and none
                        # of them reaches 200 characters on its own. Length is
                        # now accumulated across the consecutive prose run, and
                        # the sentences already emitted from it are relabelled
                        # once the run is long enough to be an abstract rather
                        # than a title or an author list.
                        abstract_run.append(text)
                        abstract_sentence_ids.extend(
                            range(sentence_id, sentence_id + _n_sentences(
                                segmenter, text)))
                        if sum(len(t) for t in abstract_run) >= _ABSTRACT_MIN_CHARS:
                            current_section = "Abstract"
                            section_page = pno
                            _push_section(sections, "Abstract", pno)
                            for s in sentences:
                                if s["sentence_id"] in set(abstract_sentence_ids):
                                    s["section"] = "Abstract"
                if cm:
                    n_captions_detected += 1
                    figure_counter += 1
                    label = _clean_text(cm.group("label"))
                    figure_id = f"fig{figure_counter}_p{pno+1:02d}"
                    caption_top = b["bbox"][1]
                    # A TABLE caption sits ABOVE its table; a figure caption
                    # sits BELOW its figure. Every crop rule here reads the
                    # region above the caption, so applying it to a table
                    # captures whatever preceded it — a delivered report showed
                    # "Report Figure 7" as a bare title line reading "Table 2."
                    # with no table under it. The caption stays as text evidence,
                    # which is what it was useful for; only the crop is skipped.
                    is_table = str(label or "").strip().lower().startswith("table")
                    # Strategy A: union of embedded images on this page that
                    # lie strictly above the caption. This is the most robust
                    # anchor in journal layouts where the figure region
                    # contains in-figure rasterized text (axis labels, panel
                    # letters, gel-image labels) that would otherwise be picked
                    # up as text blocks and collapse the text-walk region.
                    imgs_above = [im for im in page_images[pno]
                                  if im[3] < caption_top]
                    fig_bbox_img = None
                    if imgs_above:
                        ix0 = min(im[0] for im in imgs_above)
                        iy0 = min(im[1] for im in imgs_above)
                        ix1 = max(im[2] for im in imgs_above)
                        iy1 = max(im[3] for im in imgs_above)
                        if (ix1 - ix0) >= _MIN_FIGURE_DIM_PT and (iy1 - iy0) >= _MIN_FIGURE_DIM_PT:
                            fig_bbox_img = (ix0, iy0, ix1, iy1)
                    # Strategy B: closest text block above (legacy heuristic).
                    # GATE: only attempt the text-walk fallback if at least one
                    # embedded image actually exists on this page. Without this
                    # gate, "captions" that are really running headers, page
                    # numbers, or running figure references on text-only pages
                    # produce crops over pure-whitespace regions (blank PNGs).
                    fig_bbox_txt = None
                    if page_images[pno]:
                        prev_bottom = 0.0
                        for bb in blocks:
                            if bb is b:
                                continue
                            if bb["bbox"][3] < caption_top and bb["bbox"][3] > prev_bottom:
                                prev_bottom = bb["bbox"][3]
                        x0_txt = max(20, b["bbox"][0] - 10)
                        x1_txt = min(page_w - 20, b["bbox"][2] + 10)
                        y0_txt = max(prev_bottom + 4, 30)
                        y1_txt = caption_top - 2
                        # Same rule as Strategy A: widen to the margins only
                        # through space that holds no body text. Fixing this in
                        # Strategy A alone left the fallback path shipping crops
                        # with the article's other column in them.
                        left_limit, right_limit = _text_free_span(
                            blocks, b, (x0_txt, y0_txt, x1_txt, y1_txt), page_w)
                        x0_txt = max(min(x0_txt, 30), left_limit)
                        x1_txt = min(max(x1_txt, page_w - 30), right_limit)
                        fig_bbox_txt = (x0_txt, y0_txt, x1_txt, y1_txt)
                    # Pick whichever has a usable height (>= _MIN_FIGURE_DIM_PT).
                    # Prefer image-union; fall back to text-walk only if it was
                    # built (i.e. the page actually has embedded images).
                    if fig_bbox_img is not None:
                        ix0, iy0, ix1, iy1 = fig_bbox_img
                        _px0, y0, _px1, y1 = _padded_image_bbox(
                            fig_bbox_img, caption_top
                        )
                        # Widen horizontally for visual context (axis labels and
                        # panel letters that sat outside the raw image bbox) —
                        # but only into space that holds no body text. Widening
                        # unconditionally to the page margins is correct on a
                        # single-column page and wrong on a two-column one,
                        # where it drags the neighbouring column into the crop:
                        # a delivered report reproduced a figure with two
                        # columns of the article's running prose baked into it.
                        left_limit, right_limit = _text_free_span(
                            blocks, b, (ix0, y0, ix1, y1), page_w)
                        x0 = max(min(ix0, 30), left_limit)
                        x1 = min(max(ix1, page_w - 30), right_limit)
                        figure_bbox = [x0, y0, x1, y1]
                    elif fig_bbox_txt is not None:
                        figure_bbox = list(fig_bbox_txt)
                    else:
                        # No image evidence on the page; this "caption" is
                        # almost certainly a running header / page artifact.
                        # Record the figure metadata for traceability but skip
                        # the crop.
                        figure_bbox = list(b["bbox"])
                    image_path = None
                    if (not is_table) and (figure_bbox[2] - figure_bbox[0]) >= _MIN_FIGURE_DIM_PT and (figure_bbox[3] - figure_bbox[1]) >= _MIN_FIGURE_DIM_PT and (fig_bbox_img is not None or fig_bbox_txt is not None):
                        crop_name = f"{_safe_pid(paper_id)}__{figure_id}.png"
                        crop_path = figures_dir / crop_name
                        n_crop_attempted += 1
                        ok = _crop_pypdfium2(pdf, pno, tuple(figure_bbox),
                                             dpi=figure_dpi, out_path=crop_path,
                                             pdf_path=pdf_path,
                                             reasons=crop_failure_reasons)
                        if ok:
                            n_crop_ok += 1
                            image_path = str(crop_path)
                    elif figure_bbox is not None:
                        crop_failure_reasons["no_figure_region"] = (
                            crop_failure_reasons.get("no_figure_region", 0) + 1)
                    figures.append({
                        "figure_id": figure_id,
                        "label": label,
                        "page": pno + 1,
                        "caption": text,
                        "caption_bbox": b["bbox"],
                        "figure_bbox": figure_bbox,
                        "image_path": image_path,
                    })
                    # Caption text is itself searchable: do NOT also feed it into
                    # the sentence stream (avoid double-matching).
                    continue
                # Body sentence: split with pysbd.
                try:
                    sents = segmenter.segment(text)
                except Exception:
                    sents = [text]
                for s in sents:
                    s_clean = _clean_text(s)
                    if len(s_clean) < 5:
                        continue
                    sentences.append({
                        "sentence_id": sentence_id,
                        "text": s_clean,
                        "section": section_for_page(
                            current_section, section_page, pno),
                        "page": pno + 1,
                        "bbox": b["bbox"],
                    })
                    sentence_id += 1

        # Close out the last section to span to the final page.
        if sections:
            sections[-1]["page_end"] = n_pages

        # Fallback: if no figure was found via captions, reassemble adjacent
        # embedded raster panels before rendering. Preprints often store one
        # multi-panel figure as several tiles; emitting each tile independently
        # produced 46 captionless candidates in one SLC33A1 paper and eventually
        # enlarged a clipped 617x338 fragment into a page-width report figure.
        if not figures:
            for pno in range(n_pages):
                eligible = [
                    box for box in page_images[pno]
                    if (box[2] - box[0]) >= 60 and (box[3] - box[1]) >= 60
                ]
                for group_index, group in enumerate(
                    _group_embedded_images(eligible)
                ):
                    bbox = [
                        min(box[0] for box in group),
                        min(box[1] for box in group),
                        max(box[2] for box in group),
                        max(box[3] for box in group),
                    ]
                    figure_counter += 1
                    composite = len(group) > 1
                    suffix = (
                        f"composite_p{pno+1:02d}_g{group_index}"
                        if composite else f"p{pno+1:02d}_i{group_index}"
                    )
                    figure_id = f"fig{figure_counter}_embedded_{suffix}"
                    crop_path = figures_dir / f"{_safe_pid(paper_id)}__{figure_id}.png"
                    n_crop_attempted += 1
                    ok = _crop_pypdfium2(pdf, pno, tuple(bbox),
                                         dpi=figure_dpi, out_path=crop_path,
                                         pdf_path=pdf_path,
                                         reasons=crop_failure_reasons)
                    if not ok:
                        continue
                    n_crop_ok += 1
                    figures.append({
                        "figure_id": figure_id,
                        "label": (
                            f"Embedded panel group p{pno+1} g{group_index}"
                            if composite
                            else f"Embedded image p{pno+1} i{group_index}"
                        ),
                        "page": pno + 1,
                        "caption": "",
                        "caption_bbox": None,
                        "figure_bbox": bbox,
                        "image_path": str(crop_path),
                        "extraction_kind": (
                            "embedded_page_composite" if composite
                            else "embedded_image"
                        ),
                    })
    finally:
        try:
            plumber.close()
        except Exception:
            pass
        try:
            pdf.close()
        except Exception:
            pass

    elapsed = time.time() - t0

    return {
        "paper_id": paper_id,
        "parser": "pypdfium2",
        "parser_version": _PARSER_VERSION,
        "n_pages": n_pages,
        "parse_seconds": round(elapsed, 2),
        "body_font_size": round(body_font_size, 2),
        "sections": sections,
        "sentences": sentences,
        "figures": figures,
        # Crop instrumentation: without these, a run in which every crop failed
        # is indistinguishable from a paper whose figures are genuinely
        # vector-only, because each failure path returns a bare False.
        "n_captions_detected": n_captions_detected,
        "n_crop_attempted": n_crop_attempted,
        "n_crop_ok": n_crop_ok,
        "crop_failure_reasons": crop_failure_reasons,
    }


def parse_pdf_marker(
    pdf_path: str | pathlib.Path,
    paper_id: str,
    figures_dir: str | pathlib.Path,
) -> dict[str, Any]:
    """Parse a PDF with Marker (high-quality opt-in path).

    WARNING: ~30-60s per page on CPU plus a one-time ~3.3GB model download.
    Only call this when the caller passes --quality high.
    """
    pdf_path = pathlib.Path(pdf_path)
    figures_dir = pathlib.Path(figures_dir)
    figures_dir.mkdir(parents=True, exist_ok=True)

    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    t0 = time.time()
    artifact_dict = create_model_dict()
    converter = PdfConverter(artifact_dict=artifact_dict)
    rendered = converter(str(pdf_path))
    text, _meta, images = text_from_rendered(rendered)
    elapsed = time.time() - t0

    # Save figures
    figures = []
    figure_counter = 0
    n_crop_attempted = 0
    n_crop_ok = 0
    crop_failure_reasons: dict[str, int] = {}
    for fname, img in (images or {}).items():
        figure_counter += 1
        figure_id = f"fig{figure_counter}_marker_{pathlib.Path(fname).stem}"
        crop_path = figures_dir / f"{_safe_pid(paper_id)}__{figure_id}.png"
        n_crop_attempted += 1
        try:
            img.save(str(crop_path))
            n_crop_ok += 1
            figures.append({
                "figure_id": figure_id,
                "label": fname,
                "page": None,  # Marker doesn't always preserve page
                "caption": "",
                "caption_bbox": None,
                "figure_bbox": None,
                "image_path": str(crop_path),
            })
        except Exception:
            crop_failure_reasons["save_failed"] = (
                crop_failure_reasons.get("save_failed", 0) + 1)
            continue

    # Sentence splitting on the full markdown
    segmenter = pysbd.Segmenter(language="en", clean=False)
    sentences = []
    current_section = _FRONT_MATTER
    sentence_id = 0
    sections = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            # Canonicalize and cap: a markdown heading is free text, and taken
            # verbatim it puts a whole 30-word heading into every downstream
            # evidence locator (over the contract's max_section_chars).
            current_section = _canon_section_title(line.lstrip("#").strip())
            if not sections or sections[-1]["title"] != current_section:
                sections.append({"title": current_section,
                                 "page_start": None, "page_end": None})
            continue
        try:
            sents = segmenter.segment(line)
        except Exception:
            sents = [line]
        for s in sents:
            s_clean = _clean_text(s)
            if len(s_clean) < 5:
                continue
            sentences.append({
                "sentence_id": sentence_id,
                "text": s_clean,
                "section": current_section,
                "page": None,
                "bbox": None,
            })
            sentence_id += 1

    return {
        "paper_id": paper_id,
        "parser": "marker",
        "parser_version": _PARSER_VERSION,
        "n_pages": None,
        "parse_seconds": round(elapsed, 2),
        "sections": sections,
        "sentences": sentences,
        "figures": figures,
        # Marker hands back already-extracted images rather than page crops, so
        # "attempted" counts saves. Same keys as the pypdfium2 path so callers
        # can read the instrumentation without branching on parser.
        "n_captions_detected": 0,
        "n_crop_attempted": n_crop_attempted,
        "n_crop_ok": n_crop_ok,
        "crop_failure_reasons": crop_failure_reasons,
    }


def file_sha1(path: str | pathlib.Path) -> str:
    """SHA1 of a file's bytes (used in cache key)."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _rematerialize_figure_crops(
    pdf_path: pathlib.Path,
    figures: list[dict],
    figures_dir: pathlib.Path,
    paper_id: str,
    figure_dpi: int = 200,
) -> None:
    """When the parse is cache-reused, ensure each figure's PNG exists in the
    current `figures_dir` by re-rendering from the original PDF using the cached
    `figure_bbox`. The cache stores parse-time results, so image paths point to
    whichever run first parsed the PDF; subsequent runs in different output
    directories need their own copies.

    Mutates `figures` in place: rewrites `image_path` to the new location when
    re-render succeeds; leaves entries with no usable bbox unchanged.
    """
    pdf = None
    try:
        for fig in figures:
            bbox = fig.get("figure_bbox")
            if not bbox:
                continue
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            if w < _MIN_FIGURE_DIM_PT or h < _MIN_FIGURE_DIM_PT:
                continue
            new_path = figures_dir / f"{_safe_pid(paper_id)}__{fig['figure_id']}.png"
            old_path = fig.get("image_path")
            # Skip if the cached path already matches the destination and exists.
            if old_path and pathlib.Path(old_path).resolve() == new_path.resolve() \
                    and new_path.exists():
                continue
            # Skip if a file is already present at the new path (idempotent reruns).
            if new_path.exists():
                fig["image_path"] = str(new_path)
                continue
            # Otherwise re-render from the PDF (fresh document per crop for
            # deterministic rendering; see _crop_pypdfium2 robustness note).
            pno = fig.get("page", 1) - 1
            ok = _crop_pypdfium2(pdf, pno, tuple(bbox), dpi=figure_dpi,
                                 out_path=new_path, pdf_path=pdf_path)
            if ok:
                fig["image_path"] = str(new_path)
            else:
                fig["image_path"] = None
    finally:
        if pdf is not None:
            try:
                pdf.close()
            except Exception:
                pass


def parse_pdf(
    pdf_path: str | pathlib.Path,
    paper_id: str,
    figures_dir: str | pathlib.Path,
    quality: str = "default",
    cache_dir: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    """High-level parse entry point with optional caching.

    cache_dir: if provided, parses are cached under
        cache_dir/<paper_id>__<sha1>__<quality>__v<parser_version>.parsed.json

    Cache key includes the quality setting so default-quality (pypdfium2) and
    high-quality (Marker) results never collide, AND `_PARSER_VERSION` so a
    behavioral change to this module invalidates warm caches. The `parser` tag
    check below only distinguishes pypdfium2 from marker, not v1 from v2 of the
    same path: without the version in the key, every parser fix stayed invisible
    on any re-run that hit an existing cache file.
    """
    pdf_path = pathlib.Path(pdf_path)
    figures_dir = pathlib.Path(figures_dir)
    cache_path = None
    if cache_dir is not None:
        cache_dir = pathlib.Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        sha = file_sha1(pdf_path)
        cache_path = cache_dir / (
            f"{_safe_pid(paper_id)}__{sha}__{quality}"
            f"__v{_PARSER_VERSION}.parsed.json"
        )
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    cached = json.load(f)
                # If the cached result was produced by a different parser tag
                # (e.g. an old PyMuPDF cache) or a different code version,
                # prefer a fresh parse so callers observe the new behavior
                # consistently.
                expected = "pypdfium2" if quality == "default" else "marker"
                if (cached.get("parser") == expected
                        and str(cached.get("parser_version") or "") == _PARSER_VERSION):
                    # Rematerialize figure PNGs into the caller's figures_dir
                    # whenever the cached image_path is missing or points at a
                    # different output directory (a different run). The cache
                    # holds parse results, not arbitrary previous-run artifacts.
                    if quality == "default" and cached.get("figures"):
                        figures_dir.mkdir(parents=True, exist_ok=True)
                        _rematerialize_figure_crops(
                            pdf_path, cached["figures"],
                            figures_dir, paper_id,
                        )
                        # Write the updated cache so subsequent runs in the same
                        # output dir avoid the re-render too.
                        try:
                            with open(cache_path, "w") as f:
                                json.dump(cached, f)
                        except Exception:
                            pass
                    cached["__from_cache"] = True
                    return cached
            except Exception:
                pass  # fall through and re-parse

    if quality == "high":
        result = parse_pdf_marker(pdf_path, paper_id, figures_dir)
    else:
        result = parse_pdf_pypdfium2(pdf_path, paper_id, figures_dir)
    result["__from_cache"] = False

    if cache_path is not None:
        try:
            with open(cache_path, "w") as f:
                json.dump(result, f)
        except Exception:
            pass

    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf_path")
    ap.add_argument("--paper-id", default="test")
    ap.add_argument("--figures-dir", default="./_figures")
    ap.add_argument("--quality", choices=["default", "high"], default="default")
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args()
    res = parse_pdf(args.pdf_path, args.paper_id, args.figures_dir, args.quality, args.cache_dir)
    print(json.dumps({
        "paper_id": res["paper_id"],
        "parser": res["parser"],
        "n_pages": res["n_pages"],
        "n_sentences": len(res["sentences"]),
        "n_figures": len(res["figures"]),
        "n_sections": len(res["sections"]),
        "from_cache": res.get("__from_cache", False),
        "elapsed": res["parse_seconds"],
    }, indent=2))
