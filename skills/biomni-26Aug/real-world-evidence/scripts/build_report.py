#!/usr/bin/env python3
# =============================================================================
# build_report.py  --  Generic Phylo-branded PDF for the RWE cohort pipeline
# -----------------------------------------------------------------------------
# Reads the outputs produced by the R pipeline and emits a self-contained,
# branded PDF. NOTHING about the disease/drug/dataset is hardcoded here -- all
# study-specific text and numbers come from:
#   <out_dir>/report_manifest.json   (study identity, key numbers, lit refs,
#                                      methods params, next steps -- written by
#                                      the R driver: see run_all.R / SKILL.md)
#   <out_dir>/tables/*.csv           (Table 1, comparison, treatment, survival)
#   <out_dir>/figures/*.png          (infographic + KM/treatment figures)
#
# Brand system follows the pdf-report-generation skill exactly (colors, fonts,
# US-Letter margins, gold table headers, centered figures, KeepTogether, etc.).
#
# USAGE:  python build_report.py <out_dir>
#         (out_dir defaults to /mnt/results if omitted)
# =============================================================================
import os, sys, json, glob, datetime
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, PageBreak, Image, HRFlowable, KeepTogether)
from pypdf import PdfReader

# ---------------------------------------------------------------- brand colors
PHYLO_GOLD    = HexColor("#D4A04A")
PHYLO_BLUE    = HexColor("#0279EE")
PHYLO_GREEN   = HexColor("#75A025")
PHYLO_ORANGE  = HexColor("#FF9400")
HEADING       = HexColor("#111111")
BODY          = HexColor("#2C2A26")
MUTED         = HexColor("#8A8378")
TABLE_HDR_BG  = PHYLO_GOLD
TABLE_HDR_FG  = HexColor("#FFFFFF")
TABLE_ALT     = HexColor("#F9F7F3")
TABLE_BORDER  = HexColor("#D5CFC5")
OFF_WHITE     = HexColor("#FAF9F3")
USABLE_W      = 492  # letter, 60pt L/R margins

# ------------------------------------------------------------------ page chrome
def make_chrome(title):
    def _chrome(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
        canvas.drawString(60, h - 40, title[:96])
        canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1)
        canvas.line(60, h - 48, w - 60, h - 48)
        canvas.setStrokeColor(TABLE_BORDER); canvas.setLineWidth(0.75)
        canvas.line(60, 40, w - 60, 40)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
        canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
        canvas.restoreState()
    return _chrome

# ----------------------------------------------------------------------- styles
def build_styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="RTitle", fontName="Helvetica-Bold", fontSize=25,
        textColor=HEADING, leading=30, spaceAfter=6))
    s.add(ParagraphStyle(name="Sub", fontName="Helvetica", fontSize=11,
        textColor=PHYLO_GOLD, spaceAfter=4))
    s.add(ParagraphStyle(name="Attrib", fontName="Helvetica-Oblique",
        fontSize=10, textColor=MUTED, spaceAfter=8))
    s.add(ParagraphStyle(name="H1", fontName="Helvetica-Bold", fontSize=17,
        textColor=HEADING, spaceBefore=20, spaceAfter=9))
    s.add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=12.5,
        textColor=HEADING, spaceBefore=12, spaceAfter=5))
    s.add(ParagraphStyle(name="Bd", fontName="Helvetica", fontSize=10.5,
        textColor=BODY, alignment=TA_JUSTIFY, spaceAfter=8, leading=15))
    s.add(ParagraphStyle(name="Cap", fontName="Helvetica-Oblique", fontSize=9,
        textColor=MUTED, alignment=TA_CENTER, spaceAfter=14))
    s.add(ParagraphStyle(name="Cell", fontName="Helvetica", fontSize=9,
        textColor=BODY, leading=12))
    s.add(ParagraphStyle(name="CellHdr", fontName="Helvetica-Bold", fontSize=9,
        textColor=TABLE_HDR_FG, leading=12))
    s.add(ParagraphStyle(name="Ref", fontName="Helvetica", fontSize=9,
        textColor=BODY, leading=13, spaceAfter=5, leftIndent=16,
        firstLineIndent=-16))
    s.add(ParagraphStyle(name="RWEBullet", fontName="Helvetica", fontSize=10.5,
        textColor=BODY, leading=15, spaceAfter=4, leftIndent=14,
        firstLineIndent=-9))
    return s

