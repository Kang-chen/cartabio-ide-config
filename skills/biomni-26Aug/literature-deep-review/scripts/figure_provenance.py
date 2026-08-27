#!/usr/bin/env python3
"""Draw, on the figure itself, the evidence that it belongs under its claim.

A reader looking at an embedded crop has no way to tell why THAT figure was
chosen out of the paper's twelve. Two shipped reports reproduced 27 figures
between them and not one carried a mark explaining the choice.

The annotation machinery for this already existed and reached nobody.
``export_figures._draw_annotations`` drew red boxes, and ``report_model``
preferred the annotated copy over the plain crop — but the boxes came from
``ocr_lines``, which was populated ONLY by evidence rows whose ``block_type`` was
``figure_ocr``: an OCR line that had itself been quoted. Across both reports there
were 0 and 1 such rows against 35 caption anchors, so ``annotated_image`` was
always ``None``.

The deeper reason is that figure selection scores CAPTIONS
(``figure_selection.caption_relevance``), and a caption is text underneath the
image. Nothing in the picture was ever the recorded reason, so there was nothing
to box.

**So this module closes the loop rather than decorating it.** It matches the
claim's terms against in-figure OCR text using the SAME stemmer the selection
score uses, boxes each hit, and hands the matches back so
``figure_selection`` can count them. The boxes then mark something that genuinely
contributed to the figure winning its place, and the caption can say so without
overclaiming.

Honesty rules this module follows:

  * A box is only drawn for a term the CLAIM actually contains. It never boxes
    "something interesting".
  * When OCR is unavailable or finds no claim term, NO annotated image is
    produced and the caller must say the selection was caption-only. Silence
    would imply the picture had been checked.
  * The plain crop is never modified in place; annotation is a separate file, so
    the unaltered reproduction of the published figure always survives.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from figure_selection import _terms as claim_terms
from figure_selection import surface_form

# Colour for the in-figure term boxes. Chosen to sit clearly on top of both
# fluorescence micrographs (dark) and plotted panels (white).
BOX_COLOR = (214, 39, 40)          # brand-adjacent red
BOX_WIDTH = 3

# Longest OCR line that may be boxed. A whole paragraph of figure-embedded text
# (some journals rasterize the caption INTO the image) is not a "term hit" and
# boxing it would ring most of the picture.
MAX_LINE_CHARS = 60

# Least confident OCR line worth trusting for an annotation. Panel labels are
# short and crisp; anything the engine is unsure of is not worth drawing a box
# around and labelling as evidence.
MIN_CONF = 0.45

# Shortest stem worth boxing. `figure_selection._terms` floors at 3 because a
# caption saying "GRN" or "FTD" is genuinely about the claim and must score --
# but a BOX is a visual assertion about one region, and in a GRN paper "grn"
# appears in half the panel titles, so boxing it marks everything and explains
# nothing. Scoring keeps its floor; drawing gets a higher one.
MIN_BOX_TERM_CHARS = 4

# Hyphen fragments `_terms` splits out of compounds. "anti-sortilin" yields
# "anti", which then boxes every "Anti-sort" tick label on an axis while the
# specific compound sits right there unused.
_FRAGMENTS = frozenset({
    "anti", "non", "pre", "post", "sub", "over", "under", "multi", "mono",
    "high", "low", "type", "wild", "full", "half", "long", "short",
})

# A term matching many lines is wallpaper, not evidence: it no longer
# distinguishes this region from the rest of the picture.
MAX_HITS_PER_TERM = 3

# Total boxes on one figure. Past this the annotation competes with the figure
# instead of pointing into it.
MAX_BOXES = 8


@dataclass
class TermHit:
    """One in-figure text region matching a term from the claim."""
    term: str                       # the claim term, stemmed
    text: str                       # the OCR line as read
    conf: float
    bbox: list[tuple[float, float]]  # polygon in the crop's pixel space


@dataclass
class Provenance:
    """Why a figure is shown, in the form a caption and a manifest can use."""
    caption_terms: list[str] = field(default_factory=list)
    figure_hits: list[TermHit] = field(default_factory=list)
    relevance: float = 0.0
    reason: str = ""
    annotated_image: str | None = None
    ocr_available: bool = True

    def as_manifest(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "relevance": round(self.relevance, 4),
            "caption_terms": sorted(self.caption_terms),
            "figure_term_hits": [
                {"term": h.term, "text": h.text, "conf": round(h.conf, 3)}
                for h in self.figure_hits
            ],
            "ocr_available": self.ocr_available,
        }

    def caption_note(self) -> str:
        """The sentence a report caption uses to justify the figure.

        Never claims the picture was inspected when it was not: with no OCR the
        note names the caption terms only, and says so.
        """
        bits: list[str] = []
        if self.caption_terms:
            bits.append("matched on caption terms "
                        + ", ".join(sorted(self.caption_terms)[:6]))
        if self.figure_hits:
            terms = sorted({h.term for h in self.figure_hits})
            bits.append(f"boxed in-figure text for {', '.join(terms[:6])}")
        elif not self.ocr_available:
            bits.append("in-figure text not read (OCR unavailable), so no "
                        "regions are boxed")
        elif not self.figure_hits:
            bits.append("no claim term appears as text inside the figure")
        if not bits:
            return ""
        return "Why this figure: " + "; ".join(bits) + "."


def find_term_hits(ocr_lines: Sequence[dict[str, Any]],
                   claim_text: str, scope: str = "") -> list[TermHit]:
    """OCR lines containing a term the claim uses.

    Matching goes through ``figure_selection._terms``, so an in-figure "PGRN-/-"
    matches a claim that says "progranulin deficiency" by the same stemming rule
    that scored the caption. One vocabulary, two places it is applied.
    """
    wanted = {t for t in claim_terms(claim_text) | claim_terms(scope)
              if len(t) >= MIN_BOX_TERM_CHARS and t not in _FRAGMENTS}
    if not wanted:
        return []

    found: list[tuple[str, TermHit]] = []
    for line in ocr_lines or []:
        text = str(line.get("text") or "").strip()
        if not text or len(text) > MAX_LINE_CHARS:
            continue
        conf = float(line.get("conf") or 0.0)
        if conf < MIN_CONF:
            continue
        matched = claim_terms(text) & wanted
        if not matched:
            continue
        bbox = _normalize_bbox(line.get("bbox"))
        if bbox is None:
            continue
        # The most specific term the line matched, not the alphabetically first:
        # a line reading "anti-sortilin antibody" should be boxed for sortilin.
        stem = max(sorted(matched), key=len)
        # Label the box with the word, not the stem: a box captioned
        # "frontotempor" tells the reader about the stemmer, not the evidence.
        label = surface_form(stem, text, claim_text, scope)
        found.append((stem, TermHit(term=label, text=text, conf=conf, bbox=bbox)))

    return _prune(found)


def _prune(found: list[tuple[str, TermHit]]) -> list[TermHit]:
    """Keep the most specific, most confident boxes within the caps.

    An unpruned pass boxed one figure thirty times over: every axis tick, every
    panel title, the same three generic stems again and again. Thirty boxes say
    "this figure contains words", which is not why it was selected.
    """
    ranked = sorted(found, key=lambda pair: (-len(pair[0]), -pair[1].conf))
    per_term: dict[str, int] = {}
    kept: list[TermHit] = []
    for stem, hit in ranked:
        if per_term.get(stem, 0) >= MAX_HITS_PER_TERM:
            continue
        per_term[stem] = per_term.get(stem, 0) + 1
        kept.append(hit)
        if len(kept) >= MAX_BOXES:
            break
    return kept


def _normalize_bbox(bbox: Any) -> list[tuple[float, float]] | None:
    """Accept an EasyOCR 4-point polygon or an [x0,y0,x1,y1] rectangle."""
    if not bbox:
        return None
    if (isinstance(bbox, (list, tuple)) and len(bbox) == 4
            and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in bbox)):
        return [(float(x), float(y)) for x, y in bbox]
    if (isinstance(bbox, (list, tuple)) and len(bbox) == 4
            and all(isinstance(v, (int, float)) for v in bbox)):
        x0, y0, x1, y1 = (float(v) for v in bbox)
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return None


def annotate(src_png: pathlib.Path, dst_png: pathlib.Path,
             hits: Iterable[TermHit]) -> bool:
    """Write a copy of ``src_png`` with a labelled box around each hit.

    Returns False (leaving no file) when there is nothing to draw or the draw
    fails. Annotation is cosmetic; it must never cost the figure, so the caller
    falls back to the plain crop.
    """
    hits = list(hits)
    if not hits:
        return False
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001 - Pillow absent: skip annotation
        return False
    try:
        with Image.open(src_png) as im:
            im = im.convert("RGB")
            draw = ImageDraw.Draw(im)
            for hit in hits:
                _draw_hit(draw, hit)
            dst_png.parent.mkdir(parents=True, exist_ok=True)
            im.save(str(dst_png))
        return True
    except Exception:  # noqa: BLE001 - any decode/draw failure is a skip
        return False



def _draw_hit(draw, hit: TermHit) -> None:
    """Box one hit. The box marks WHERE; the caption already says WHAT.

    This used to stamp the matched term above the box on an opaque background.
    The space directly above a hit is not empty — the hit usually IS a panel
    title or an axis label, so the stamp painted over the figure's own text and
    the published figure came back damaged: "latozinemab mab concentration in
    HVs", "sortilinilin levels", axis ticks buried under repeated "anti". A
    report whose entire premise is faithful reproduction cannot hand back
    corrupted source figures to explain itself. The caption names every boxed
    term, so the label was redundant even when it landed on whitespace.
    """
    draw.polygon([(float(x), float(y)) for x, y in hit.bbox],
                 outline=BOX_COLOR, width=BOX_WIDTH)
