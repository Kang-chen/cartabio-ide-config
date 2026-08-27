"""
============================================================================
GENERATE REPORT  —  cell-therapy scRNA-seq QC release scorecard
============================================================================

Assemble the Phylo-branded PDF: an INFOGRAPHIC SUMMARY page first (one card per
unit with its overall call + a 5-module status strip), then Introduction,
Methods, Results (figures + tables), Scorecard, Discussion, Limitations, Next
Steps, and References. Follows the `pdf-report-generation` skill conventions.

The AGENT supplies grounded literature references (from LiteratureSearch) via
`references=` — a list of dicts {n, text}. The report inserts them verbatim; it
never fabricates citations.

Functions
  - generate_report(cfg, metrics_df, calls_df, figures, references,
                    intro=None, methods_extra=None, discussion=None,
                    limitations=None, next_steps=None) -> pdf_path
  - validate_pdf(path) -> dict

Usage
  from generate_report import generate_report, validate_pdf
  pdf = generate_report(cfg, metrics_df, calls_df, figures, references)
  validate_pdf(pdf)   # then Read(pdf, mode="media_output_check")
"""

import os
import datetime
from typing import Dict, List, Optional

import pandas as pd

from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, Image, HRFlowable, KeepTogether)

# ---- Phylo brand tokens (see references/report_layout.md) ----
PHYLO_GOLD = HexColor("#D4A04A")
PHYLO_GREEN = HexColor("#75A025")
PHYLO_ORANGE = HexColor("#FF9400")
PHYLO_RED = HexColor("#D62728")
PHYLO_BLUE = HexColor("#0279EE")
HEADING_COLOR = HexColor("#111111")
BODY_TEXT = HexColor("#2C2A26")
MUTED_TEXT = HexColor("#8A8378")
TABLE_HEADER_BG = PHYLO_GOLD
TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F9F7F3")
TABLE_BORDER = HexColor("#D5CFC5")
CALL_HEX = {"GREEN": PHYLO_GREEN, "AMBER": PHYLO_ORANGE, "RED": PHYLO_RED, "NA": HexColor("#BBBBBB")}

USABLE_W = letter[0] - 120  # 60pt margins


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="RTitle", fontName="Helvetica-Bold", fontSize=24,
                         textColor=HEADING_COLOR, leading=30, spaceAfter=4))
    s.add(ParagraphStyle(name="RSub", fontName="Helvetica", fontSize=11,
                         textColor=PHYLO_GOLD, spaceAfter=4))
    s.add(ParagraphStyle(name="Attrib", fontName="Helvetica-Oblique", fontSize=9.5,
                         textColor=MUTED_TEXT, spaceAfter=6))
    s.add(ParagraphStyle(name="H1", fontName="Helvetica-Bold", fontSize=16,
                         textColor=HEADING_COLOR, spaceBefore=18, spaceAfter=8))
    s.add(ParagraphStyle(name="Bd", fontName="Helvetica", fontSize=10.5,
                         textColor=BODY_TEXT, alignment=TA_JUSTIFY, leading=15, spaceAfter=8))
    s.add(ParagraphStyle(name="Cap", fontName="Helvetica-Oblique", fontSize=9,
                         textColor=MUTED_TEXT, alignment=TA_CENTER, spaceAfter=12))
    s.add(ParagraphStyle(name="Cell", fontName="Helvetica", fontSize=8.5,
                         textColor=BODY_TEXT, leading=11))
    s.add(ParagraphStyle(name="CellH", fontName="Helvetica-Bold", fontSize=8.5,
                         textColor=TABLE_HEADER_FG, leading=11))
    s.add(ParagraphStyle(name="CardTitle", fontName="Helvetica-Bold", fontSize=12,
                         textColor=HEADING_COLOR, leading=14))
    s.add(ParagraphStyle(name="CardCall", fontName="Helvetica-Bold", fontSize=13,
                         textColor=HexColor("#FFFFFF"), alignment=TA_CENTER, leading=15))
    s.add(ParagraphStyle(name="Strip", fontName="Helvetica-Bold", fontSize=7,
                         textColor=HexColor("#FFFFFF"), alignment=TA_CENTER, leading=9))
    return s