def divider():
    return HRFlowable(width=USABLE_W, thickness=1, color=PHYLO_GOLD,
                      spaceAfter=10, spaceBefore=4)

def callout(text, style):
    t = Table([[Paragraph(text, style)]], colWidths=[USABLE_W])
    t.hAlign = "CENTER"
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), OFF_WHITE),
        ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, PHYLO_GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14)]))
    return t

def df_to_table(df, styles, col_frac=None, max_rows=25):
    """Render a DataFrame as a branded table. col_frac = list of column width
    fractions (sums ~1). Truncates to max_rows with a note row."""
    df = df.copy()
    note = None
    if len(df) > max_rows:
        note = f"Showing {max_rows} of {len(df)} rows; full data in CSV."
        df = df.head(max_rows)
    cols = list(df.columns)
    if col_frac is None:
        col_frac = [1.0 / len(cols)] * len(cols)
    widths = [USABLE_W * f for f in col_frac]
    header = [Paragraph(f"<b>{c}</b>", styles["CellHdr"]) for c in cols]
    rows = [[Paragraph("" if pd.isna(v) else str(v), styles["Cell"]) for v in r]
            for r in df.itertuples(index=False)]
    data = [header] + rows
    t = Table(data, colWidths=widths, repeatRows=1)
    t.hAlign = "CENTER"
    st = [("BACKGROUND", (0, 0), (-1, 0), TABLE_HDR_BG),
          ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HDR_FG),
          ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
          ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
          ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
          ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
          ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for i in range(2, len(data), 2):
        st.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT))
    t.setStyle(TableStyle(st))
    return t, note

def fig(path, styles, caption, max_w=USABLE_W, max_h=300):
    """Embed an image scaled to fit, bound to its caption."""
    try:
        from PIL import Image as PILImage
        iw, ih = PILImage.open(path).size
        ratio = min(max_w / iw, max_h / ih)
        w, h = iw * ratio, ih * ratio
    except Exception:
        w, h = max_w, max_h * 0.7
    img = Image(path, width=w, height=h); img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1, 4), Paragraph(caption, styles["Cap"])])

