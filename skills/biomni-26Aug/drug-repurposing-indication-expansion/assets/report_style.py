"""ReportLab flowable helpers for the drug-repurposing report builder.

PDF layout, brand constants and figure-style conventions are owned by the
`pdf-report-generation` skill. This module does NOT declare its own palette or typography:
it **reads** the brand palette and fonts directly from that skill's SKILL.md at
runtime (single source of truth) and exposes structural helpers (styles, tables, callouts,
dividers, page chrome) built from them. `build_report.py` supplies only the scientific
content and the data-driven gates.

Why load instead of hardcode: a previous round stripped the brand constants and left the
builder drawing with a neutral grey palette (header/footer rules came out #CCCCCC instead
of the gold accent #D4A04A / warm-grey #D5CFC5). Sourcing the values from the platform means the
report can never silently drift out of brand again, and there is no hardcoded hex in the
report layer to go stale. If the platform skill cannot be found the report helpers fail
loudly rather than rendering an unbranded document.

The OKABE_ITO dict below is the ONE exception that stays local: it is imported by
make_figures.py for *figure/plot* colouring (colorblind-safe data series), which is figure
styling, not report styling, and is explicitly out of scope for the brand-source change.
"""
import os
import re

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.platypus import (Paragraph, Spacer, Table, TableStyle, Image,
                                HRFlowable, KeepTogether)

# ---------------------------------------------------------------------------
# Okabe-Ito colorblind-safe palette for matplotlib FIGURES (hex strings).
# This is FIGURE styling (used by make_figures.py), NOT report styling — it is
# intentionally local and must NOT be removed.
# ---------------------------------------------------------------------------
OKABE_ITO = {
    "blue":   "#0072B2",
    "orange": "#E69F00",
    "green":  "#009E73",
    "red":    "#D55E00",
    "purple": "#CC79A7",
    "sky":    "#56B4E9",
    "yellow": "#F0E442",
    "grey":   "#999999",
}

# ---------------------------------------------------------------------------
# Report-layer brand styling — LOADED from the pdf-report-generation skill.
# No hex literals live here; the values come from that skill's SKILL.md.
# ---------------------------------------------------------------------------
# Colour + font constants the report layer needs from the platform skill.
_REQUIRED = (
    "PHYLO_GOLD", "HEADING_COLOR", "BODY_TEXT", "MUTED_TEXT",
    "TABLE_HEADER_BG", "TABLE_HEADER_FG", "TABLE_ALT_ROW", "TABLE_BORDER",
    "DIVIDER_COLOR", "CALLOUT_BG", "CALLOUT_BORDER",
    "FONT_HEADING", "FONT_BODY", "FONT_ITALIC", "FONT_MONO",
)

_PLATFORM = None          # dict of loaded constants once resolved
_PLATFORM_ERR = None      # last load error (retried until success)


