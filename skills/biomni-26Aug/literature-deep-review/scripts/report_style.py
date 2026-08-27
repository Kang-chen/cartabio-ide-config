#!/usr/bin/env python3
"""Report styling mirrored from the platform ``pdf-report-generation`` skill.

The platform owns report styling. That skill is guidance-only — there is no
importable ``pdf_report_generation`` module — so its palette, fonts, page
geometry and header/footer canvas routine are mirrored here as the single
source of truth for this skill's PDF. ``build_pdf.py`` assembles the report's
*content* and pulls every visual decision from this module, so no hex literal,
ParagraphStyle or page-chrome routine lives in the builder itself.

Colours are the pdf-report-generation palette: gold ``#D4A04A`` (header rule,
table headers, dividers) and warm-gray ``#D5CFC5`` (footer line, table grid).

Body text is set in **DejaVu Sans** — a stock, non-brand Unicode font shipped
with Matplotlib — rather than base-14 Helvetica. This skill renders verbatim
scientific quotations (β, ε4, Greek letters, en dashes) that Helvetica cannot
encode, and ``verify_pdf_quotes.py`` checks those exact sentences survive into
the rendered PDF; Helvetica would drop the glyphs and could fail that gate.
The ``#D4A04A`` / ``#D5CFC5`` chrome and the overall layout follow the platform
skill exactly.
"""
from __future__ import annotations

import pathlib
import sys
import unicodedata
from dataclasses import dataclass

# --- pdf-report-generation palette (mirrored; the skill is guidance-only) ----
GOLD = "#D4A04A"          # PHYLO_GOLD: header rule, table header background, dividers
RULE = "#D5CFC5"          # TABLE_BORDER: footer line, table grid lines
HEADING = "#111111"       # near-black headings / title
BODY = "#2C2A26"          # warm dark body text
MUTED = "#8A8378"         # captions, footnotes, secondary text
TABLE_HEADER_BG = GOLD
TABLE_HEADER_FG = "#FFFFFF"
TABLE_ALT_ROW = "#F9F7F3"
LINK = "#0563C1"          # hyperlinks
# Skill-semantic colours kept to preserve scientific-content distinctions the
# report depends on (a positive/support marker, and a reviewer-inference marker
# that must read as visually distinct from an observed result).
SUPPORT = "#75A025"       # PHYLO_GREEN: support / positive
INFERENCE = "#8A5A00"     # distinct amber: reviewer inference is not observation

# --- page geometry (letter, generous scientific margins), points -------------
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
LEFT_MARGIN = 0.9 * 72
RIGHT_MARGIN = 0.9 * 72
TOP_MARGIN = 0.85 * 72
BOTTOM_MARGIN = 0.8 * 72

# One indent for every part of a claim below its heading — facets, quotes and
# locators all hang off the same left edge, so the reader sees one block per
# claim instead of a staircase.
FACET_INDENT = 10

# DejaVu faces (stock, Unicode). Registered under these ReportLab names.
BODY_FAMILY = "DejaVuSans"
_FACES = {
    "normal": "DejaVuSans.ttf",
    "bold": "DejaVuSans-Bold.ttf",
    "italic": "DejaVuSans-Oblique.ttf",
    "boldItalic": "DejaVuSans-BoldOblique.ttf",
}
_FACE_NAMES = {
    "normal": BODY_FAMILY,
    "bold": f"{BODY_FAMILY}-Bold",
    "italic": f"{BODY_FAMILY}-Oblique",
    "boldItalic": f"{BODY_FAMILY}-BoldOblique",
}
# Base-14 graceful fallback if DejaVu is unavailable at build time.
BASE14_BODY = "Helvetica"
BASE14_BOLD = "Helvetica-Bold"
MONO = "Courier"          # ASCII machine locators; matches pdf-report-generation


@dataclass(frozen=True)
class FontSet:
    body: str
    display: str
    mono: str
    embedded: bool
    source: str = ""


def _font_candidate_dirs() -> list[pathlib.Path]:
    dirs: list[pathlib.Path] = []
    try:
        import matplotlib

        dirs.append(
            pathlib.Path(matplotlib.__file__).resolve().parent
            / "mpl-data" / "fonts" / "ttf"
        )
    except Exception:  # noqa: BLE001 - absence is reported by register()
        pass
    dirs += [
        pathlib.Path("/usr/share/fonts/truetype/dejavu"),
        pathlib.Path("/usr/share/fonts/dejavu"),
        pathlib.Path("/usr/local/share/fonts"),
    ]
    return dirs


