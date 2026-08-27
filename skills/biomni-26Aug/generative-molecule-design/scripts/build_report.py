"""
Phylo-branded PDF report for the generative small-molecule design pipeline.

Follows the `pdf-report-generation` system skill conventions (brand palette,
Helvetica, US-Letter, header/footer, centered figures/tables, KeepTogether,
<sub>/<super> not Unicode, pypdf + media_output_check validation). Adds, per the
skill spec:
  * an INFOGRAPHIC summary panel (pipeline funnel + headline metric cards),
  * sections: Introduction, Methods, Results, Conclusions, Figures, References,
    Next Steps,
  * graceful handling when retrosynthesis was skipped (report says so),
  * a References section populated from LiteratureSearch results passed in by the
    caller (see build_report(..., references=[...])).

The builder is data-driven: pass a `bundle` dict (see build_report docstring). It
never hard-codes a target -- DRD2 is just one possible input.
"""
from __future__ import annotations
import os
from typing import Dict, List, Optional

import pandas as pd
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (HRFlowable, Image, KeepTogether, PageBreak,
                                Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

# ---- Phylo brand (from pdf-report-generation skill) ----
PHYLO_GOLD = HexColor("#D4A04A")
HEADING = HexColor("#111111")
BODY = HexColor("#2C2A26")
MUTED = HexColor("#8A8378")
TB_HDR_FG = HexColor("#FFFFFF")
TB_ALT = HexColor("#F9F7F3")
TB_BORDER = HexColor("#D5CFC5")
CALLOUT_BG = HexColor("#FAF9F3")
GREEN = HexColor("#75A025")
CONTENT_W = 492  # letter - 60pt margins each side


def _styles():
    st = getSampleStyleSheet()

    def add(n, **k):
        st.add(ParagraphStyle(name=n, **k))

    add("RTitle", fontName="Helvetica-Bold", fontSize=23, textColor=HEADING, leading=28, spaceAfter=6)
    add("Sub", fontName="Helvetica", fontSize=11, textColor=PHYLO_GOLD, spaceAfter=4)
    add("Attr", fontName="Helvetica-Oblique", fontSize=9.5, textColor=MUTED, spaceAfter=8)
    add("H1", fontName="Helvetica-Bold", fontSize=15, textColor=HEADING, spaceBefore=16, spaceAfter=8)
    add("H2", fontName="Helvetica-Bold", fontSize=11.5, textColor=HEADING, spaceBefore=9, spaceAfter=4)
    add("Body2", fontName="Helvetica", fontSize=10, textColor=BODY, alignment=TA_JUSTIFY, spaceAfter=6, leading=14)
    add("Cap", fontName="Helvetica-Oblique", fontSize=8.6, textColor=MUTED, alignment=TA_CENTER, spaceAfter=12)
    add("CalloutT", fontName="Helvetica", fontSize=9.6, textColor=BODY, leading=13.5)
    add("Metric", fontName="Helvetica-Bold", fontSize=17, textColor=PHYLO_GOLD, alignment=TA_CENTER, leading=19)
    add("MetricL", fontName="Helvetica", fontSize=7.6, textColor=MUTED, alignment=TA_CENTER, leading=9)
    add("Ref", fontName="Helvetica", fontSize=8.8, textColor=BODY, leading=12, spaceAfter=4)
    return st


def _divider():
    return HRFlowable(width=CONTENT_W, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)


def _callout(txt, st, accent=PHYLO_GOLD):
    t = Table([[Paragraph(txt, st["CalloutT"])]], colWidths=[CONTENT_W - 18])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, TB_BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 13), ("RIGHTPADDING", (0, 0), (-1, -1), 11),
    ]))
    t.hAlign = "CENTER"
    return t


