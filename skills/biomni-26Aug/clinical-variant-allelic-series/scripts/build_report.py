#!/usr/bin/env python3
"""
build_report.py — Assemble a Phylo-branded PDF report for a gene allelic series.

Follows the pdf-report-generation skill conventions (Phylo palette, Helvetica,
Platypus flowables, gold header underline, validation). Structure:
  Title + infographic -> Executive Summary -> Introduction -> Methods ->
  Results (tiered series, counts, figures, key-allele vignettes with [N] cites)
  -> Conclusions (+ limitations callout) -> References -> Next steps.

The report CONTENT (prose, references, key-allele table, next steps) is supplied
by the agent as a JSON config so this builder stays gene-agnostic. Figures and
the master table are read from the standard output directory.

CRITICAL text hygiene (baked in):
  - ascii_clean(): Helvetica lacks glyphs for en/em dashes, curly quotes, Greek,
    arrows -> transliterate to ASCII so references don't render as black boxes.
  - Never use unicode sub/superscripts; use <sub>/<super> tags in Paragraphs.

Usage:
    python build_report.py --gene EGFR \
        --outdir /mnt/results/EGFR_allelic_series \
        --config /mnt/results/EGFR_allelic_series/report_config.json
"""
import argparse
import json
import os
import re
import sys
import unicodedata

import pandas as pd
from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, PageBreak, HRFlowable,
                                KeepTogether)

# ---- Phylo palette ----------------------------------------------------------
PHYLO_GOLD = HexColor("#D4A04A")
HEADING = HexColor("#111111")
BODY = HexColor("#2C2A26")
MUTED = HexColor("#8A8378")
TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F9F7F3")
TABLE_BORDER = HexColor("#D5CFC5")
CALLOUT_BG = HexColor("#FAF9F3")

FONT_H = "Helvetica-Bold"
FONT_B = "Helvetica"
FONT_I = "Helvetica-Oblique"

_ASCII_REPL = {
    "\u2013": "-", "\u2014": "-", "\u2010": "-", "\u2011": "-", "\u2012": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...",
    "\u00d7": "x", "\u00b1": "+/-", "\u2009": " ", "\u00a0": " ", "\u2192": "->",
    "\u2265": ">=", "\u2264": "<=", "\u2212": "-",
}


def ascii_clean(s):
    if s is None:
        return ""
    s = str(s)
    for k, v in _ASCII_REPL.items():
        s = s.replace(k, v)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


_SIG_PRIORITY = ["Pathogenic", "Likely pathogenic", "drug response",
                 "Conflicting classifications of pathogenicity",
                 "Uncertain significance", "Likely benign", "Benign"]


def clean_sig(s):
    """Deduplicate + priority-order a compound ClinVar significance string."""
    if not s:
        return ""
    parts = {p.strip() for p in re.split(r"[;,]", str(s)) if p.strip()}
    ordered = [p for p in _SIG_PRIORITY if p in parts]
    ordered += sorted(parts - set(_SIG_PRIORITY))
    out = "; ".join(ordered)
    return ascii_clean(out.replace("Conflicting classifications of pathogenicity", "Conflicting"))


def styles():
    st = getSampleStyleSheet()
    st.add(ParagraphStyle(name="RTitle", fontName=FONT_H, fontSize=25,
                          textColor=HEADING, leading=30, spaceAfter=6))
    st.add(ParagraphStyle(name="RSub", fontName=FONT_B, fontSize=11,
                          textColor=PHYLO_GOLD, spaceAfter=4))
    st.add(ParagraphStyle(name="Attr", fontName=FONT_I, fontSize=10,
                          textColor=MUTED, spaceAfter=8))
    st.add(ParagraphStyle(name="H1", fontName=FONT_H, fontSize=16,
                          textColor=HEADING, spaceBefore=18, spaceAfter=8))
    st.add(ParagraphStyle(name="H2", fontName=FONT_H, fontSize=12.5,
                          textColor=HEADING, spaceBefore=10, spaceAfter=5))
    st.add(ParagraphStyle(name="Bd", fontName=FONT_B, fontSize=10.3,
                          textColor=BODY, alignment=TA_JUSTIFY, leading=15, spaceAfter=7))
    st.add(ParagraphStyle(name="Cap", fontName=FONT_I, fontSize=9,
                          textColor=MUTED, alignment=TA_CENTER, spaceAfter=12))
    st.add(ParagraphStyle(name="Cell", fontName=FONT_B, fontSize=8.4,
                          textColor=BODY, leading=11))
    st.add(ParagraphStyle(name="CellH", fontName=FONT_H, fontSize=8.6,
                          textColor=TABLE_HEADER_FG, leading=11))
    st.add(ParagraphStyle(name="Ref", fontName=FONT_B, fontSize=8.6,
                          textColor=BODY, leading=12, spaceAfter=4))
    return st


