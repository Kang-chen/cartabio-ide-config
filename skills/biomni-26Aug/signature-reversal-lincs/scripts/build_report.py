#!/usr/bin/env python3
"""
build_report.py — branded PDF report for a LINCS signature-reversal repurposing analysis.

Disease-agnostic. Reads a facts JSON (+ the robustness summary + a figures dir) and produces a
Phylo-branded multi-section PDF: title, infographic band, Introduction, Methods, Results (with
ranked-hit table + figures), Positive-control validation, Limitations, Conclusions & Next steps,
References. All numbers/text come from the JSON — nothing hardcoded per disease.

This is a REFERENCE builder for the `signature-reversal-lincs` skill. For a polished report you may
also load the `pdf-report-generation` skill and follow its brand/QA guidance; this script already
implements those conventions (Phylo palette, <sub>/<super> tags, hAlign=CENTER, KeepTogether,
write-direct-to-/mnt/results). Always validate the output (pypdf + a visual media check).

Usage
-----
  python build_report.py --facts facts.json --robustness robustness_summary.json \
      --figures /mnt/results/figures --out /mnt/results/report_<disease>.pdf

facts.json schema (all optional except title/disease; missing keys are skipped gracefully)
------------------------------------------------------------------------------------------
{
  "title": "In-Silico Drug Repurposing for <Disease>",
  "disease": "<Disease>",
  "author": "Biomni / Phylo",
  "summary": "one-paragraph executive summary",
  "introduction": "background paragraph(s)",
  "methods": "methods paragraph(s)",
  "results_text": "results narrative",
  "limitations": ["bullet", "bullet"],
  "conclusions": "closing paragraph",
  "next_steps": ["bullet", "bullet"],
  "infographic": [{"value": "281", "label": "reproducible reversers"}, ...],   # 3-5 stat tiles
  "top_hits": [{"rank":1,"compound":"MG-132","n_sigs":46,"n_cells":15,
                "median_z":-8.68,"moa":"proteasome inhibitor"}, ...],
  "positive_controls": [{"drug":"tofacitinib","status":"Tier-1","detail":"rank 22, n_sig=3"}, ...],
  "figures": [{"file":"fig1_signature_overview.png","caption":"..."}, ...],
  "references": [{"n":1,"text":"Author et al. Journal Year. DOI/URL"}, ...]
}
"""
import argparse, json, os, datetime, math
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, Image, HRFlowable, KeepTogether)
from reportlab.lib.utils import ImageReader

# ---------------- Phylo brand ----------------
PHYLO_GOLD    = HexColor("#D4A04A")
HEADING_COLOR = HexColor("#111111")
BODY_TEXT     = HexColor("#2C2A26")
MUTED_TEXT    = HexColor("#8A8378")
TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F9F7F3")
TABLE_BORDER  = HexColor("#D5CFC5")
CALLOUT_BG    = HexColor("#FAF9F3")


def _safe_str(val, default="\u2014"):
    """Coerce nullable / NaN / inf DataFrame values to a ReportLab-safe string.

    ReportLab's Paragraph() raises on float('nan'), float('inf'), and None when
    they appear inside table cells or reference text derived from pandas DataFrames.
    This helper normalises those to a safe placeholder before rendering.
    """
    if val is None:
        return default
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return default
        # avoid trailing ".0" on whole-number floats from CSV round-trips
        if val.is_integer():
            return str(int(val))
        return str(val)
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null", "na", ""):
        return default
    return s