# ------------------------------------------------------------------------- main
def build(out_dir):
    tdir = os.path.join(out_dir, "tables")
    fdir = os.path.join(out_dir, "figures")
    man_path = os.path.join(out_dir, "report_manifest.json")
    man = {}
    if os.path.exists(man_path):
        with open(man_path) as fh:
            man = json.load(fh)

    def mget(key, default):
        """Return a manifest text field as a list of paragraphs. Robust to JSON
        auto-unboxing: a single string becomes a one-element list (NOT iterated
        character-by-character), None/absent falls back to `default`."""
        v = man.get(key)
        if v is None:
            return default
        if isinstance(v, str):
            v = [v]
        return [str(x) for x in v if x is not None and str(x).strip()]

    title    = man.get("study_title", "Real-World Evidence Cohort Study")
    cohort   = man.get("cohort_label", "Cohort")
    endpoint = man.get("primary_endpoint", "survival")
    date_str = datetime.date.today().strftime("%B %d, %Y")
    styles   = build_styles()
    story    = []

    def csv(name):
        p = os.path.join(tdir, name)
        return pd.read_csv(p) if os.path.exists(p) else None

    def csv_first(*names):
        """First readable CSV among candidates (None-safe; avoids DataFrame
        truthiness pitfalls)."""
        for n in names:
            df = csv(n)
            if df is not None:
                return df
        return None

    # ---- Title -------------------------------------------------------------
    story += [Spacer(1, 30), Paragraph(title, styles["RTitle"]),
              Paragraph("Real-World Evidence  |  Retrospective Cohort Analysis",
                        styles["Sub"]), Spacer(1, 6),
              Paragraph(f"<i>Generated by Biomni  |  {date_str}</i>",
                        styles["Attrib"]), divider(), Spacer(1, 6)]

    # ---- Infographic summary page -----------------------------------------
    ig = os.path.join(fdir, "infographic_summary.png")
    if os.path.exists(ig):
        story.append(Paragraph("Study at a Glance", styles["H1"]))
        story.append(fig(ig, styles,
            "Figure 1. Visual summary. Cohort selection, headline outcomes, "
            "landmark survival, and top treatment classes. All values are "
            "computed directly from the analysis outputs."))
        story.append(PageBreak())

    # ---- Executive summary -------------------------------------------------
    story.append(Paragraph("Executive Summary", styles["H1"]))
    for para in mget("executive_summary", [
        f"This report summarizes a retrospective, real-world evidence (RWE) "
        f"cohort analysis of {cohort} patients derived from structured "
        f"electronic health record (EHR) data. Patients were identified by "
        f"diagnosis codes, characterized at baseline, profiled for treatment "
        f"patterns, and followed for {endpoint}.",
        "All comparisons are exploratory and descriptive. Findings are "
        "hypothesis-generating and require confirmation in prospective or "
        "externally validated cohorts."]):
        story.append(Paragraph(para, styles["Bd"]))
    if man.get("key_finding"):
        story.append(Spacer(1, 4))
        story.append(callout("<b>Key finding.</b> " + man["key_finding"], styles["Bd"]))

    # ---- Methods -----------------------------------------------------------
    story.append(Paragraph("Methods", styles["H1"]))
    story.append(Paragraph("Data source and cohort", styles["H2"]))
    story.append(Paragraph(man.get("methods_data",
        f"Structured EHR tables (demographics, admissions, diagnoses, "
        f"medication orders, and — where available — ICU stays) were harmonized "
        f"to a common schema. The {cohort} cohort was defined by diagnosis "
        f"codes; a comparator group was drawn from the remaining eligible "
        f"population. One index encounter per patient was selected as the unit "
        f"of longitudinal follow-up."), styles["Bd"]))
    story.append(Paragraph("Analyses", styles["H2"]))
    story.append(Paragraph(man.get("methods_analysis",
        "Baseline characteristics were tabulated (median [IQR] for continuous "
        "variables, n (%) for categorical). Between-group comparisons used the "
        "Wilcoxon rank-sum test (continuous) and Fisher's exact test "
        "(categorical). Treatment exposure was classified from medication "
        "orders restricted to systemic routes. Time-to-event outcomes were "
        "estimated by Kaplan-Meier with landmark survival probabilities and "
        "log-rank tests. Multivariable Cox regression was reported only when "
        "the events-per-variable (EPV) threshold was met."), styles["Bd"]))
    # Methods parameter table (from manifest, if provided). Bind heading + table
    # together so a single trailing row cannot orphan onto a near-empty page.
    if man.get("method_params"):
        mp = pd.DataFrame(man["method_params"], columns=["Parameter", "Value"])
        t, _ = df_to_table(mp, styles, col_frac=[0.45, 0.55])
        story.append(KeepTogether([
            Paragraph("Analysis parameters", styles["H2"]), Spacer(1, 4), t]))
    story.append(PageBreak())

    # ---- Results -----------------------------------------------------------
    story.append(Paragraph("Results", styles["H1"]))

    t1 = csv("table1_cohort.csv")
    if t1 is not None:
        story.append(Paragraph("Baseline characteristics", styles["H2"]))
        nc = len(t1.columns)
        frac = [0.34] + [(0.66 / (nc - 1))] * (nc - 1) if nc > 1 else [1.0]
        tbl, note = df_to_table(t1, styles, col_frac=frac)
        story.append(tbl)
        story.append(Paragraph("Table 1. Baseline characteristics by group.",
                               styles["Cap"]))
        if note: story.append(Paragraph(note, styles["Cap"]))

    cmp = csv_first("cohort_vs_comparator.csv", "sepsis_vs_icu_comparison.csv")
    if cmp is not None:
        story.append(Paragraph("Cohort vs. comparator", styles["H2"]))
        nc = len(cmp.columns)
        frac = [0.30] + [(0.70 / (nc - 1))] * (nc - 1) if nc > 1 else [1.0]
        tbl, note = df_to_table(cmp, styles, col_frac=frac)
        story.append(tbl)
        story.append(Paragraph("Table 2. Focused comparison of key measures "
            "(p-values exploratory).", styles["Cap"]))

    # Treatment figures/tables
    tx = csv("treatment_class_summary.csv")
    if tx is not None:
        story.append(Paragraph("Treatment patterns", styles["H2"]))
        nc = len(tx.columns)
        tbl, note = df_to_table(tx, styles,
            col_frac=[0.4] + [(0.6/(nc-1))]*(nc-1) if nc > 1 else [1.0],
            max_rows=15)
        story.append(tbl)
        story.append(Paragraph("Table 3. Treatment classes in the cohort.",
                               styles["Cap"]))
        if note: story.append(Paragraph(note, styles["Cap"]))

    # Embed any KM / treatment figures present (exclude the infographic)
    figs = sorted(glob.glob(os.path.join(fdir, "*.png")))
    figs = [f for f in figs if "infographic" not in os.path.basename(f)]
    fig_caps = man.get("figure_captions", {})
    if figs:
        story.append(Paragraph("Figures", styles["H2"]))
        for i, fp in enumerate(figs, start=2):
            base = os.path.basename(fp)
            cap = fig_caps.get(base, f"Figure {i}. {base}")
            story.append(fig(fp, styles, cap))

    # Landmark survival table
    lm = csv_first("survival_landmark_cohort.csv", "survival_landmark_sepsis.csv")
    if lm is not None:
        story.append(Paragraph("Landmark survival", styles["H2"]))
        tbl, _ = df_to_table(lm, styles)
        story.append(tbl)
        story.append(Paragraph("Table 4. Landmark survival probabilities "
            "(Kaplan-Meier) with 95% confidence intervals.", styles["Cap"]))

    story.append(PageBreak())

    # ---- Discussion --------------------------------------------------------
    story.append(Paragraph("Discussion", styles["H1"]))
    for para in mget("discussion", [
        "The findings above describe real-world patterns of presentation, "
        "treatment, and outcomes in the study cohort. Effect sizes should be "
        "interpreted in the context of confounding by indication, which is "
        "intrinsic to observational EHR data.",
        "Two mortality denominators may differ: patient-level (one index "
        "encounter per patient) versus admission-level (all encounters). "
        "In-hospital and all-cause survival curves can diverge when deaths "
        "occur after discharge."]):
        story.append(Paragraph(para, styles["Bd"]))

    story.append(Paragraph("Limitations", styles["H2"]))
    for lim in mget("limitations", [
        "Retrospective, single-source design; no randomization.",
        "Diagnosis-code-based phenotyping may misclassify cases.",
        "All p-values are exploratory; no multiple-testing correction was applied.",
        "Treatment exposure reflects orders, not confirmed administration.",
        "Residual and unmeasured confounding cannot be excluded."]):
        story.append(Paragraph("&bull;&nbsp; " + lim, styles["RWEBullet"]))

    # ---- Conclusions -------------------------------------------------------
    story.append(Paragraph("Conclusions", styles["H1"]))
    for para in mget("conclusions", [
        f"In this real-world {cohort} cohort, baseline severity, treatment "
        f"intensity, and {endpoint} were characterized and compared with a "
        f"contemporaneous comparator. The results are consistent with the "
        f"expected clinical course and provide a reproducible, config-driven "
        f"template for further RWE analyses."]):
        story.append(Paragraph(para, styles["Bd"]))

    # ---- Next steps --------------------------------------------------------
    story.append(Paragraph("Recommended Next Steps", styles["H2"]))
    for step in mget("next_steps", [
        "Validate the cohort definition against chart review or a validated phenotype.",
        "Expand to the full (credentialed) data source to increase power and EPV.",
        "Pre-specify confounders and apply propensity or regression adjustment.",
        "Assess robustness with sensitivity analyses (alternative code sets, windows)."]):
        story.append(Paragraph("&bull;&nbsp; " + step, styles["RWEBullet"]))

    # ---- References --------------------------------------------------------
    refs = mget("references", [])
    if refs:
        story.append(Paragraph("References", styles["H1"]))
        for i, r in enumerate(refs, 1):
            story.append(Paragraph(f"{i}. {r}", styles["Ref"]))

    # ---- Build + validate --------------------------------------------------
    out_pdf = os.path.join(out_dir, man.get("output_filename",
                                            "report_rwe_cohort.pdf"))
    doc = SimpleDocTemplate(out_pdf, pagesize=letter, topMargin=52,
        bottomMargin=52, leftMargin=60, rightMargin=60,
        title=title, author="Biomni")
    chrome = make_chrome(title)
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)

    reader = PdfReader(out_pdf)
    npages = len(reader.pages); size = os.path.getsize(out_pdf)
    txt = (reader.pages[0].extract_text() or "").strip()
    assert npages >= 2, f"Only {npages} page(s)"
    assert size > 5000, f"Only {size} bytes"
    assert len(txt) > 0, "First page has no extractable text"
    print(f"[build_report] OK -> {out_pdf}")
    print(f"[build_report] pages={npages}  size={size:,} bytes")
    return out_pdf


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/mnt/results"
    build(out)