def _resolve_faces() -> tuple[dict[str, pathlib.Path], str]:
    for directory in _font_candidate_dirs():
        try:
            if not directory.is_dir():
                continue
        except OSError:
            continue
        faces = {role: directory / name for role, name in _FACES.items()}
        if all(path.exists() for path in faces.values()):
            return faces, str(directory)
    return {}, ""


_REGISTERED: FontSet | None = None


def register() -> FontSet:
    """Register the DejaVu Unicode faces and return the resolved font names.

    Degrades to base-14 Helvetica (with a warning) if the DejaVu TTFs cannot be
    found, so a build never hard-fails on font availability.
    """
    global _REGISTERED
    if _REGISTERED is not None:
        return _REGISTERED

    faces, source = _resolve_faces()
    if not faces:
        _REGISTERED = FontSet(BASE14_BODY, BASE14_BOLD, MONO, False)
        print(
            "WARN: DejaVu Unicode faces not found; falling back to base-14 "
            f"Helvetica. Searched: "
            f"{', '.join(str(p) for p in _font_candidate_dirs())}",
            file=sys.stderr,
        )
        return _REGISTERED

    try:
        from reportlab.lib.fonts import addMapping
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        for role, path in faces.items():
            pdfmetrics.registerFont(TTFont(_FACE_NAMES[role], str(path)))
        pdfmetrics.registerFontFamily(
            BODY_FAMILY,
            normal=_FACE_NAMES["normal"],
            bold=_FACE_NAMES["bold"],
            italic=_FACE_NAMES["italic"],
            boldItalic=_FACE_NAMES["boldItalic"],
        )
        for bold in (0, 1):
            for italic in (0, 1):
                role = (
                    "boldItalic" if bold and italic
                    else "bold" if bold
                    else "italic" if italic
                    else "normal"
                )
                addMapping(BODY_FAMILY, bold, italic, _FACE_NAMES[role])
    except Exception as exc:  # noqa: BLE001 - any registration failure degrades
        _REGISTERED = FontSet(BASE14_BODY, BASE14_BOLD, MONO, False)
        print(
            f"WARN: could not register DejaVu report fonts: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _REGISTERED

    _REGISTERED = FontSet(
        BODY_FAMILY, _FACE_NAMES["bold"], MONO, True, source
    )
    return _REGISTERED


def describe() -> str:
    fonts = register()
    if not fonts.embedded:
        return "fonts: DejaVu unavailable; base-14 Helvetica fallback in use"
    return f"fonts: body={fonts.body}, headings={fonts.display} embedded from {fonts.source}"


# Exact, meaning-preserving normalization only (presentation ligatures,
# non-breaking/zero-width spaces, soft hyphens). Characters that carry meaning
# are left intact for DejaVu to render.
_EXACT: dict[int, str] = {
    0x00A0: " ", 0x202F: " ", 0x2007: " ", 0x2009: " ", 0x200A: " ",
    0x200B: "", 0x200C: "", 0x200D: "", 0x00AD: "", 0xFEFF: "",
    0xFB00: "ff", 0xFB01: "fi", 0xFB02: "fl", 0xFB03: "ffi", 0xFB04: "ffl",
}


def normalize(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFC", str(text)).translate(_EXACT)


def build_styles():
    """The ParagraphStyle set the builder renders with, mirrored from the
    platform skill and coloured from the palette above."""
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    fonts = register()
    body_font, display_font, mono_font = fonts.body, fonts.display, fonts.mono
    ss = getSampleStyleSheet()

    def add(name, **kw):
        kw.setdefault("fontName", body_font)
        ss.add(ParagraphStyle(name=name, **kw))

    add("DocTitle", parent=ss["Title"], fontSize=22, leading=27,
        textColor=HEADING, spaceAfter=6, alignment=0, fontName=display_font)
    add("Subtitle", parent=ss["Normal"], fontSize=12, leading=16,
        textColor=GOLD, spaceAfter=10)
    add("Meta", parent=ss["Normal"], fontSize=8.5, leading=12, textColor=MUTED,
        spaceAfter=4)
    add("H1", parent=ss["Heading1"], fontSize=16, leading=20, textColor=HEADING,
        spaceBefore=14, spaceAfter=8, fontName=display_font)
    add("H2", parent=ss["Heading2"], fontSize=12.5, leading=16,
        textColor=HEADING, spaceBefore=10, spaceAfter=5, fontName=display_font)
    add("ClaimHead", parent=ss["Normal"], fontSize=10.5, leading=14,
        textColor=HEADING, spaceBefore=9, spaceAfter=3, fontName=display_font)
    # Ragged right, not justified: reportlab does not hyphenate, so justifying
    # this measure opened rivers wide enough to read as a layout fault.
    add("Body", parent=ss["Normal"], fontSize=9.5, leading=13.5, textColor=BODY,
        spaceAfter=6)
    add("Support", parent=ss["Normal"], fontSize=8.5, leading=12,
        textColor=SUPPORT, spaceAfter=4)
    add("Quote", parent=ss["Normal"], fontSize=9.5, leading=13.5, textColor=BODY,
        leftIndent=FACET_INDENT + 8, rightIndent=8, spaceAfter=2)
    add("Locator", parent=ss["Normal"], fontSize=7.5, leading=10.5,
        textColor=MUTED, leftIndent=FACET_INDENT + 8, fontName=mono_font,
        spaceAfter=6)
    add("Caption", parent=ss["Normal"], fontSize=8, leading=11, textColor=MUTED,
        spaceBefore=3, spaceAfter=10, alignment=1)
    # A narrative facet and the visually distinct style reviewer inference is
    # rendered in. Both sit at the SAME indent — they are sibling facets of one
    # claim — so colour, not position, is what marks inference.
    add("Facet", parent=ss["Normal"], fontSize=9.5, leading=13.5, textColor=BODY,
        leftIndent=FACET_INDENT, spaceAfter=5)
    add("Inference", parent=ss["Normal"], fontSize=9.5, leading=13.5,
        textColor=INFERENCE, leftIndent=FACET_INDENT, spaceAfter=5)
    add("Cell", parent=ss["Normal"], fontSize=8, leading=11, textColor=BODY)
    add("CellHead", parent=ss["Normal"], fontSize=8.5, leading=11,
        textColor=TABLE_HEADER_FG)
    add("TOC0", parent=ss["Normal"], fontSize=10, leading=14, leftIndent=0,
        firstLineIndent=-14, textColor=HEADING)
    add("TOC1", parent=ss["Normal"], fontSize=9, leading=12.5, leftIndent=16,
        firstLineIndent=-14, textColor=MUTED)
    return ss


def make_header_footer(title: str):
    """Canvas callback for every page: muted running title with a gold
    ``#D4A04A`` underline, and a ``#D5CFC5`` footer rule with a centered page
    number. Mirrors the pdf-report-generation ``page_header_footer`` routine.
    """
    from reportlab.lib.colors import HexColor

    fonts = register()
    head_font = fonts.body
    running = normalize(title)[:95]

    def draw(canvas, doc):
        page_w, page_h = doc.pagesize
        canvas.saveState()
        # Header: running title, muted, left-aligned.
        canvas.setFont(head_font, 9)
        canvas.setFillColor(HexColor(MUTED))
        canvas.drawString(LEFT_MARGIN, page_h - 40, running)
        # Gold underline beneath the header (#D4A04A).
        canvas.setStrokeColor(HexColor(GOLD))
        canvas.setLineWidth(1)
        canvas.line(LEFT_MARGIN, page_h - 48,
                    page_w - RIGHT_MARGIN, page_h - 48)
        # Footer: thin warm-gray rule (#D5CFC5) + centered page number.
        canvas.setStrokeColor(HexColor(RULE))
        canvas.setLineWidth(0.75)
        canvas.line(LEFT_MARGIN, 40, page_w - RIGHT_MARGIN, 40)
        canvas.setFont(head_font, 8)
        canvas.setFillColor(HexColor(MUTED))
        canvas.drawCentredString(page_w / 2, 26,
                                 f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    return draw


if __name__ == "__main__":  # pragma: no cover
    print(describe())
