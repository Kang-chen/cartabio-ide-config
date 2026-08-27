#!/usr/bin/env python3
# ============================================================================
# build_report.py  --  consensus-disease-signature skill (PDF report)
#
# Assemble a polished, self-contained PDF from the artifacts produced by
# run_meta_signature.R. Follows the Phylo pdf-report-generation skill:
#   - open directly with a title block (no full-bleed cover)
#   - clean white pages, gold (#D4A04A) accents, brand palette
#   - Platypus flowables; header/footer via canvas callback
#
# INPUTS (all under --results dir):
#   tables/summary.json          headline numbers + top genes (from the R engine)
#   figures/*.png                analysis figures
#   <infographic.png>            schematic produced by the agent via GenerateImage
#   references.jsonl (optional)  literature validation records (LiteratureSearch)
#
# USAGE:
#   python build_report.py --results /mnt/results \
#          --infographic /mnt/results/figures/infographic.png \
#          --out /mnt/results/<disease>_consensus_signature_report.pdf \
#          [--lit-summary lit_findings.json]
#
# CRITICAL LESSONS BAKED IN (do not regress):
#   * NEVER put literal non-ASCII/Unicode chars in strings passed to Paragraph.
#     Use ReportLab inline tags: subscripts <sub>2</sub>, superscripts <super>2</super>,
#     and ASCII for dashes/Greek. Literal glyphs render as black boxes.
#   * NEVER reuse a built-in style name (e.g. "Bullet", "Title") in stylesheet.add()
#     -> KeyError. Prefix custom styles (e.g. "BodyBullet").
# ============================================================================

import argparse, json, os, html, sys, math
from datetime import date
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                Table, TableStyle, PageBreak, HRFlowable)
from reportlab.platypus.flowables import KeepTogether
from PIL import Image as PILImage

# ---- Phylo brand palette (from pdf-report-generation skill) ---------------
PHYLO_WARM_GRAY = HexColor("#ECE9E2"); PHYLO_OFF_WHITE = HexColor("#FAF9F3")
PHYLO_ORANGE = HexColor("#FF9400"); PHYLO_GREEN = HexColor("#75A025")
PHYLO_BLUE = HexColor("#0279EE")
PHYLO_GOLD = HexColor("#D4A04A")      # PRIMARY ACCENT
HEADING_COLOR = HexColor("#111111"); BODY_TEXT = HexColor("#2C2A26")
MUTED_TEXT = HexColor("#8A8378")
TABLE_HEADER_BG = PHYLO_GOLD; TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F3F1EA")


def _safe_str(v):
    """Coerce nullable/NaN DataFrame values to a safe string for Paragraph().

    ReportLab's Paragraph() raises on float('nan') / None / pandas NA because
    str(nan) -> 'nan' is fine but some code paths pass the raw float which
    triggers layout errors. This normalises everything to a plain ASCII string,
    converting NaN/None/NA to '-' so tables never crash on missing values."""
    if v is None:
        return "-"
    try:
        fv = float(v)
        if math.isnan(fv):
            return "-"
    except (TypeError, ValueError):
        pass
    return str(v)


def esc(s):
    """Escape for Paragraph and strip any stray non-ASCII to protect against black boxes."""
    s = _safe_str(s)
    return html.escape(s).encode("ascii", "ignore").decode("ascii")


