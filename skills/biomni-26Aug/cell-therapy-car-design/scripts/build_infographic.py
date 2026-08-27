#!/usr/bin/env python3
"""
Build a one-page, data-driven summary INFOGRAPHIC for the CAR-design +
CRISPR-screen report, as a ReportLab Drawing that can be dropped onto page 1
of the PDF (see references/reporting.md).

This is intentionally a DATA plot (metric callouts + a ranked hit bar), NOT a
conceptual/mechanism diagram. Conceptual MOA/schematic art should be made with
the GenerateImage tool instead; this helper renders numbers you computed.

Inputs are passed as a small JSON of already-computed metrics so the infographic
never re-derives science -- it only visualizes verified values. Example JSON:

{
  "title": "CD19 CAR-T: design + SLICE CRISPR screen",
  "kpis": [
    {"label": "sgRNAs mapped", "value": "92.4%"},
    {"label": "Gini index", "value": "0.11"},
    {"label": "Genes tested", "value": "1209"},
    {"label": "Top proliferation brake", "value": "CBLB"},
    {"label": "Top essential (KO impairs)", "value": "CD3D"}
  ],
  "pos_hits": [["CBLB",0.545],["KCNC1",0.356],["PTEN",0.304],["CD5",0.240]],
  "neg_hits": [["CD3D",-0.597],["SUN2",-0.394],["ITK",-0.358],["IFNGR1",-0.344]],
  "footer": "Positive = KO enhances proliferation; Negative = KO impairs it (LFC)."
}

Usage:
    python build_infographic.py --metrics metrics.json --out infographic.pdf
    # --out may be .pdf (standalone, for QC) or .png (for embedding as an Image)

To embed inside a larger report, import make_infographic_drawing() and add the
returned Drawing (hAlign='CENTER') to your Platypus story.
"""
import argparse, json, os

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Group
from reportlab.graphics.charts.barcharts import HorizontalBarChart

# ---- Phylo palette (matches pdf-report-generation skill) ----
PHYLO_GOLD   = colors.HexColor("#D4A04A")
HEADING      = colors.HexColor("#111111")
BODY         = colors.HexColor("#2C2A26")
MUTED        = colors.HexColor("#8A8378")
CARD_BG      = colors.HexColor("#F9F7F3")
BORDER       = colors.HexColor("#D5CFC5")
POS_COLOR    = colors.HexColor("#C0603A")   # warm = enhances proliferation
NEG_COLOR    = colors.HexColor("#3A6FA0")   # cool = impairs proliferation

W_DEFAULT = 7.2 * inch     # fits inside US-Letter margins
H_DEFAULT = 4.7 * inch


def _safe_str(val):
    """Coerce nullable / NaN DataFrame values to a safe string for ReportLab.

    ReportLab ``String`` and ``Paragraph`` raise on ``float('nan')`` or ``None``
    because ``str(nan)`` -> 'nan' renders as literal text and ``None`` causes a
    TypeError.  This helper centralises the conversion so every call site is
    consistent and crash-proof.
    """
    if val is None:
        return ""
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val)


def _kpi_cards(d, kpis, x0, y0, w, row_h, font="Helvetica"):
    n = max(1, len(kpis))
    gap = 6
    cw = (w - gap * (n - 1)) / n
    for i, k in enumerate(kpis):
        x = x0 + i * (cw + gap)
        d.add(Rect(x, y0, cw, row_h, rx=6, ry=6,
                   fillColor=CARD_BG, strokeColor=BORDER, strokeWidth=0.8))
        d.add(String(x + cw / 2, y0 + row_h - 20, _safe_str(k["value"]),
                     fontName="Helvetica-Bold", fontSize=15,
                     fillColor=PHYLO_GOLD, textAnchor="middle"))
        # label may wrap into 2 lines
        label = _safe_str(k["label"])
        words, lines, cur = label.split(), [], ""
        for wd in words:
            if len(cur + " " + wd) > 18:
                lines.append(cur); cur = wd
            else:
                cur = (cur + " " + wd).strip()
        if cur:
            lines.append(cur)
        for j, ln in enumerate(lines[:2]):
            d.add(String(x + cw / 2, y0 + row_h - 34 - j * 10, ln,
                         fontName=font, fontSize=7, fillColor=MUTED,
                         textAnchor="middle"))


