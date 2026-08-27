#!/usr/bin/env python3
"""build_report.py -- generalized Phylo-branded PDF builder for the
methods-landscape-review skill.

This is a LAYOUT engine, not a content author. It reads:
  - synthesis.json   : ALL narrative text (agent-authored, source-bound). Required.
  - references.json   : ordered reference list [{n, text}] or ["text", ...]. Required.
  - fig_manifest.csv  : figures to embed (file, mode, caption). Optional.
  - infographic PNG   : conceptual overview from GenerateImage. Optional.
  - comparison_matrix.csv / performance_claims.json / benchmark_catalog.json
                        : comparison-mode tables. Optional.
  - theme_table.csv    : topic-mode table. Optional.
  - citation_verification.json : provenance status line. Optional but recommended.

It embeds ONLY what exists, so the same script serves comparison mode and topic
mode. Because every narrative string comes from synthesis.json (which the agent
writes AFTER the blocking citation-verify gate), the builder never invents facts.

Report structure (sections present only if provided):
  Title -> Infographic -> Executive Summary -> Methods -> Results (figures +
  tables + section prose) -> Discussion / Conclusions -> Limitations ->
  Next Steps -> References.

Usage:
  python3 build_report.py --run <dir> --out <path.pdf> [--infographic <png>]

synthesis.json schema (all fields optional except title):
{
  "title": "...", "subtitle": "...", "mode": "comparison" | "topic",
  "header_short": "short running header",
  "executive_summary": ["para", ...],
  "methods": ["para", ...],
  "results_intro": ["para", ...],
  "results_sections": [ {"heading": "...", "paragraphs": ["..."],
                          "figure": "fig_x.png", "figure_caption": "...",
                          "callout": {"title": "...", "body": "...", "accent": "gold"|"orange"}} ],
  "discussion": ["para", ...],
  "limitations": ["para", ...],
  "next_steps": ["bullet", ...],
  "callouts": [ {"where": "executive_summary"|"discussion", "title": "...",
                 "body": "...", "accent": "gold"|"orange"} ]
}
Rich text uses ReportLab XML tags (<b>, <i>, <super>, <sub>) -- NEVER markdown
or Unicode super/subscripts (they render as black boxes).
"""
import argparse
import csv
import datetime
import json
import os

from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, Image, HRFlowable, KeepTogether, ListFlowable,
                                ListItem)
from reportlab.lib.utils import ImageReader

# ---- Phylo palette (from pdf-report-generation skill) ----
PHYLO_GOLD = HexColor("#D4A04A"); HEADING = HexColor("#111111"); BODY = HexColor("#2C2A26")
MUTED = HexColor("#8A8378"); TH_BG = PHYLO_GOLD; TH_FG = HexColor("#FFFFFF")
ALT = HexColor("#F9F7F3"); BORDER = HexColor("#D5CFC5"); CALLOUT_BG = HexColor("#FAF9F3")
ORANGE = HexColor("#FF9400")
ACCENTS = {"gold": PHYLO_GOLD, "orange": ORANGE}

CONTENT_W = 492  # letter, 60pt margins

# ---- styles ----
S = getSampleStyleSheet()
def _add(n, **k):
    if n in S.byName:
        del S.byName[n]
    S.add(ParagraphStyle(name=n, **k))