def make_styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="RTitle", fontName="Helvetica-Bold", fontSize=23,
        textColor=HEADING_COLOR, spaceAfter=6, leading=27))
    s.add(ParagraphStyle(name="RSub", fontName="Helvetica", fontSize=12,
        textColor=PHYLO_GOLD, spaceAfter=4, leading=15))
    s.add(ParagraphStyle(name="RAttr", fontName="Helvetica-Oblique", fontSize=9.5,
        textColor=MUTED_TEXT, spaceAfter=8))
    s.add(ParagraphStyle(name="Head", fontName="Helvetica-Bold", fontSize=15,
        textColor=HEADING_COLOR, spaceBefore=18, spaceAfter=8, leading=18))
    s.add(ParagraphStyle(name="BodyJ", fontName="Helvetica", fontSize=10, textColor=BODY_TEXT,
        alignment=TA_JUSTIFY, spaceAfter=7, leading=14.5))
    s.add(ParagraphStyle(name="Cap", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=MUTED_TEXT, alignment=TA_CENTER, spaceAfter=14, leading=11))
    s.add(ParagraphStyle(name="CellL", fontName="Helvetica", fontSize=8.5, textColor=BODY_TEXT, leading=11))
    s.add(ParagraphStyle(name="CellHdr", fontName="Helvetica-Bold", fontSize=8.5,
        textColor=TABLE_HEADER_FG, leading=11))
    s.add(ParagraphStyle(name="BulletX", fontName="Helvetica", fontSize=10, textColor=BODY_TEXT,
        alignment=TA_LEFT, spaceAfter=4, leading=14, leftIndent=14, bulletIndent=2))
    s.add(ParagraphStyle(name="StatVal", fontName="Helvetica-Bold", fontSize=19,
        textColor=PHYLO_GOLD, alignment=TA_CENTER, leading=21))
    s.add(ParagraphStyle(name="StatLbl", fontName="Helvetica", fontSize=8,
        textColor=BODY_TEXT, alignment=TA_CENTER, leading=10))
    return s


def divider():
    return HRFlowable(width=480, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=2)


def infographic_band(tiles, s):
    """Row of 3-5 stat tiles."""
    if not tiles:
        return Spacer(1, 2)
    cells = [[Paragraph(_safe_str(t.get("value","")), s["StatVal"]),
              Paragraph(_safe_str(t.get("label","")), s["StatLbl"])] for t in tiles]
    # transpose into a single row of stacked (value/label) mini-tables
    minis = []
    for c in cells:
        minis.append(Table([[c[0]],[c[1]]], colWidths=[110]))
    row = Table([minis], colWidths=[112]*len(minis))
    row.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), CALLOUT_BG),
        ("BOX",(0,0),(-1,-1), 1, PHYLO_GOLD),
        ("INNERGRID",(0,0),(-1,-1), 0.5, TABLE_BORDER),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
    ]))
    row.hAlign = "CENTER"
    return row


def hit_table(hits, s):
    if not hits:
        return Spacer(1, 2)
    header = ["Rank","Compound","# sigs","# cells","median z-sum","MoA / target"]
    rows = [[Paragraph(h, s["CellHdr"]) for h in header]]
    for h in hits:
        rows.append([
            Paragraph(_safe_str(h.get("rank","")), s["CellL"]),
            Paragraph(_safe_str(h.get("compound","")), s["CellL"]),
            Paragraph(_safe_str(h.get("n_sigs","")), s["CellL"]),
            Paragraph(_safe_str(h.get("n_cells","")), s["CellL"]),
            Paragraph(_safe_str(h.get("median_z","")), s["CellL"]),
            Paragraph(_safe_str(h.get("moa")), s["CellL"]),
        ])
    t = Table(rows, colWidths=[34, 118, 42, 42, 70, 170], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), PHYLO_GOLD),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [HexColor("#FFFFFF"), TABLE_ALT_ROW]),
        ("GRID",(0,0),(-1,-1), 0.5, TABLE_BORDER),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ("LEFTPADDING",(0,0),(-1,-1),5),
    ]))
    t.hAlign = "CENTER"
    return t


def pc_table(pcs, s):
    if not pcs:
        return Spacer(1, 2)
    header = ["Known drug","Recovery","Detail"]
    rows = [[Paragraph(h, s["CellHdr"]) for h in header]]
    for p in pcs:
        rows.append([Paragraph(_safe_str(p.get("drug","")), s["CellL"]),
                     Paragraph(_safe_str(p.get("status","")), s["CellL"]),
                     Paragraph(_safe_str(p.get("detail","")), s["CellL"])])
    t = Table(rows, colWidths=[130, 80, 266], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), PHYLO_GOLD),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [HexColor("#FFFFFF"), TABLE_ALT_ROW]),
        ("GRID",(0,0),(-1,-1), 0.5, TABLE_BORDER),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ("LEFTPADDING",(0,0),(-1,-1),5),
    ]))
    t.hAlign = "CENTER"
    return t


def scaled_image(path, max_w=470):
    ir = ImageReader(path)
    iw, ih = ir.getSize()
    w = min(max_w, iw)
    h = w * ih / iw
    img = Image(path, width=w, height=h)
    img.hAlign = "CENTER"
    return img