def _metric_cards(st, cards: List[tuple]):
    """Infographic: a row of headline metric cards (value + label)."""
    cells = [[Paragraph(v, st["Metric"]), ] for v, _ in cards]
    row_vals = [Paragraph(v, st["Metric"]) for v, _ in cards]
    row_labs = [Paragraph(l, st["MetricL"]) for _, l in cards]
    n = len(cards)
    cw = [CONTENT_W / n] * n
    t = Table([row_vals, row_labs], colWidths=cw)
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, TB_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, TB_BORDER),
        ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    t.setStyle(TableStyle(style))
    t.hAlign = "CENTER"
    return t


def _funnel(st, stages: List[tuple]):
    """Infographic: pipeline funnel as a shaded table (stage -> count)."""
    head = [Paragraph("<b>Pipeline stage</b>", st["MetricL"]),
            Paragraph("<b>Molecules</b>", st["MetricL"])]
    rows = [head]
    for name, cnt in stages:
        rows.append([Paragraph(name, st["Ref"]), Paragraph(str(cnt), st["Ref"])])
    t = Table(rows, colWidths=[CONTENT_W * 0.72, CONTENT_W * 0.28])
    sstyle = [
        ("BACKGROUND", (0, 0), (-1, 0), PHYLO_GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), TB_HDR_FG),
        ("GRID", (0, 0), (-1, -1), 0.5, TB_BORDER),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            sstyle.append(("BACKGROUND", (0, i), (-1, i), TB_ALT))
    t.setStyle(TableStyle(sstyle))
    t.hAlign = "CENTER"
    return t


def _fig(path, st, caption, width=None, max_h=430):
    """Centered image + caption bound together (KeepTogether)."""
    from PIL import Image as PILImage
    if not os.path.exists(path):
        return Paragraph(f"<i>[figure missing: {os.path.basename(path)}]</i>", st["Cap"])
    iw, ih = PILImage.open(path).size
    w = width or min(CONTENT_W, iw * 0.75)
    h = w * ih / iw
    if h > max_h:
        h = max_h
        w = h * iw / ih
    img = Image(path, width=w, height=h)
    img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1, 3), Paragraph(caption, st["Cap"])])


def _table_from_df(df: pd.DataFrame, st, cols: List[str], colwidths=None,
                   headers=None, max_rows=12):
    headers = headers or cols
    cellC = ParagraphStyle("cC", fontName="Helvetica", fontSize=8, textColor=BODY,
                           alignment=TA_CENTER, leading=10)
    hdrC = ParagraphStyle("hC", fontName="Helvetica-Bold", fontSize=8,
                          textColor=TB_HDR_FG, alignment=TA_CENTER, leading=10)
    data = [[Paragraph(str(h), hdrC) for h in headers]]
    for _, r in df.head(max_rows).iterrows():
        row = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                v = f"{v:.3f}" if abs(v) < 100 else f"{v:.1f}"
            row.append(Paragraph(str(v), cellC))
        data.append(row)
    t = Table(data, colWidths=colwidths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), PHYLO_GOLD),
        ("GRID", (0, 0), (-1, -1), 0.5, TB_BORDER),
        ("BOX", (0, 0), (-1, -1), 0.75, TB_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TB_ALT))
    t.setStyle(TableStyle(style))
    t.hAlign = "CENTER"
    return t