def _hit_bar(d, title, hits, x0, y0, w, h, bar_color, font="Helvetica"):
    d.add(String(x0, y0 + h + 6, title, fontName="Helvetica-Bold",
                 fontSize=9, fillColor=HEADING))
    if not hits:
        return
    labels = [_safe_str(g) for g, _ in hits]
    vals = [float(v) for _, v in hits]
    bc = HorizontalBarChart()
    bc.x = x0 + 46
    bc.y = y0
    bc.width = w - 92          # leave room at the right for value labels
    bc.height = h
    bc.data = [vals]
    bc.bars[0].fillColor = bar_color
    bc.bars[0].strokeColor = None
    bc.valueAxis.visible = False
    # pad both ends so bars never touch the value labels
    bc.valueAxis.valueMin = min(0, min(vals)) * 1.30
    bc.valueAxis.valueMax = max(0, max(vals)) * 1.30
    bc.categoryAxis.categoryNames = labels
    bc.categoryAxis.labels.fontName = font
    bc.categoryAxis.labels.fontSize = 8
    bc.categoryAxis.labels.fillColor = BODY
    bc.categoryAxis.strokeColor = BORDER
    bc.barWidth = 8
    # Let ReportLab position the numeric labels at each bar END (correctly aligned
    # to the actual bar geometry -- no manual geometry guessing).
    bc.barLabels.nudge = 12
    bc.barLabelFormat = "%+.2f"
    bc.barLabels.fontName = font
    bc.barLabels.fontSize = 7
    bc.barLabels.fillColor = BODY
    bc.barLabelArray = None
    d.add(bc)


def make_infographic_drawing(metrics, width=W_DEFAULT, height=H_DEFAULT):
    d = Drawing(width, height)
    # outer frame
    d.add(Rect(0, 0, width, height, fillColor=colors.white,
               strokeColor=BORDER, strokeWidth=1))
    # gold header band
    band_h = 30
    d.add(Rect(0, height - band_h, width, band_h, fillColor=PHYLO_GOLD,
               strokeColor=None))
    d.add(String(14, height - 20, metrics.get("title", "Study summary"),
                 fontName="Helvetica-Bold", fontSize=13, fillColor=colors.white))

    # KPI cards
    card_h = 56
    kpi_y = height - band_h - 12 - card_h
    _kpi_cards(d, metrics.get("kpis", []), 14, kpi_y, width - 28, card_h)

    # two hit bars side by side
    bars_top = kpi_y - 24
    bar_h = bars_top - 34
    half = (width - 28 - 24) / 2
    _hit_bar(d, "KO enhances proliferation (top brakes)",
             metrics.get("pos_hits", []), 14, 34, half, bar_h, POS_COLOR)
    _hit_bar(d, "KO impairs proliferation (essential)",
             metrics.get("neg_hits", []), 14 + half + 24, 34, half, bar_h, NEG_COLOR)

    # footer
    foot = metrics.get("footer", "")
    if foot:
        d.add(Line(14, 26, width - 14, 26, strokeColor=BORDER, strokeWidth=0.6))
        d.add(String(14, 14, foot, fontName="Helvetica-Oblique", fontSize=7.5,
                     fillColor=MUTED))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", required=True, help="JSON of computed metrics")
    ap.add_argument("--out", required=True, help=".pdf (standalone) or .png")
    ap.add_argument("--width", type=float, default=W_DEFAULT)
    ap.add_argument("--height", type=float, default=H_DEFAULT)
    args = ap.parse_args()

    with open(args.metrics) as f:
        metrics = json.load(f)
    d = make_infographic_drawing(metrics, args.width, args.height)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    ext = os.path.splitext(args.out)[1].lower()
    if ext == ".png":
        try:
            from reportlab.graphics import renderPM
            renderPM.drawToFile(d, args.out, fmt="PNG", dpi=200)
        except Exception:
            # Fallback: render to a temp PDF, then rasterize with PyMuPDF.
            # (renderPM needs the optional rlPyCairo backend, which may be absent.)
            #
            # COMMERCIAL-USE NOTICE — PyMuPDF (fitz) is AGPL-3.0 licensed.
            # AGPL-3.0 is copyleft and may require source-code disclosure for
            # network-accessible services; commercial redistribution may need a
            # commercial license from Artifex.  This fallback is only used when
            # the BSD-licensed renderPM/rlPyCairo backend is unavailable.
            # Embedding the Drawing directly (make_infographic_drawing) or
            # rendering to .pdf never touches PyMuPDF and has no such
            # restriction.  needs_commercial_review: evaluate Artifex
            # commercial-license terms before using this PNG fallback in a
            # commercial pipeline.
            import tempfile, fitz
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import letter
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
            c = canvas.Canvas(tmp, pagesize=letter)
            pw, ph = letter
            d.drawOn(c, (pw - args.width) / 2, (ph - args.height) / 2)
            c.showPage(); c.save()
            doc = fitz.open(tmp)
            doc[0].get_pixmap(dpi=200).save(args.out)
            doc.close()
    else:
        # standalone one-page PDF for QC
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        c = canvas.Canvas(args.out, pagesize=letter)
        pw, ph = letter
        d.drawOn(c, (pw - args.width) / 2, (ph - args.height) / 2)
        c.showPage()
        c.save()
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