def para_block(text, s):
    """Split a text field (str or list) into Paragraphs."""
    out = []
    if isinstance(text, list):
        for t in text:
            out.append(Paragraph(f"\u2022 {_safe_str(t)}", s["BulletX"]))
    elif isinstance(text, str) and text.strip():
        for chunk in text.split("\n\n"):
            if chunk.strip():
                out.append(Paragraph(chunk.strip(), s["BodyJ"]))
    return out


def build(facts, rob, figdir, out):
    s = make_styles()
    story = []
    date_str = datetime.date.today().strftime("%B %d, %Y")

    # ---- Title ----
    story.append(Paragraph(facts.get("title", "Drug Repurposing Report"), s["RTitle"]))
    story.append(Paragraph("LINCS L1000 connectivity mapping / signature reversal", s["RSub"]))
    story.append(Paragraph(f'{facts.get("author","Biomni / Phylo")} \u2014 {date_str}', s["RAttr"]))
    story.append(divider())

    # ---- Infographic band ----
    story.append(infographic_band(facts.get("infographic", []), s))
    story.append(Spacer(1, 10))

    # ---- Executive summary ----
    if facts.get("summary"):
        story.append(Paragraph("Executive summary", s["Head"]))
        story += para_block(facts["summary"], s)

    # ---- Introduction ----
    if facts.get("introduction"):
        story.append(Paragraph("Introduction", s["Head"]))
        story += para_block(facts["introduction"], s)

    # ---- Methods ----
    if facts.get("methods"):
        story.append(Paragraph("Methods", s["Head"]))
        story += para_block(facts["methods"], s)

    # ---- Results ----
    story.append(Paragraph("Results", s["Head"]))
    story += para_block(facts.get("results_text", ""), s)
    if facts.get("top_hits"):
        story.append(Spacer(1, 4))
        story.append(KeepTogether([hit_table(facts["top_hits"], s),
                                   Spacer(1,3),
                                   Paragraph("Table 1. Top reproducible (Tier-1) reversing "
                                             "compounds, ranked by composite score.", s["Cap"])]))

    # ---- Figures ----
    for fig in facts.get("figures", []):
        path = os.path.join(figdir, fig["file"]) if not os.path.isabs(fig["file"]) else fig["file"]
        if os.path.exists(path):
            story.append(KeepTogether([scaled_image(path),
                                       Paragraph(fig.get("caption",""), s["Cap"])]))

    # ---- Positive-control validation ----
    if facts.get("positive_controls"):
        story.append(Paragraph("Positive-control validation", s["Head"]))
        story.append(Paragraph(
            "Recovery of drugs already used clinically for this disease \u2014 without providing "
            "them as input \u2014 is the strongest internal evidence the connectivity map is "
            "meaningful. Each drug is classified by its <b>actual</b> membership in the Tier-1 or "
            "Tier-2 tables; drugs absent from both are reported honestly as not recovered.", s["BodyJ"]))
        story.append(KeepTogether([pc_table(facts["positive_controls"], s),
                                   Spacer(1,3),
                                   Paragraph("Table 2. Known-drug recovery classification.", s["Cap"])]))

    # ---- Limitations ----
    if facts.get("limitations"):
        story.append(Paragraph("Limitations", s["Head"]))
        story += para_block(facts["limitations"], s)

    # ---- Conclusions & next steps ----
    if facts.get("conclusions") or facts.get("next_steps"):
        story.append(Paragraph("Conclusions & next steps", s["Head"]))
        story += para_block(facts.get("conclusions",""), s)
        story += para_block(facts.get("next_steps", []), s)

    # ---- References ----
    if facts.get("references"):
        story.append(Paragraph("References", s["Head"]))
        for r in facts["references"]:
            story.append(Paragraph(f'[{_safe_str(r.get("n",""))}] {_safe_str(r.get("text",""))}', s["CellL"]))
            story.append(Spacer(1, 2))

    doc = SimpleDocTemplate(out, pagesize=letter,
                            leftMargin=0.9*inch, rightMargin=0.9*inch,
                            topMargin=0.8*inch, bottomMargin=0.7*inch,
                            title=facts.get("title",""))
    doc.build(story)
    print(f"[report] wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", required=True)
    ap.add_argument("--robustness", required=False)
    ap.add_argument("--figures", default="/mnt/results/figures")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    facts = json.load(open(a.facts))
    rob = json.load(open(a.robustness)) if a.robustness and os.path.exists(a.robustness) else {}
    # write PDF directly to /mnt/results (compatible format)
    build(facts, rob, a.figures, a.out)


if __name__ == "__main__":
    main()