_add("ReportTitle", fontName="Helvetica-Bold", fontSize=22, textColor=HEADING, leading=27, spaceAfter=6)
_add("Subtitle", fontName="Helvetica", fontSize=11, textColor=PHYLO_GOLD, spaceAfter=4)
_add("Attribution", fontName="Helvetica-Oblique", fontSize=9.5, textColor=MUTED, spaceAfter=8)
_add("SectionHead", fontName="Helvetica-Bold", fontSize=16, textColor=HEADING, spaceBefore=20, spaceAfter=9)
_add("SubHead", fontName="Helvetica-Bold", fontSize=12, textColor=HEADING, spaceBefore=11, spaceAfter=5)
_add("Body", fontName="Helvetica", fontSize=10, textColor=BODY, alignment=TA_JUSTIFY, spaceAfter=7, leading=14.5)
_add("BodyL", fontName="Helvetica", fontSize=10, textColor=BODY, alignment=TA_LEFT, spaceAfter=7, leading=14.5)
_add("Caption", fontName="Helvetica-Oblique", fontSize=8.5, textColor=MUTED, alignment=TA_CENTER, spaceAfter=13, leading=11)
_add("Cell", fontName="Helvetica", fontSize=8.2, textColor=BODY, leading=10.5)
_add("CellH", fontName="Helvetica-Bold", fontSize=8.4, textColor=TH_FG, leading=10.5)
_add("CalloutH", fontName="Helvetica-Bold", fontSize=10.5, textColor=HEADING, spaceAfter=4, leading=14)
_add("CalloutB", fontName="Helvetica", fontSize=9.3, textColor=BODY, leading=13, alignment=TA_LEFT)
_add("Ref", fontName="Helvetica", fontSize=8.3, textColor=BODY, leading=11.5, spaceAfter=3, alignment=TA_LEFT)
_add("Bullet", fontName="Helvetica", fontSize=10, textColor=BODY, leading=14.5)


def divider(w=CONTENT_W):
    return HRFlowable(width=w, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)


def callout(title, body, accent="gold", w=CONTENT_W):
    acc = ACCENTS.get(accent, PHYLO_GOLD)
    inner = [Paragraph(title, S["CalloutH"]), Paragraph(body, S["CalloutB"])]
    t = Table([[inner]], colWidths=[w])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER), ("LINEBEFORE", (0, 0), (0, -1), 3, acc),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 13), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    t.hAlign = "CENTER"
    return t


def mktable(header, rows, widths):
    data = [[Paragraph(h, S["CellH"]) for h in header]]
    for r in rows:
        data.append([Paragraph("" if c is None else str(c), S["Cell"]) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    sty = [("BACKGROUND", (0, 0), (-1, 0), TH_BG), ("TEXTCOLOR", (0, 0), (-1, 0), TH_FG),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER), ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for i in range(2, len(data), 2):
        sty.append(("BACKGROUND", (0, i), (-1, i), ALT))
    t.setStyle(TableStyle(sty))
    t.hAlign = "CENTER"
    return t


def fig_flowable(path, cap, max_w=468):
    """Embed an image scaled to max_w preserving aspect ratio, with caption."""
    ir = ImageReader(path)
    iw, ih = ir.getSize()
    w = min(max_w, iw)
    h = w * ih / iw
    img = Image(path, width=w, height=h)
    img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1, 3), Paragraph(cap, S["Caption"])])


def load_json(p, default=None):
    return json.load(open(p)) if os.path.exists(p) else default