def _page_chrome(title):
    def cb(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(MUTED)
        canvas.drawString(60, h - 40, title[:90])
        canvas.setStrokeColor(PHYLO_GOLD)
        canvas.setLineWidth(1)
        canvas.line(60, h - 48, w - 60, h - 48)
        canvas.setStrokeColor(TB_BORDER)
        canvas.setLineWidth(0.75)
        canvas.line(60, 40, w - 60, 40)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
        canvas.restoreState()
    return cb


def build_report(bundle: Dict, out_pdf: str) -> str:
    """Render the PDF. `bundle` keys:

      target        : str, e.g. 'DRD2'
      target_desc   : str, one-sentence target biology (for Introduction)
      date          : str
      objective_desc: str, human description of the scoring objective/preset
      activity_backend: str, e.g. 'TDC DRD2 oracle (SVM on ECFP)'
      ga_params     : dict (pop_size, n_generations, ...)
      summary       : dict of headline numbers (n_generated, best/mean fitness,
                      cascade counts, n_top, n_solved, ranges, medians)
      cascade       : list[(stage_name, count)]
      top_df        : DataFrame of top designs (with retro columns if available)
      figures       : dict of fig-name -> path (convergence, activity_qed,
                      property_dist, novelty, top_grid, seed_grid, routes:list)
      retro_ran     : bool
      retro_skip_reason : str (if not retro_ran)
      references    : list[{'index':int,'text':str}] from LiteratureSearch
      next_steps    : list[str]
      caveats       : list[str]
    """
    st = _styles()
    S = bundle.get("summary", {})
    target = bundle.get("target", "target")
    figs = bundle.get("figures", {})
    story = []

    # ---------------- Title ----------------
    story.append(Spacer(1, 24))
    story.append(Paragraph(f"De Novo Small-Molecule Design for {target}", st["RTitle"]))
    story.append(Paragraph("Goal-directed generative chemistry with multi-objective "
                           "scoring, synthesizability gating, and retrosynthesis", st["Sub"]))
    story.append(Paragraph(f"<i>Generated by Biomni  |  {bundle.get('date','')}</i>", st["Attr"]))
    story.append(_divider())

    # ---------------- Infographic summary ----------------
    story.append(Paragraph("At a Glance", st["H1"]))
    cards = [
        (str(S.get("n_generated", "-")), "molecules<br/>generated"),
        (str(S.get("n_top", "-")), "top designs<br/>selected"),
        (f"{S.get('best_fit_final', 0):.2f}", "best composite<br/>fitness"),
    ]
    if bundle.get("retro_ran", False):
        cards.append((f"{S.get('n_solved','-')}/{S.get('n_top','-')}", "routes fully<br/>solved"))
    else:
        cards.append(("SA-proxy", "synthesizability<br/>(retro skipped)"))
    story.append(_metric_cards(st, cards))
    story.append(Spacer(1, 10))
    if bundle.get("cascade"):
        story.append(_funnel(st, bundle["cascade"]))
        story.append(Paragraph("Funnel: molecules surviving each successive filter, "
                               "from the full generated library to the final selection.",
                               st["Cap"]))

    # ---------------- Executive summary ----------------
    story.append(Paragraph("Executive Summary", st["H1"]))
    story.append(Paragraph(bundle.get("exec_summary", ""), st["Body2"]))

    story.append(PageBreak())

    # ---------------- 1. Introduction ----------------
    story.append(Paragraph("1. Introduction", st["H1"]))
    story.append(Paragraph(bundle.get("intro", ""), st["Body2"]))
    if "seed_grid" in figs:
        story.append(_fig(figs["seed_grid"], st,
                          f"Figure 1. Known {target} ligands used to seed the generator, "
                          "annotated with activity, QED, and synthetic-accessibility scores."))

    # ---------------- 2. Methods ----------------
    story.append(Paragraph("2. Methods", st["H1"]))
    story.append(Paragraph("2.1 Generation", st["H2"]))
    story.append(Paragraph(bundle.get("methods_ga", ""), st["Body2"]))
    story.append(Paragraph("2.2 Multi-objective scoring", st["H2"]))
    story.append(Paragraph(bundle.get("methods_scoring", ""), st["Body2"]))
    story.append(Paragraph("2.3 Novelty & selection", st["H2"]))
    story.append(Paragraph(bundle.get("methods_selection", ""), st["Body2"]))
    story.append(Paragraph("2.4 Retrosynthesis", st["H2"]))
    story.append(Paragraph(bundle.get("methods_retro", ""), st["Body2"]))

    # ---------------- 3. Results ----------------
    story.append(PageBreak())
    story.append(Paragraph("3. Results", st["H1"]))
    story.append(Paragraph("3.1 Convergence", st["H2"]))
    story.append(Paragraph(bundle.get("results_convergence", ""), st["Body2"]))
    if "convergence" in figs:
        story.append(_fig(figs["convergence"], st,
                          "Figure 2. GA convergence: best/mean composite fitness and "
                          "cumulative unique molecules per generation."))
    if "activity_qed" in figs:
        story.append(_fig(figs["activity_qed"], st,
                          "Figure 3. Generated library in activity-QED space; selected "
                          "top designs circled."))

    story.append(Paragraph("3.2 Property landscape", st["H2"]))
    story.append(Paragraph(bundle.get("results_properties", ""), st["Body2"]))
    if "property_dist" in figs:
        story.append(_fig(figs["property_dist"], st,
                          "Figure 4. Distributions of key physicochemical and scoring "
                          "properties; dashed guides mark common drug-like thresholds."))

    story.append(Paragraph("3.3 Top designs", st["H2"]))
    story.append(Paragraph(bundle.get("results_top", ""), st["Body2"]))
    top_df = bundle.get("top_df")
    if top_df is not None and len(top_df):
        cols = [c for c in ["design_id", "activity", "QED", "combined", "SA_Score",
                            "MW", "LogP", "nn_known_tanimoto"] if c in top_df.columns]
        story.append(_table_from_df(top_df, st, cols,
                     headers=["ID", "Act", "QED", "Fit", "SA", "MW", "LogP", "NN-Tan"],
                     colwidths=[CONTENT_W * w for w in
                                [0.14, 0.11, 0.11, 0.11, 0.10, 0.13, 0.12, 0.18][:len(cols)]]))
        story.append(Paragraph("Table 1. Top designs ranked by composite fitness. "
                               "Act = activity score; Fit = composite; SA = synthetic "
                               "accessibility (lower is easier); NN-Tan = max Tanimoto to "
                               "any known active (lower is more novel).", st["Cap"]))
    if "top_grid" in figs:
        story.append(_fig(figs["top_grid"], st,
                          "Figure 5. Structures of the top designs with activity, QED, "
                          "and SA_Score annotations."))
    if "novelty" in figs:
        story.append(_fig(figs["novelty"], st,
                          "Figure 6. Novelty of selected designs (max Tanimoto to known "
                          "actives) versus the novelty threshold."))

    # 3.4 routes (only if retro ran)
    story.append(Paragraph("3.4 Retrosynthetic routes", st["H2"]))
    if bundle.get("retro_ran", False):
        story.append(Paragraph(bundle.get("results_routes", ""), st["Body2"]))
        for i, rp in enumerate(figs.get("routes", []), start=7):
            story.append(_fig(rp, st, f"Figure {i}. Predicted best retrosynthetic route "
                              "(leaves are commercially available building blocks)."))
    else:
        story.append(_callout(
            "<b>Retrosynthesis was not run for this report.</b> " +
            bundle.get("retro_skip_reason", "Models/egress unavailable.") +
            " Synthesizability was instead assessed with the SA_Score proxy "
            "(Tier-1), which is reported in Table 1 and Figure 4.", st, accent=HexColor("#FF9400")))

    # ---------------- 4. Conclusions ----------------
    story.append(PageBreak())
    story.append(Paragraph("4. Conclusions", st["H1"]))
    story.append(Paragraph(bundle.get("conclusions", ""), st["Body2"]))

    # ---------------- 5. Limitations ----------------
    story.append(Paragraph("5. Limitations", st["H1"]))
    for c in bundle.get("caveats", []):
        story.append(Paragraph(f"&bull; {c}", st["Body2"]))

    # ---------------- 6. Next steps ----------------
    story.append(Paragraph("6. Next Steps", st["H1"]))
    for s in bundle.get("next_steps", []):
        story.append(Paragraph(f"&bull; {s}", st["Body2"]))

    # ---------------- 7. References ----------------
    refs = bundle.get("references", [])
    if refs:
        story.append(Paragraph("7. References", st["H1"]))
        for r in refs:
            story.append(Paragraph(f"[{r['index']}] {r['text']}", st["Ref"]))

    doc = SimpleDocTemplate(out_pdf, pagesize=letter, topMargin=56, bottomMargin=52,
                            leftMargin=60, rightMargin=60)
    chrome = _page_chrome(f"De Novo Design for {target}")
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return out_pdf