def page_chrome(title):
    def _cb(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFont(FONT_B, 9)
        canvas.setFillColor(MUTED)
        canvas.drawString(60, h - 40, ascii_clean(title)[:90])
        canvas.setStrokeColor(PHYLO_GOLD)
        canvas.setLineWidth(1)
        canvas.line(60, h - 48, w - 60, h - 48)
        canvas.setStrokeColor(TABLE_BORDER)
        canvas.setLineWidth(0.75)
        canvas.line(60, 40, w - 60, 40)
        canvas.setFont(FONT_B, 8)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
        canvas.restoreState()
    return _cb


def _img(path, max_w=492):
    from PIL import Image as PILImage
    iw, ih = PILImage.open(path).size
    w = min(max_w, iw)
    h = w * ih / iw
    im = Image(path, width=w, height=h)
    im.hAlign = "CENTER"
    return im


def divider():
    return HRFlowable(width=480, thickness=1, color=PHYLO_GOLD,
                      spaceAfter=10, spaceBefore=4)


def callout(text, st):
    t = Table([[Paragraph(ascii_clean(text), st["Bd"])]], colWidths=[456])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, PHYLO_GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    return t


def make_table(headers, rows, st, colwidths):
    data = [[Paragraph(f"<b>{ascii_clean(h)}</b>", st["CellH"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(ascii_clean(str(c)), st["Cell"]) for c in r])
    t = Table(data, colWidths=colwidths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), PHYLO_GOLD),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    t.setStyle(TableStyle(style))
    t.hAlign = "CENTER"
    return t


def build(gene, outdir, config_path):
    st = styles()
    with open(config_path) as fh:
        cfg = json.load(fh)
    figdir = os.path.join(outdir, "figures")
    stats = {}
    stats_path = os.path.join(outdir, f"{gene}_summary_stats.json")
    if os.path.exists(stats_path):
        stats = json.load(open(stats_path))
    master = pd.read_csv(os.path.join(outdir, f"{gene}_allelic_series_master.csv"))

    title = cfg.get("title", f"{gene} Allelic Series and Clinical Actionability")
    out_pdf = os.path.join(outdir, f"report_{gene}_allelic_series.pdf")
    doc = SimpleDocTemplate(out_pdf, pagesize=letter, topMargin=58, bottomMargin=52,
                            leftMargin=60, rightMargin=60,
                            title=ascii_clean(title))
    story = []

    def P(text, style="Bd"):
        story.append(Paragraph(ascii_clean(text), st[style]))

    # ---- Title + infographic ----
    story.append(Spacer(1, 24))
    P(title, "RTitle")
    P(cfg.get("subtitle", "ClinVar + CIViC integrated variant actionability"), "RSub")
    P(f"<i>Generated by Biomni  |  {cfg.get('access_date','')}</i>", "Attr")
    story.append(divider())
    infographic = cfg.get("infographic")
    if infographic and os.path.exists(infographic):
        story.append(Spacer(1, 6))
        story.append(_img(infographic, max_w=470))
        story.append(Paragraph(ascii_clean(cfg.get("infographic_caption",
                     f"Figure 1. {gene} actionability overview.")), st["Cap"]))

    # ---- Executive summary ----
    story.append(Spacer(1, 6))
    P("Executive Summary", "H1")
    for para in cfg.get("executive_summary", []):
        P(para)

    # ---- Introduction ----
    P("Introduction", "H1")
    for para in cfg.get("introduction", []):
        P(para)

    # ---- Methods ----
    story.append(PageBreak())
    P("Methods", "H1")
    for para in cfg.get("methods", []):
        P(para)

    # ---- Results ----
    P("Results", "H1")
    for block in cfg.get("results", []):
        if block.get("heading"):
            P(block["heading"], "H2")
        for para in block.get("text", []):
            P(para)
        fig = block.get("figure")
        if fig:
            fpath = fig if os.path.isabs(fig) else os.path.join(figdir, fig)
            if os.path.exists(fpath):
                story.append(KeepTogether([_img(fpath),
                             Paragraph(ascii_clean(block.get("caption", "")), st["Cap"])]))

    # ---- Key-allele table ----
    kt = cfg.get("key_allele_table")
    if kt:
        story.append(Spacer(1, 4))
        P(kt.get("caption", "Table 1. Key actionable alleles."), "H2")
        story.append(make_table(kt["headers"], kt["rows"], st,
                                kt.get("colwidths", [70, 40, 130, 120, 100])))

    # ---- Conclusions ----
    story.append(Spacer(1, 8))
    P("Conclusions", "H1")
    for para in cfg.get("conclusions", []):
        P(para)
    if cfg.get("limitations"):
        story.append(Spacer(1, 4))
        story.append(callout("<b>Limitations.</b> " + cfg["limitations"], st))

    # ---- Next steps ----
    if cfg.get("next_steps"):
        P("Next Steps", "H1")
        for para in cfg["next_steps"]:
            P("&bull; " + para)

    # ---- References ----
    refs = cfg.get("references", [])
    if refs:
        story.append(PageBreak())
        P("References", "H1")
        for i, r in enumerate(refs, 1):
            num = r.get("n", i)
            txt = ascii_clean(r["text"])
            story.append(Paragraph(f"{num}. {txt}", st["Ref"]))

    doc.build(story, onFirstPage=page_chrome(title), onLaterPages=page_chrome(title))

    # ---- validation ----
    reader = PdfReader(out_pdf)
    npages = len(reader.pages)
    size = os.path.getsize(out_pdf)
    txt0 = reader.pages[0].extract_text() or ""
    assert npages >= 3, f"only {npages} pages"
    assert size > 5000, f"only {size} bytes"
    assert len(txt0.strip()) > 0, "no extractable text on page 1"
    print(f"[report] wrote {out_pdf}: {npages} pages, {size//1024} KB, text OK")
    return out_pdf


def main():
    ap = argparse.ArgumentParser(description="Build Phylo PDF for an allelic series.")
    ap.add_argument("--gene", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--config", required=True, help="report_config.json path")
    args = ap.parse_args()
    build(args.gene.upper(), args.outdir, args.config)


if __name__ == "__main__":
    sys.exit(main())