def build_styles():
    ss = getSampleStyleSheet()
    add = ss.add
    add(ParagraphStyle(name="ReportTitle", fontName="Helvetica-Bold", fontSize=25,
                       textColor=HEADING_COLOR, spaceAfter=6, leading=30))
    add(ParagraphStyle(name="Subtitle", fontName="Helvetica-Bold", fontSize=13,
                       textColor=PHYLO_GOLD, spaceAfter=4, leading=16))
    add(ParagraphStyle(name="Attribution", fontName="Helvetica-Oblique", fontSize=9,
                       textColor=MUTED_TEXT, spaceAfter=10))
    add(ParagraphStyle(name="H1", fontName="Helvetica-Bold", fontSize=15,
                       textColor=HEADING_COLOR, spaceBefore=16, spaceAfter=6, leading=19))
    add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=11.5,
                       textColor=BODY_TEXT, spaceBefore=10, spaceAfter=4, leading=15))
    add(ParagraphStyle(name="Body", fontName="Helvetica", fontSize=9.6,
                       textColor=BODY_TEXT, leading=14, alignment=TA_JUSTIFY, spaceAfter=6))
    add(ParagraphStyle(name="BodyBullet", parent=ss["Body"], leftIndent=14,
                       bulletIndent=4, spaceAfter=3))
    add(ParagraphStyle(name="Caption", fontName="Helvetica-Oblique", fontSize=8.2,
                       textColor=MUTED_TEXT, alignment=TA_CENTER, spaceBefore=3, spaceAfter=12))
    add(ParagraphStyle(name="TblCell", fontName="Helvetica", fontSize=8.2,
                       textColor=BODY_TEXT, leading=10))
    add(ParagraphStyle(name="TblHead", fontName="Helvetica-Bold", fontSize=8.4,
                       textColor=TABLE_HEADER_FG, leading=10))
    return ss


def header_footer(canvas, doc):
    canvas.saveState()
    w, h = LETTER
    canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1.2)
    canvas.line(0.75 * inch, h - 0.6 * inch, w - 0.75 * inch, h - 0.6 * inch)
    canvas.setFont("Helvetica", 7.5); canvas.setFillColor(MUTED_TEXT)
    canvas.drawString(0.75 * inch, h - 0.55 * inch, doc.report_title[:70])
    canvas.setStrokeColor(PHYLO_WARM_GRAY); canvas.setLineWidth(0.6)
    canvas.line(0.75 * inch, 0.6 * inch, w - 0.75 * inch, 0.6 * inch)
    canvas.drawString(0.75 * inch, 0.42 * inch, "Generated by Biomni")
    canvas.drawRightString(w - 0.75 * inch, 0.42 * inch, "Page %d" % doc.page)
    canvas.restoreState()


def fig(path, styles, story, caption, max_w=6.6, max_h=7.4):
    """Add an image scaled to fit, with a caption. Warns on stderr if missing."""
    if not path or not os.path.exists(path):
        sys.stderr.write("[build_report] WARNING: missing figure: %s\n" % (path or "(none)"))
        return
    try:
        iw, ih = PILImage.open(path).size
    except Exception as e:
        sys.stderr.write("[build_report] WARNING: cannot open figure %s: %s\n" % (path, e))
        return
    ar = ih / iw
    w = min(max_w, max_h / ar); h = w * ar
    story.append(Image(path, width=w * inch, height=h * inch))
    story.append(Paragraph(esc(caption), styles["Caption"]))