def load_manifest(run):
    p = os.path.join(run, "fig_manifest.csv")
    out = {}
    if os.path.exists(p):
        with open(p) as fh:
            for row in csv.DictReader(fh):
                out[row["file"]] = row
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="dir with artifacts")
    ap.add_argument("--out", required=True, help="output PDF path (write to /mnt/results/...)")
    ap.add_argument("--infographic", default=None, help="optional infographic PNG")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to today")
    a = ap.parse_args()
    run = a.run
    rd = lambda f: os.path.join(run, f)

    syn = load_json(rd("synthesis.json"))
    if not syn:
        raise SystemExit("synthesis.json is required (agent-authored narrative). "
                         "Write it AFTER the citation-verify gate passes.")
    refs = load_json(rd("references.json"), [])
    manifest = load_manifest(run)
    verif = load_json(rd("citation_verification.json"), {})
    mode = syn.get("mode", "comparison")

    title = syn.get("title", "Comparative Methods Landscape")
    subtitle = syn.get("subtitle", "")
    header_short = syn.get("header_short", title[:60])
    date_str = (datetime.date.fromisoformat(a.date) if a.date
                else datetime.date.today()).strftime("%B %d, %Y")

    def paras(key, style="Body"):
        return [Paragraph(t, S[style]) for t in syn.get(key, []) if str(t).strip()]

    def inline_callouts(where):
        out = []
        for c in syn.get("callouts", []):
            if c.get("where") == where:
                out.append(callout(c.get("title", ""), c.get("body", ""),
                                   c.get("accent", "gold")))
        return out

    story = []
    # ===== TITLE =====
    story += [Spacer(1, 26), Paragraph(title, S["ReportTitle"])]
    if subtitle:
        story.append(Paragraph(subtitle, S["Subtitle"]))
    story += [Spacer(1, 6),
              Paragraph(f"<i>Generated by Biomni &nbsp;|&nbsp; {date_str}</i>", S["Attribution"]),
              divider(), Spacer(1, 4)]

    # ===== INFOGRAPHIC (optional conceptual overview) =====
    info = a.infographic or (rd(syn["infographic"]) if syn.get("infographic") else None)
    if info and os.path.exists(info):
        story.append(fig_flowable(info, syn.get("infographic_caption",
                     "Visual overview of the decision landscape (schematic; see text for sourced detail).")))

    # ===== EXECUTIVE SUMMARY =====
    if syn.get("executive_summary"):
        story.append(Paragraph("Executive Summary", S["SectionHead"]))
        story += paras("executive_summary")
        story += inline_callouts("executive_summary")

    # ===== METHODS =====
    if syn.get("methods"):
        story.append(Paragraph("Methods", S["SectionHead"]))
        story += paras("methods", "BodyL")
        if verif.get("doi_layer_status"):
            story.append(Paragraph(
                f"<b>Provenance.</b> All cited references and figure-critical numbers were "
                f"verified against retrieved records and the working transcript before inclusion "
                f"(citation-integrity status: <b>{verif['doi_layer_status']}</b>).", S["BodyL"]))

    # ===== RESULTS =====
    story.append(Paragraph("Results", S["SectionHead"]))
    story += paras("results_intro")

    used_figs = set()
    for sec in syn.get("results_sections", []):
        blk = []
        if sec.get("heading"):
            story.append(Paragraph(sec["heading"], S["SubHead"]))
        for t in sec.get("paragraphs", []):
            story.append(Paragraph(t, S["Body"]))
        f = sec.get("figure")
        if f and os.path.exists(rd(f)):
            cap = sec.get("figure_caption") or manifest.get(f, {}).get("caption", "")
            story.append(fig_flowable(rd(f), cap))
            used_figs.add(f)
        if sec.get("callout"):
            co = sec["callout"]
            story.append(callout(co.get("title", ""), co.get("body", ""), co.get("accent", "gold")))

    # ---- comparison-mode tables ----
    if mode == "comparison":
        import pandas as pd
        if os.path.exists(rd("comparison_matrix.csv")):
            cm = pd.read_csv(rd("comparison_matrix.csv"))
            cols = list(cm.columns)
            n = len(cols)
            w0 = 96
            wr = (CONTENT_W - w0) / max(1, n - 1)
            widths = [w0] + [wr] * (n - 1)
            rows = [[r[c] for c in cols] for _, r in cm.iterrows()]
            story.append(Paragraph("Method characteristics", S["SubHead"]))
            story.append(mktable(cols, rows, widths))
            story.append(Paragraph("Table. Structural / algorithmic comparison (from method papers).",
                                   S["Caption"]))
        bc = load_json(rd("benchmark_catalog.json"))
        if bc:
            story.append(Paragraph("Benchmark catalog", S["SubHead"]))
            brows = [[b.get("benchmark_name", ""), b.get("truth_basis", ""),
                      b.get("key_metric", "")] for b in bc]
            story.append(mktable(["Benchmark (design)", "Truth basis", "Headline result"],
                                 brows, [140, 176, 176]))
            story.append(Paragraph("Table. Benchmarks and their source-bound headline results.",
                                   S["Caption"]))
        pc = load_json(rd("performance_claims.json"))
        if pc:
            story.append(Paragraph("Curated performance claims", S["SubHead"]))
            crows = [[c.get("method", ""), c.get("dimension", ""), c.get("finding", ""),
                      c.get("evidence_thickness", "")] for c in pc]
            story.append(mktable(["Method", "Dimension", "Finding", "Evidence"],
                                 crows, [80, 88, 218, 106]))
            story.append(Paragraph("Table. Curated, source-bound performance claims.", S["Caption"]))

    # ---- topic-mode table ----
    if mode == "topic" and os.path.exists(rd("theme_table.csv")):
        import pandas as pd
        tt = pd.read_csv(rd("theme_table.csv"))
        cols = list(tt.columns)
        n = len(cols)
        widths = [CONTENT_W / n] * n
        rows = [[r[c] for c in cols] for _, r in tt.iterrows()]
        story.append(Paragraph("Themes", S["SubHead"]))
        story.append(mktable(cols, rows, widths))
        story.append(Paragraph("Table. Synthesized themes across the corpus.", S["Caption"]))

    # ---- any figures from the manifest not already placed inline ----
    extra = [f for f in manifest if f not in used_figs and os.path.exists(rd(f))]
    if extra:
        story.append(Paragraph("Additional figures", S["SubHead"]))
        for f in extra:
            story.append(fig_flowable(rd(f), manifest[f].get("caption", "")))

    # ===== DISCUSSION / CONCLUSIONS =====
    if syn.get("discussion"):
        story.append(Paragraph("Discussion &amp; Conclusions", S["SectionHead"]))
        story += paras("discussion")
        story += inline_callouts("discussion")

    # ===== LIMITATIONS =====
    if syn.get("limitations"):
        story.append(Paragraph("Limitations", S["SubHead"]))
        story += paras("limitations")

    # ===== NEXT STEPS =====
    if syn.get("next_steps"):
        story.append(Paragraph("Next Steps", S["SectionHead"]))
        items = [ListItem(Paragraph(t, S["Bullet"]))
                 for t in syn["next_steps"] if str(t).strip()]
        story.append(ListFlowable(items, bulletType="bullet", start="\u2022",
                                  bulletColor=PHYLO_GOLD, bulletFontSize=8,
                                  leftIndent=16, bulletOffsetY=1))

    # ===== REFERENCES =====
    if refs:
        story.append(Paragraph("Key References", S["SectionHead"]))
        for i, r in enumerate(refs, 1):
            if isinstance(r, dict):
                num = r.get("n", i)
                txt = r.get("text", "")
            else:
                num, txt = i, str(r)
            story.append(Paragraph(f"<b>[{num}]</b> {txt}", S["Ref"]))

    # ===== page chrome =====
    def hdr_ftr(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
        canvas.drawString(60, h - 40, header_short)
        canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60, h - 48, w - 60, h - 48)
        canvas.setStrokeColor(BORDER); canvas.setLineWidth(0.75); canvas.line(60, 40, w - 60, 40)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
        canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
        canvas.restoreState()

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    doc = SimpleDocTemplate(a.out, pagesize=letter, topMargin=56, bottomMargin=52,
                            leftMargin=60, rightMargin=60, title=title)
    doc.build(story, onFirstPage=hdr_ftr, onLaterPages=hdr_ftr)

    # ===== validation =====
    from pypdf import PdfReader
    reader = PdfReader(a.out)
    npages = len(reader.pages)
    size = os.path.getsize(a.out)
    print(f"PDF written: {a.out}")
    print(f"  pages: {npages}  size: {size} bytes")
    assert npages >= 2, f"only {npages} page(s) -- likely missing content"
    assert size > 5000, f"only {size} bytes -- likely blank/corrupt"
    blank = [i + 1 for i, pg in enumerate(reader.pages)
             if len((pg.extract_text() or "").strip()) < 5]
    if blank:
        print(f"  WARNING: near-blank pages: {blank}")
    else:
        print("  all pages have extractable text")
    print("  -> run a media_output_check on the PDF before delivering.")


if __name__ == "__main__":
    main()