def _hdr_ftr(title):
    def cb(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED_TEXT)
        canvas.drawString(60, h - 40, title[:90])
        canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1)
        canvas.line(60, h - 48, w - 60, h - 48)
        canvas.setStrokeColor(TABLE_BORDER); canvas.setLineWidth(0.75)
        canvas.line(60, 40, w - 60, 40)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED_TEXT)
        canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
        canvas.restoreState()
    return cb


def divider():
    return HRFlowable(width=USABLE_W, thickness=1, color=PHYLO_GOLD,
                      spaceAfter=10, spaceBefore=4)


def _module_labels(calls_df):
    return [c for c in calls_df.columns if c not in ("unit", "OVERALL")]


def _infographic(story, s, calls_df, metrics_df, cfg):
    """One card per unit: overall call banner + 5-module status strip."""
    story.append(Paragraph("Release Scorecard — Summary", s["H1"]))
    story.append(Paragraph(
        "Each card is one unit (lot/batch/sample). The banner is the overall release call "
        "(the worst active module). The strip shows the per-module call. Thresholds are "
        "defaults, not universal standards — see the Scorecard section.", s["Bd"]))
    story.append(Spacer(1, 6))

    mod_cols = _module_labels(calls_df)
    short = {"A_identity_purity": "Identity", "B_residual_pluripotency": "Pluri",
             "C_offtarget_lineage": "Off-tgt", "D_maturity": "Maturity",
             "E_technical_qc": "Tech QC"}
    mrow = metrics_df.set_index("unit")

    cards = []
    for _, row in calls_df.iterrows():
        unit = row["unit"]
        overall = row.get("OVERALL", "NA")
        # banner
        banner = Table([[Paragraph(f"{unit}", s["CardTitle"])],
                        [Paragraph(f"{overall}", s["CardCall"])]],
                       colWidths=[USABLE_W / 2 - 16])
        banner.setStyle(TableStyle([
            ("BACKGROUND", (0, 1), (0, 1), CALL_HEX.get(overall, CALL_HEX["NA"])),
            ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        # strip cells
        strip_cells, strip_style = [], [
            ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        for j, mc in enumerate(mod_cols):
            call = row.get(mc, "NA")
            strip_cells.append(Paragraph(f"{short.get(mc, mc)}<br/>{call}", s["Strip"]))
            strip_style.append(("BACKGROUND", (j, 0), (j, 0), CALL_HEX.get(call, CALL_HEX["NA"])))
        strip = Table([strip_cells], colWidths=[(USABLE_W / 2 - 16) / max(len(mod_cols), 1)] * len(mod_cols))
        strip.setStyle(TableStyle(strip_style))
        # headline metric line
        met = mrow.loc[unit] if unit in mrow.index else None
        line = ""
        if met is not None:
            bits = []
            if "pct_target_purity" in met and pd.notna(met["pct_target_purity"]):
                bits.append(f"purity {met['pct_target_purity']:.1f}%")
            if "pct_offtarget" in met and pd.notna(met.get("pct_offtarget")):
                bits.append(f"off-tgt {met['pct_offtarget']:.1f}%")
            if "n_cells_final" in met:
                bits.append(f"n={int(met['n_cells_final'])}")
            line = "  ·  ".join(bits)
        card = Table([[banner], [strip], [Paragraph(line, s["Cap"])]],
                     colWidths=[USABLE_W / 2 - 12])
        card.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        cards.append(card)

    # lay cards two per row
    for i in range(0, len(cards), 2):
        pair = cards[i:i + 2]
        if len(pair) == 1:
            pair.append(Paragraph("", s["Bd"]))
        t = Table([pair], colWidths=[USABLE_W / 2, USABLE_W / 2])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 4),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        t.hAlign = "CENTER"
        story.append(t)
    story.append(PageBreak())


# human-readable column labels for the metrics/calls tables
_COL_LABELS = {
    "unit": "Unit", "type": "Type", "n_cells_final": "Cells",
    "pct_target_purity": "Purity %", "pct_clean_target": "Clean %",
    "pct_aberrant_target": "Aberrant %", "pct_true_contaminant": "Contam %",
    "pct_residual_pluripotent": "Resid pluri %", "n_triad_all3": "Triad n",
    "pct_offtarget": "Off-target %", "pct_mature_of_target": "Mature %",
    "retention_pct": "Retention %", "median_pct_mito": "Mito %",
    "doublet_rate_pct": "Doublet %", "species_contam_pct": "Cross-sp %",
    "A_identity_purity": "A Identity", "B_residual_pluripotency": "B Pluri",
    "C_offtarget_lineage": "C Off-tgt", "D_maturity": "D Maturity",
    "E_technical_qc": "E Tech QC", "OVERALL": "OVERALL",
    "module_metric": "Module / metric", "green": "GREEN", "red": "RED",
    "amber_between": "AMBER", "direction": "Direction",
}


def _fmt(v, floatfmt="{:.2f}"):
    if isinstance(v, float):
        if v != v:  # NaN
            return "-"
        return floatfmt.format(v)
    return str(v)


def _df_table(df, s, max_rows=30, floatfmt="{:.2f}", transpose_over=7, index_name="Metric"):
    """Render a DataFrame as a Platypus table.

    If the frame has more than `transpose_over` columns, TRANSPOSE it (metrics
    become rows, the 'unit' column becomes the headers) so headers never wrap
    character-by-character on the ~492pt page.
    """
    df = df.head(max_rows).copy()

    transposed = False
    if len(df.columns) > transpose_over and "unit" in df.columns:
        df = df.set_index("unit").T
        df.index.name = index_name
        df = df.reset_index()
        transposed = True

    def label(c):
        return _COL_LABELS.get(str(c), str(c))

    headers = [Paragraph(label(c), s["CellH"]) for c in df.columns]
    body = []
    for _, r in df.iterrows():
        cells = []
        for j, c in enumerate(df.columns):
            v = r[c]
            # first column of a transposed table holds metric names -> pretty-print
            txt = _COL_LABELS.get(str(v), _fmt(v, floatfmt)) if (transposed and j == 0) else _fmt(v, floatfmt)
            cells.append(Paragraph(txt, s["Cell"]))
        body.append(cells)
    data = [headers] + body

    ncol = len(df.columns)
    # give the first column a bit more room when transposed (metric names are long)
    if transposed:
        first = min(USABLE_W * 0.34, 170)
        rest = (USABLE_W - first) / max(ncol - 1, 1)
        widths = [first] + [rest] * (ncol - 1)
    else:
        widths = [USABLE_W / ncol] * ncol

    t = Table(data, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
             ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
             ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    if transposed:  # shade the metric-name column like a header
        style.append(("BACKGROUND", (0, 1), (0, -1), TABLE_ALT_ROW))
        style.append(("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"))
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    t.setStyle(TableStyle(style))
    t.hAlign = "CENTER"
    return t


def _fig(story, s, path, caption, width=USABLE_W):
    if not path or not os.path.exists(path):
        return
    from PIL import Image as PILImage
    iw, ih = PILImage.open(path).size
    w = width; h = w * ih / iw
    img = Image(path, width=w, height=h)
    img.hAlign = "CENTER"
    story.append(KeepTogether([img, Spacer(1, 4), Paragraph(caption, s["Cap"])]))


def generate_report(cfg: Dict, metrics_df: pd.DataFrame, calls_df: pd.DataFrame,
                    figures: Dict[str, str], references: Optional[List[Dict]] = None,
                    intro: Optional[str] = None, methods_extra: Optional[str] = None,
                    discussion: Optional[str] = None, limitations: Optional[str] = None,
                    next_steps: Optional[str] = None) -> str:
    s = _styles()
    product = cfg.get("product", "Cell-therapy product")
    title = f"scRNA-seq QC Release Scorecard — {product}"
    date_str = datetime.date.today().strftime("%B %d, %Y")
    out = os.path.join(cfg["outdir"],
                       f"report_{_slug(product)}_qc_release.pdf")

    doc = SimpleDocTemplate(out, pagesize=letter, topMargin=52, bottomMargin=52,
                            leftMargin=60, rightMargin=60, title=title)
    story = []

    # ---- Title ----
    story.append(Spacer(1, 12))
    story.append(Paragraph(title, s["RTitle"]))
    story.append(Paragraph("Single-cell RNA-seq lot-release characterization", s["RSub"]))
    story.append(Paragraph(f"<i>Generated by Biomni  |  {date_str}</i>", s["Attrib"]))
    story.append(divider())

    # ---- Infographic summary (page 1) ----
    _infographic(story, s, calls_df, metrics_df, cfg)

    # ---- Introduction ----
    story.append(Paragraph("Introduction", s["H1"]))
    intro = intro or (
        f"This report characterizes {_n_units(calls_df)} unit(s) of {product} by single-cell "
        f"RNA-seq to support lot-release decision-making. Each unit is scored across five "
        f"identity and safety modules — target-cell identity &amp; purity, residual pluripotency, "
        f"off-target lineage contamination, target-cell maturity, and technical quality — and each "
        f"module is expressed as a GREEN / AMBER / RED release call. The overall call for a unit is "
        f"the worst active module, so a single red module fails the lot. Cell-source: "
        f"<b>{cfg.get('source','?')}</b>; target cell: <b>{cfg.get('target_cell','?')}</b>"
        + (f"; engineering: <b>{cfg['engineering']}</b>" if cfg.get("engineering") else "") + ".")
    story.append(Paragraph(intro, s["Bd"]))

    # ---- Methods ----
    story.append(Paragraph("Methods", s["H1"]))
    methods = (
        "Units were loaded from " + ("a GEO accession" if cfg.get("is_geo") else "local matrices") +
        " and, where a multi-species reference was used, split by species (product-species cells "
        f"retained at fraction &gt; {cfg.get('keep_species_frac',0.9)}). Per unit, quality control "
        "used median-absolute-deviation outlier detection on log-scaled counts/genes with a "
        "mitochondrial ceiling, Scrublet doublet detection, and standard log-normalization "
        "(target sum 10<super>4</super>), reusing the Biomni scrnaseq-scanpy-core-analysis pipeline. "
        "Module scoring is expression-anchored: target-cell identity is called on raw detection of "
        "target markers (not a background-corrected signature score, which is unreliable in "
        "target-dominated products); residual pluripotency requires co-expression of specific "
        "pluripotency transcription factors above a per-unit shuffled-null threshold and excludes "
        "target-positive cells; off-target lineage is restricted to target-negative cells expressing "
        "&ge;2 markers of a non-target lineage. Marker panels were derived from a curated registry, "
        "CellMarker2, and literature grounding.")
    story.append(Paragraph(methods, s["Bd"]))
    if methods_extra:
        story.append(Paragraph(methods_extra, s["Bd"]))

    # ---- Results ----
    story.append(Paragraph("Results", s["H1"]))
    story.append(Paragraph("Per-unit headline QC metrics:", s["Bd"]))
    story.append(_df_table(metrics_df, s))
    story.append(Spacer(1, 10))
    _fig(story, s, figures.get("fig1"), "Figure 1. Per-unit QC distributions "
         "(UMI counts, genes, mitochondrial %, doublet score).")
    _fig(story, s, figures.get("fig2"), "Figure 2. Release scorecard. Each cell is a "
         "GREEN/AMBER/RED module call; overall is the worst active module.")
    if figures.get("fig3"):
        _fig(story, s, figures.get("fig3"), "Figure 3. Per-unit module overlays on UMAP.")
    _fig(story, s, figures.get("fig4"), "Figure 4. Cross-lot comparison of headline metrics "
         "(dashed lines mark GREEN and RED thresholds).")

    # ---- Scorecard ----
    story.append(PageBreak())
    story.append(Paragraph("Scorecard &amp; Thresholds", s["H1"]))
    story.append(Paragraph("Per-unit × module release calls:", s["Bd"]))
    story.append(_df_table(calls_df, s))
    story.append(Spacer(1, 8))
    thr_path = os.path.join(cfg["dirs"]["tables"], "06_thresholds_reference.csv")
    if os.path.exists(thr_path):
        story.append(Paragraph("Thresholds used (defaults — override per product):", s["Bd"]))
        story.append(_df_table(pd.read_csv(thr_path), s, floatfmt="{:.3f}"))

    # ---- Discussion ----
    story.append(Paragraph("Discussion", s["H1"]))
    disc = discussion or (
        "The scorecard separates units that meet default release expectations (all-GREEN) from "
        "those with identity, purity, off-target, or maturity concerns. Aberrant-but-target cells "
        "(target-positive cells carrying leaky off-lineage transcripts) are reported separately "
        "from true contaminants (target-negative, off-lineage) because they carry different "
        "manufacturing implications.")
    story.append(Paragraph(disc, s["Bd"]))

    # ---- Limitations ----
    story.append(Paragraph("Limitations", s["H1"]))
    lim = limitations or (
        "Thresholds are defaults, not regulatory standards, and must be set with the sponsor. "
        "Residual-pluripotency detection by scRNA-seq is limited by depth and cell number; a "
        "&ldquo;below detection&rdquo; result at these cell numbers is far coarser than orthogonal "
        "ddPCR/qPCR assays (which reach ~0.001–0.01%) and does not by itself certify absence of "
        "residual undifferentiated cells. Marker-based identity depends on panel choice; results "
        "should be confirmed with an orthogonal assay (flow cytometry, ddPCR) for release.")
    story.append(Paragraph(lim, s["Bd"]))

    # ---- Next steps ----
    story.append(Paragraph("Next Steps", s["H1"]))
    nxt = next_steps or (
        "1) Confirm any AMBER/RED module with an orthogonal release assay (flow cytometry for "
        "purity/maturity; ddPCR for residual pluripotency). 2) For units with off-target signal, "
        "characterize the contaminating population (functional-enrichment-from-degs). 3) Lock "
        "product-specific thresholds with the sponsor and re-run to finalize the release call.")
    story.append(Paragraph(nxt, s["Bd"]))

    # ---- References ----
    if references:
        story.append(Paragraph("References", s["H1"]))
        for r in references:
            story.append(Paragraph(f"[{r.get('n','?')}] {r.get('text','')}", s["Cap"]))

    doc.build(story, onFirstPage=_hdr_ftr(title), onLaterPages=_hdr_ftr(title))
    print(f"✓ PDF report -> {out}")
    return out


def validate_pdf(path: str) -> Dict:
    from pypdf import PdfReader
    reader = PdfReader(path)
    npages = len(reader.pages)
    size = os.path.getsize(path)
    text0 = reader.pages[0].extract_text() or ""
    ok = npages >= 2 and size > 5000 and len(text0.strip()) > 0
    print(f"  pages={npages}, size={size} bytes, page1_text={len(text0.strip())} chars -> "
          f"{'OK' if ok else 'FAIL'}")
    assert ok, "PDF validation failed (pages/size/text)"
    print("  → now run Read(path, mode='media_output_check') for a visual QA pass.")
    return {"pages": npages, "size": size, "ok": ok}


def _slug(x):
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", str(x)).strip("_")[:60] or "product"


def _n_units(calls_df):
    return len(calls_df)


if __name__ == "__main__":
    print("generate_report.py — import and call generate_report(cfg, metrics_df, calls_df, figures, references).")