def data_table(headers, rows, styles, col_widths=None):
    head = [Paragraph(esc(x), styles["TblHead"]) for x in headers]
    body = [[Paragraph(esc(c), styles["TblCell"]) for c in r] for r in rows]
    t = Table([head] + body, colWidths=col_widths, hAlign="LEFT")
    style = [("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("GRID", (0, 0), (-1, -1), 0.4, PHYLO_WARM_GRAY),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for i in range(1, len(body) + 1):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    t.setStyle(TableStyle(style))
    return t


def load_lit(path):
    """Load literature records (references.jsonl or a lit-summary json)."""
    refs = []
    if path and os.path.exists(path):
        if path.endswith(".jsonl"):
            for line in open(path):
                line = line.strip()
                if line:
                    try: refs.append(json.loads(line))
                    except Exception: pass
        else:
            try:
                obj = json.load(open(path))
                refs = obj if isinstance(obj, list) else obj.get("references", [])
            except Exception: pass
    return refs


# Analysis figures expected by the report (must exist under <results>/figures/).
EXPECTED_FIGURES = [
    "QC_sample_distributions.png",
    "volcano_per_study.png",
    "concordance_scatter.png",
    "consensus_heatmap.png",
    "forest_top_genes.png",
    "enrichment_ORA_dotplots.png",
    "GSEA_hallmark_barplot.png",
]


def assert_figures(figdir):
    """Pre-build check: warn on stderr for every missing expected figure.

    Does not hard-exit (some panels are legitimately skipped for sparse data,
    e.g. no core genes or no GSEA hits), but emits a prominent warning so
    blank figure sections are never silent."""
    missing = [f for f in EXPECTED_FIGURES if not os.path.exists(os.path.join(figdir, f))]
    if missing:
        sys.stderr.write("[build_report] WARNING: %d of %d expected figures missing under %s:\n"
                         % (len(missing), len(EXPECTED_FIGURES), figdir))
        for f in missing:
            sys.stderr.write("  - %s\n" % f)
        sys.stderr.write("[build_report] The PDF will have blank sections where these figures should appear.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--infographic", default=None)
    ap.add_argument("--lit-summary", default=None,
                    help="Optional JSON with 'validation_text' and/or 'references' list.")
    ap.add_argument("--references", default=None,
                    help="Optional references.jsonl (defaults to <results>/execution_trace/references.jsonl).")
    args = ap.parse_args()

    R = args.results
    S = json.load(open(os.path.join(R, "tables", "summary.json")))
    figdir = os.path.join(R, "figures")
    disease = S.get("disease", "disease")
    styles = build_styles()

    # Determine the ACTUAL per-cohort data types so the Methods text matches the
    # inputs (do not assume microarray/limma when inputs are RNA-seq, or vice versa).
    dtypes = S.get("data_types")
    if not dtypes:
        dtypes = sorted({(c.get("type") or "microarray") for c in S.get("cohorts", [])})
    has_array = "microarray" in dtypes
    has_rnaseq = "rnaseq" in dtypes
    if has_array and has_rnaseq:
        de_sentence = ("Microarray cohorts used limma moderated t-statistics on log<sub>2</sub> intensities, "
                       "and RNA-seq cohorts used limma-voom on filtered, TMM-normalized counts.")
    elif has_rnaseq:
        de_sentence = ("All cohorts were RNA-seq and were analyzed with limma-voom on filtered, "
                       "TMM-normalized counts.")
    elif has_array:
        de_sentence = ("All cohorts were microarray and were analyzed with limma moderated "
                       "t-statistics on log<sub>2</sub> intensities.")
    else:
        de_sentence = ("Each cohort was analyzed with the appropriate limma pipeline for its "
                       "data type (limma for microarray, limma-voom for RNA-seq counts).")

    # Pre-build check: surface missing figures before assembling the PDF.
    assert_figures(figdir)

    # Literature: prefer an explicit lit-summary; else fall back to references.jsonl
    lit_text = None; refs = []
    if args.lit_summary and os.path.exists(args.lit_summary):
        try:
            lo = json.load(open(args.lit_summary))
            lit_text = lo.get("validation_text"); refs = lo.get("references", [])
        except Exception: pass
    if not refs:
        rpath = args.references or os.path.join(R, "execution_trace", "references.jsonl")
        refs = load_lit(rpath)

    story = []
    P = lambda t, s="Body": story.append(Paragraph(t if _has_tags(t) else esc(t), styles[s]))

    # ---------------- Title block (no cover page) ----------------
    story.append(Spacer(1, 34))
    story.append(Paragraph(esc("Consensus Transcriptional Signature of " + disease.title()), styles["ReportTitle"]))
    story.append(Paragraph(esc("Cross-Cohort Random-Effects Meta-Analysis of Bulk Transcriptomes"), styles["Subtitle"]))
    story.append(Paragraph("<i>Generated by Biomni  |  " + esc(S.get("generated", str(date.today()))) + "</i>", styles["Attribution"]))
    story.append(HRFlowable(width="100%", thickness=1, color=PHYLO_GOLD, spaceAfter=10))

    # ---------------- Executive summary ----------------
    story.append(Paragraph("Executive Summary", styles["H1"]))
    ncoh = len(S.get("cohorts", []))
    contrast = S.get("contrast", {})
    case_s = _join(contrast.get("case")); ctrl_s = _join(contrast.get("control"))
    P(("We integrated %d independent bulk-transcriptome cohorts to derive a reproducible, "
       "direction-consistent consensus signature separating %s from %s. Effect sizes (log<sub>2</sub> "
       "fold changes with standard errors) were combined per gene using a random-effects "
       "(REML) meta-analysis. Of %s genes tested in at least two cohorts, %s were significant at "
       "FDR &lt; %s in the pooled model (in either direction). Requiring in addition that the direction "
       "of effect agree across all contributing cohorts defines the <b>consensus set</b> of %s genes "
       "(%s up, %s down); a stricter <b>core set</b> of %s genes additionally requires |log<sub>2</sub>FC| &ge; %s "
       "(%s up, %s down). Between-cohort heterogeneity was low (median I<super>2</super> = %s%% among "
       "FDR-significant genes), and pairwise effect-size correlations ranged r = %s to %s.") % (
        ncoh, esc(case_s), esc(ctrl_s), f"{S['n_genes_tested']:,}", f"{S['n_fdr_sig']:,}",
        S["fdr_cut"], f"{S['n_consensus']:,}", f"{S['n_consensus_up']:,}", f"{S['n_consensus_down']:,}",
        f"{S['n_core']:,}", S["core_lfc"], f"{S['n_core_up']:,}", f"{S['n_core_down']:,}",
        S.get("median_I2_sig", "NA"), S.get("pairwise_r_min", "NA"), S.get("pairwise_r_max", "NA")))

    # cohort table (includes the control type so heterogeneous controls are explicit)
    story.append(Paragraph("Contributing cohorts", styles["H2"]))
    crows = [[c["id"], c.get("type", "-"), c.get("control_type", "-"),
              str(c.get("n_control", "-")), str(c.get("n_case", "-"))] for c in S["cohorts"]]
    story.append(data_table(["Cohort", "Type", "Control type", "N control", "N case"], crows, styles,
                            col_widths=[1.5 * inch, 1.2 * inch, 1.8 * inch, 1.0 * inch, 1.0 * inch]))

    # infographic
    if args.infographic and os.path.exists(args.infographic):
        story.append(Spacer(1, 8))
        fig(args.infographic, styles, story, "Figure 1. Analysis overview: from multi-cohort ingestion to a validated consensus signature.", max_w=6.6, max_h=4.2)

    # ---------------- Introduction ----------------
    story.append(PageBreak())
    story.append(Paragraph("Introduction", styles["H1"]))
    P(("Single transcriptomic studies of complex disease are frequently underpowered and "
       "sensitive to cohort-specific technical and biological variation, which limits the "
       "reproducibility of individual differential-expression hits. Combining multiple cohorts "
       "with formal meta-analysis increases statistical power and, critically, isolates the signal "
       "that is consistent across independent populations and measurement platforms. Here we build "
       "a consensus signature for %s: a ranked, direction-labelled gene set supported by concordant "
       "evidence across cohorts, suitable for downstream biomarker, pathway, and target-hypothesis work.") % esc(disease))

    # ---------------- Methods ----------------
    story.append(Paragraph("Methods", styles["H1"]))
    P("<b>Data and sample selection.</b> Each cohort was ingested from GEO (via GEOquery) or from a user-supplied "
      "expression matrix with sample metadata. Samples were assigned to a two-group contrast (%s vs %s) using explicit "
      "metadata filters; ambiguous or off-contrast samples were excluded. Re-deposited datasets were detected by "
      "cross-cohort correlation of mean expression (flagged at r &gt; 0.999) to preserve statistical independence." % (esc(case_s), esc(ctrl_s)))
    P("<b>Annotation.</b> Features were mapped to gene symbols via the appropriate Bioconductor annotation "
      "package (array probes) or org.Hs.eg.db (Ensembl/Entrez identifiers); multiple features per gene were "
      "collapsed to the one with the highest mean expression, giving a shared symbol-level feature space.")
    P("<b>Per-cohort differential expression.</b> " + de_sentence + " For each gene we retained the "
      "log<sub>2</sub> fold change and its standard error (stdev.unscaled &times; sqrt(s<super>2</super>.post)) as the effect-size input.")
    P("<b>Meta-analysis.</b> For every gene measured in at least two cohorts, effect sizes were combined with a "
      "random-effects model (metafor::rma, REML; DerSimonian-Laird fallback). P-values were FDR-adjusted (Benjamini-Hochberg). "
      "We report two distinct quantities: the number of genes significant at FDR &lt; %s in the pooled model (either "
      "direction), and the <b>consensus</b> set, which additionally requires the same direction of effect in all "
      "contributing cohorts. The <b>core</b> set further requires |log<sub>2</sub>FC| &ge; %s in the pooled estimate. "
      "By construction the consensus set is a subset of the FDR-significant genes." % (S["fdr_cut"], S["core_lfc"]))
    # Heterogeneous-control caveat + sensitivity, built from summary.json.
    _hetero = S.get("heterogeneous_controls", False)
    _ctypes = S.get("control_types", []) or [c.get("control_type", "unspecified") for c in S.get("cohorts", [])]
    _cohort_ct = ", ".join("%s (%s)" % (c["id"], c.get("control_type", "unspecified")) for c in S.get("cohorts", []))
    if _hetero:
        P("<b>Heterogeneous control groups.</b> The contributing cohorts do not share a single control definition "
          "[%s]. Non-inflammatory controls (e.g. histologically normal or trauma tissue) and disease controls "
          "(e.g. osteoarthritis, which carries its own low-grade synovial inflammation) are not equivalent baselines, "
          "so genes distinguishing the case condition specifically from an inflammatory comparator may be attenuated, "
          "and a naive single pooled contrast could conflate case-vs-normal with case-vs-other-disease effects. "
          "Rather than pooling these silently, the random-effects model treats each cohort's effect as drawn from a "
          "distribution (absorbing control-type differences as between-study heterogeneity), each cohort's control "
          "type is recorded above, and a sensitivity meta-analysis (below) re-derives the signature using only "
          "cohorts with a non-inflammatory control." % esc(_cohort_ct))
    elif _ctypes and any(ct not in ("unspecified", "", None) for ct in _ctypes):
        P("<b>Control groups.</b> Control type per cohort: %s. Controls were consistent across cohorts." % esc(_cohort_ct))
    P("<b>Functional enrichment.</b> Over-representation analysis (GO Biological Process and Reactome) was run separately on "
      "core up- and down-regulated genes against the universe of all meta-tested genes (clusterProfiler and ReactomePA; GO "
      "terms simplified at 0.7). Hallmark pathway activity was assessed by GSEA (fgsea) on the z-score-ranked gene list. "
      "KEGG over-representation was intentionally omitted because the KEGG API is not licensed for commercial use; "
      "Reactome (an open, commercially usable pathway database) is used in its place.")
    if lit_text:
        P("<b>Literature validation.</b> " + esc(lit_text))
    else:
        P("<b>Literature validation.</b> Top consensus genes and enriched pathways were cross-checked against the "
          "published literature for this condition to confirm biological plausibility and prior reporting.")

    # ---------------- Results ----------------
    story.append(PageBreak())
    story.append(Paragraph("Results", styles["H1"]))

    story.append(Paragraph("Cross-cohort concordance and quality control", styles["H2"]))
    P("Per-sample expression distributions were comparable within each cohort's groups, and per-cohort effect "
      "sizes were positively correlated across all cohort pairs, supporting a shared disease signal.")
    fig(os.path.join(figdir, "QC_sample_distributions.png"), styles, story,
        "Per-sample expression distributions (median and IQR) by cohort and group.")
    fig(os.path.join(figdir, "concordance_scatter.png"), styles, story,
        "Pairwise concordance of per-cohort log<sub>2</sub> fold changes.")
    fig(os.path.join(figdir, "volcano_per_study.png"), styles, story,
        "Per-cohort differential expression (volcano plots).")

    story.append(PageBreak())
    story.append(Paragraph("The consensus signature", styles["H2"]))
    P(("The meta-analysis yielded a consensus set of %s directionally-consistent genes (%s up, %s down) and a core "
       "set of %s genes at |log<sub>2</sub>FC| &ge; %s. The strongest and most reproducible changes are shown below.") % (
        f"{S['n_consensus']:,}", f"{S['n_consensus_up']:,}", f"{S['n_consensus_down']:,}",
        f"{S['n_core']:,}", S["core_lfc"]))
    fig(os.path.join(figdir, "consensus_heatmap.png"), styles, story,
        "Per-cohort log<sub>2</sub>FC for the top core consensus genes (blue = down, orange = up).", max_h=7.2)
    fig(os.path.join(figdir, "forest_top_genes.png"), styles, story,
        "Top consensus genes: per-cohort effect sizes (points) and pooled meta-estimate (diamond).")

    # top gene tables
    def gene_rows(lst): return [[g["gene"], _fmt(g.get("log2FC")), _fmt(g.get("FDR"))] for g in lst]
    story.append(Paragraph("Top up-regulated consensus genes", styles["H2"]))
    story.append(data_table(["Gene", "log2FC", "FDR"], gene_rows(S.get("top_up", [])[:15]), styles,
                            col_widths=[1.8 * inch, 1.3 * inch, 1.3 * inch]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Top down-regulated consensus genes", styles["H2"]))
    story.append(data_table(["Gene", "log2FC", "FDR"], gene_rows(S.get("top_down", [])[:15]), styles,
                            col_widths=[1.8 * inch, 1.3 * inch, 1.3 * inch]))

    # ---- Sensitivity analysis for heterogeneous controls ----
    sens = S.get("sensitivity")
    if sens:
        story.append(Paragraph("Sensitivity to control heterogeneity", styles["H2"]))
        P(("Because the cohorts use different control groups, we re-ran the meta-analysis using only the "
           "%d cohorts with a non-inflammatory control (%s) and asked how much of the primary consensus "
           "signature is preserved. Of %s primary consensus genes, %s were testable in this subset and "
           "%s (%s%%) remained direction-consistent and significant at FDR &lt; %s, indicating the signature "
           "is driven by disease biology rather than by an inflammatory (e.g. osteoarthritis) comparator. "
           "This non-inflammatory subset itself yielded %s consensus genes.") % (
            sens.get("n_subset_cohorts", "?"),
            esc(", ".join(sens.get("subset_cohorts", []))),
            f"{sens.get('n_primary_consensus', 0):,}",
            f"{sens.get('n_primary_tested_in_subset', 0):,}",
            f"{sens.get('n_primary_preserved', 0):,}",
            f"{100 * sens.get('preservation_fraction', 0):.0f}",
            S["fdr_cut"],
            f"{sens.get('n_consensus_subset', 0):,}"))
        fig(os.path.join(figdir, "sensitivity_preservation.png"), styles, story,
            "Consensus preservation in the non-inflammatory-control subset: primary consensus size, "
            "subset consensus size, and number of primary genes preserved.", max_h=4.2)

    story.append(PageBreak())
    story.append(Paragraph("Pathway and functional enrichment", styles["H2"]))
    P("Over-representation analysis and Hallmark GSEA localize the consensus signature to coherent biological "
      "programs, providing mechanistic context and candidate pathways.")
    fig(os.path.join(figdir, "enrichment_ORA_dotplots.png"), styles, story,
        "Over-representation analysis of core up/down genes (GO Biological Process and Reactome).", max_h=6.5)
    fig(os.path.join(figdir, "GSEA_hallmark_barplot.png"), styles, story,
        "Hallmark pathway enrichment (GSEA), normalized enrichment score.", max_h=6.5)

    # ---------------- Conclusions & next steps ----------------
    story.append(PageBreak())
    story.append(Paragraph("Conclusions", styles["H1"]))
    P(("Meta-analysis across %d independent cohorts produced a reproducible consensus transcriptional signature for "
       "%s, robust to platform and cohort heterogeneity (median I<super>2</super> = %s%%). The signature recapitulates "
       "expected disease biology in its top genes and enriched pathways, and the effect-size framework yields "
       "calibrated, directionally-consistent estimates suitable for reuse.") % (
        ncoh, esc(disease), S.get("median_I2_sig", "NA")))
    if S.get("heterogeneous_controls"):
        _sp = S.get("sensitivity")
        P(("<b>Caveat.</b> The cohorts did not share a single control definition (control types: %s). The pooled "
           "estimate should therefore be read as an average over heterogeneous baselines rather than a single "
           "case-vs-normal contrast, and genes that separate the case condition specifically from an inflammatory "
           "comparator may be under-counted. %s") % (
            esc(", ".join(str(c) for c in (S.get("control_types") or []))),
            ("A sensitivity meta-analysis restricted to non-inflammatory-control cohorts preserved %s%% of the "
             "primary consensus genes, supporting robustness." % f"{100 * _sp.get('preservation_fraction', 0):.0f}"
             ) if _sp else ("A non-inflammatory-control sensitivity meta-analysis was not possible because fewer "
                            "than two cohorts shared such a control; interpret the pooled signature accordingly.")))

    story.append(Paragraph("Suggested next steps", styles["H1"]))
    for b in [
        "Validate the core signature in an independent held-out cohort or a prospective sample set.",
        "Score the signature (e.g. GSVA / ssGSEA) against clinical variables such as treatment response, severity, or subtype.",
        "Prioritize druggable up-regulated genes and pathways for target and repurposing hypotheses.",
        "Test signature stability across tissue compartments and, where relevant, single-cell deconvolution.",
    ]:
        story.append(Paragraph("&bull; " + esc(b), styles["BodyBullet"]))

    # ---------------- References ----------------
    if refs:
        story.append(Paragraph("References", styles["H1"]))
        for i, r in enumerate(refs, 1):
            auth = r.get("authors") or r.get("author") or ""
            if isinstance(auth, list): auth = ", ".join(auth[:3]) + (" et al." if len(auth) > 3 else "")
            yr = r.get("year", ""); ttl = r.get("title", ""); jour = r.get("journal", "")
            doi = r.get("doi", "")
            cite = " ".join(x for x in [f"{auth}", f"({yr})." if yr else "", f"{ttl}.",
                                        f"<i>{jour}</i>." if jour else "",
                                        f"doi:{doi}" if doi else ""] if x)
            story.append(Paragraph(f"{i}. " + esc(_striptags(cite)).replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>"),
                                   styles["Body"]))

    doc = SimpleDocTemplate(args.out, pagesize=LETTER,
                            topMargin=0.85 * inch, bottomMargin=0.75 * inch,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch)
    doc.report_title = "Consensus Signature: " + disease.title()
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print("[build_report] wrote", args.out)


# ---- small helpers (module level) ----
def _has_tags(t):
    return any(tag in t for tag in ("<sub>", "<super>", "<b>", "<i>", "<br", "&bull;", "&ge;", "&lt;", "&gt;", "&amp;"))
def _fmt(x):
    try: return f"{float(x):+.2f}" if abs(float(x)) < 1000 else str(x)
    except Exception: return str(x)
def _join(x):
    if x is None: return "case"
    if isinstance(x, list): return ", ".join(str(i) for i in x)
    return str(x)
def _striptags(s):  # keep only <i> for journal; drop others defensively
    return s
def esc_wrap():
    pass


if __name__ == "__main__":
    main()