def _find_platform_skill():
    """Locate the pdf-report-generation SKILL.md. Resilient multi-path lookup.

    Order: explicit env override -> known system/user skill roots -> a search upward from
    this file's location (so a co-installed skill tree is found wherever it lives).
    """
    cands = []
    env = (os.environ.get("PDF_REPORT_GENERATION_SKILL")
           or os.environ.get("BIOMNI_PDF_REPORT_SKILL"))
    if env:
        cands.append(env if env.lower().endswith(".md")
                     else os.path.join(env, "SKILL.md"))
    cands += [
        "/mnt/skills/system/pdf-report-generation/SKILL.md",
        "/mnt/skills/user/pdf-report-generation/SKILL.md",
    ]
    here = os.path.dirname(os.path.abspath(__file__))
    up = here
    for _ in range(7):
        up = os.path.dirname(up)
        if not up or up == os.sep:
            break
        cands.append(os.path.join(up, "pdf-report-generation", "SKILL.md"))
        cands.append(os.path.join(up, "system", "pdf-report-generation", "SKILL.md"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "report_style: could not locate the pdf-report-generation SKILL.md "
        "(searched: " + "; ".join(c for c in cands if c) + "). Set the "
        "PDF_REPORT_GENERATION_SKILL environment variable to its path.")


def _load_platform_style():
    """Parse the brand palette + typography constants out of the platform SKILL.md.

    The pdf-report-generation skill is guidance-only (no importable module); its palette and
    fonts live inside fenced ```python blocks. We exec those blocks in an isolated namespace
    (HexColor pre-injected) and read the required constants back out. Blocks that reference
    runtime-only objects (doc/story/canvas/styles) simply fail to exec and are skipped.
    """
    path = _find_platform_skill()
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    blocks = re.findall(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if not blocks:
        raise RuntimeError(f"report_style: no python code blocks in {path}")
    ns = {"HexColor": HexColor}
    for block in blocks:
        try:
            exec(block, ns)  # noqa: S102 - trusted platform skill file
        except Exception:
            continue  # skip blocks that reference runtime-only names
    missing = [k for k in _REQUIRED if k not in ns]
    if missing:
        raise RuntimeError(
            f"report_style: pdf-report-generation SKILL.md ({path}) did not define "
            f"required brand constant(s): {missing}")
    return {k: ns[k] for k in _REQUIRED}


def _load():
    """Load the platform palette once (retry on prior failure). Never raises."""
    global _PLATFORM, _PLATFORM_ERR
    if _PLATFORM is not None:
        return
    try:
        _PLATFORM = _load_platform_style()
        _PLATFORM_ERR = None
        globals().update(_PLATFORM)  # expose PHYLO_GOLD, DIVIDER_COLOR, ... at module level
    except Exception as e:  # noqa: BLE001
        _PLATFORM_ERR = e


def _require():
    """Ensure the brand styling is loaded, else fail loudly (used by report helpers)."""
    _load()
    if _PLATFORM_ERR is not None:
        raise RuntimeError(
            "report_style: could not load brand styling from the "
            f"pdf-report-generation skill ({_PLATFORM_ERR}). Report styling must come from "
            "that skill; refusing to render an unbranded report.")


def brand_palette():
    """Public accessor: the loaded {name: value} brand constants (for tests/callers)."""
    _require()
    return dict(_PLATFORM)


def __getattr__(name):
    """PEP 562: resolve brand constants lazily so `report_style.DIVIDER_COLOR` works even
    if the module was imported before the platform skill was available, and raises a clear
    error (not a bare AttributeError) if styling truly cannot be loaded."""
    if name in _REQUIRED:
        _require()
        return _PLATFORM[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Attempt an eager load at import so module-level constants are populated in the common
# case; swallow any error here (report helpers call _require() and fail loudly on use, while
# `from report_style import OKABE_ITO` keeps working regardless).
_load()


def build_styles():
    """Return a ReportLab stylesheet with the skill's named styles added.

    Colours and font families come from the pdf-report-generation skill (loaded above);
    point sizes / spacing are layout choices local to this report.
    """
    _require()
    styles = getSampleStyleSheet()

    def add(n, **k):
        if n in styles.byName:
            for kk, vv in k.items():
                setattr(styles.byName[n], kk, vv)
        else:
            styles.add(ParagraphStyle(name=n, **k))

    add("ReportTitle", fontName=FONT_HEADING, fontSize=25, textColor=HEADING_COLOR, spaceAfter=6, leading=30)
    add("Subtitle", fontName=FONT_BODY, fontSize=12, textColor=MUTED_TEXT, spaceAfter=4)
    add("Attribution", fontName=FONT_ITALIC, fontSize=10, textColor=MUTED_TEXT, spaceAfter=8)
    add("SectionHead", fontName=FONT_HEADING, fontSize=16, textColor=HEADING_COLOR, spaceBefore=20, spaceAfter=9, leading=20)
    add("SubHead", fontName=FONT_HEADING, fontSize=12, textColor=HEADING_COLOR, spaceBefore=10, spaceAfter=5, leading=15)
    add("Body", fontName=FONT_BODY, fontSize=10.3, textColor=BODY_TEXT, alignment=TA_JUSTIFY, spaceAfter=8, leading=15)
    add("BodyL", fontName=FONT_BODY, fontSize=10.3, textColor=BODY_TEXT, alignment=TA_LEFT, spaceAfter=6, leading=15)
    add("Caption", fontName=FONT_ITALIC, fontSize=9, textColor=MUTED_TEXT, alignment=TA_CENTER, spaceAfter=14, leading=12)
    add("CalloutTitle", fontName=FONT_HEADING, fontSize=10.5, textColor=HEADING_COLOR, spaceAfter=3)
    add("CalloutBody", fontName=FONT_BODY, fontSize=9.8, textColor=BODY_TEXT, alignment=TA_LEFT, leading=14)
    add("Ref", fontName=FONT_BODY, fontSize=8.5, textColor=BODY_TEXT, alignment=TA_LEFT, spaceAfter=4, leading=11)
    add("TblCell", fontName=FONT_BODY, fontSize=8.3, textColor=BODY_TEXT, leading=10)
    add("TblCellB", fontName=FONT_HEADING, fontSize=8.3, textColor=BODY_TEXT, leading=10)
    add("TblHead", fontName=FONT_HEADING, fontSize=8.5, textColor=TABLE_HEADER_FG, leading=10)
    return styles


def divider(w=492):
    _require()
    return HRFlowable(width=w, thickness=1, color=DIVIDER_COLOR, spaceAfter=10, spaceBefore=4)


def callout(title, body, styles, w=468):
    _require()
    inner = [Paragraph(title, styles["CalloutTitle"]), Paragraph(body, styles["CalloutBody"])]
    t = Table([[inner]], colWidths=[w])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, CALLOUT_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    t.hAlign = "CENTER"
    return t


def fig(png_path, w, cap, styles):
    """Embed a PNG figure (auto-scaled to width w) with a centered caption, kept together."""
    from PIL import Image as PILImage
    iw, ih = PILImage.open(png_path).size
    h = w * ih / iw
    im = Image(png_path, width=w, height=h)
    im.hAlign = "CENTER"
    return KeepTogether([im, Spacer(1, 4), Paragraph(cap, styles["Caption"])])


def make_table(header, data_rows, colWidths, styles):
    """Build a table. header=list[str] (may contain <sub>/<super>), data_rows=list[list[str]]."""
    _require()
    rows = [[Paragraph(h, styles["TblHead"]) for h in header]]
    for r in data_rows:
        rows.append([Paragraph(str(c), styles["TblCell"]) for c in r])
    t = Table(rows, colWidths=colWidths, repeatRows=1)
    t.hAlign = "CENTER"
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        *[("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW) for i in range(2, len(rows), 2)],
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


def table_style(nrows, valign="MIDDLE"):
    """Return a TableStyle for a table with `nrows` rows (row 0 = header).

    Used when a caller builds the Table rows itself (e.g. mixing TblCell / TblCellB /
    Paragraph cells) but wants the standard header fill, zebra striping, grid and padding.
    `valign` controls vertical alignment ('MIDDLE' for numeric tables, 'TOP' for text-heavy
    tables like the literature evidence table).
    """
    _require()
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        *[("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW) for i in range(2, nrows, 2)],
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), valign)])


def page_chrome_factory(title):
    """Return a canvas callback that draws the header/footer with the given report title.

    Header rule = gold accent (DIVIDER_COLOR, #D4A04A); footer rule = warm grey
    (TABLE_BORDER, #D5CFC5) — both taken from the pdf-report-generation skill.
    """
    _require()

    def page_chrome(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFont(FONT_BODY, 9)
        canvas.setFillColor(MUTED_TEXT)
        # Truncate very long titles so they fit the header
        ht = title if len(title) <= 78 else title[:75] + "..."
        canvas.drawString(60, h - 40, ht)
        canvas.setStrokeColor(DIVIDER_COLOR)      # gold accent, loaded from pdf-report-generation
        canvas.setLineWidth(1)
        canvas.line(60, h - 48, w - 60, h - 48)
        canvas.setStrokeColor(TABLE_BORDER)       # warm grey, loaded from pdf-report-generation
        canvas.setLineWidth(0.75)
        canvas.line(60, 40, w - 60, 40)
        canvas.setFont(FONT_BODY, 8)
        canvas.setFillColor(MUTED_TEXT)
        canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
        canvas.drawRightString(w - 60, 26, "Generated by Biomni")
        canvas.restoreState()
    return page_chrome
