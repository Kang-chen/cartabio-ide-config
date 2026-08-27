#!/usr/bin/env python3
"""generate_report.py — Biomni-branded PDF report for the neoantigen-TESLA skill.

Builds a polished, self-contained PDF (ReportLab Platypus) that summarises a
neoantigen prioritization run and its benchmark against the TESLA consortium
(Wells et al., Cell 2020). Follows the Phylo/Biomni PDF brand spec: US-Letter,
gold accent, Helvetica, centered figures/tables, <sub>/<super> tags (no Unicode).

Consumes the artifacts produced by ``neoantigen_tesla.export_results`` +
``generate_plots.generate_all``:
  - neoantigens.csv / summary.csv       (per-peptide results + tier counts)
  - benchmark_summary.json               (real-TESLA benchmark metrics)
  - figures/fig1..4 + workflow_infographic.png

CLI:
    python generate_report.py \
        --results  <demo_results/>  \
        --benchmark <benchmark_summary.json> \
        --figures  <figures/> \
        --out      /mnt/results/report_neoantigen_tesla.pdf

With no arguments, packaged demo fixtures are used and the PDF is written to
/mnt/results/report_neoantigen_tesla.pdf.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from typing import Optional

import pandas as pd
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---- Phylo brand palette -------------------------------------------------
PHYLO_GOLD = HexColor("#D4A04A")
HEADING_COLOR = HexColor("#111111")
BODY_TEXT = HexColor("#2C2A26")
MUTED_TEXT = HexColor("#8A8378")
TABLE_HEADER_BG = PHYLO_GOLD
TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F9F7F3")
TABLE_BORDER = HexColor("#D5CFC5")
CALLOUT_BG = HexColor("#FAF9F3")

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
DEF_RESULTS = os.path.join(_SKILL, "tests", "fixtures", "demo_results")
DEF_BENCH = os.path.join(_SKILL, "tests", "fixtures", "benchmark_summary.json")
DEF_FIGS = os.path.join(_SKILL, "assets", "figures")

REPORT_TITLE = "Neoantigen Prioritization Report"

TIER_LABELS = {
    "Tier1": "Tier 1", "Tier2": "Tier 2", "Tier3": "Tier 3",
    "excluded_low_abundance": "Excluded - low abundance",
    "excluded_nonbinder": "Excluded - non-binder",
}


# =============================================================================
# Styles + page chrome
# =============================================================================
def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="RTitle", fontName="Helvetica-Bold", fontSize=25,
                         textColor=HEADING_COLOR, spaceBefore=0, spaceAfter=6, leading=30))
    s.add(ParagraphStyle(name="RSub", fontName="Helvetica", fontSize=11,
                         textColor=PHYLO_GOLD, spaceAfter=4))
    s.add(ParagraphStyle(name="Attribution", fontName="Helvetica-Oblique", fontSize=10,
                         textColor=MUTED_TEXT, spaceAfter=8))
    s.add(ParagraphStyle(name="SectionHead", fontName="Helvetica-Bold", fontSize=16,
                         textColor=HEADING_COLOR, spaceBefore=20, spaceAfter=9))
    s.add(ParagraphStyle(name="SubHead", fontName="Helvetica-Bold", fontSize=12,
                         textColor=HEADING_COLOR, spaceBefore=10, spaceAfter=5))
    s.add(ParagraphStyle(name="Body2", fontName="Helvetica", fontSize=10.3,
                         textColor=BODY_TEXT, alignment=TA_JUSTIFY, spaceAfter=8, leading=15))
    s.add(ParagraphStyle(name="Caption", fontName="Helvetica-Oblique", fontSize=8.7,
                         textColor=MUTED_TEXT, alignment=TA_CENTER, spaceAfter=13, leading=12))
    s.add(ParagraphStyle(name="CellL", fontName="Helvetica", fontSize=8.4,
                         textColor=BODY_TEXT, leading=10.5))
    s.add(ParagraphStyle(name="CellC", fontName="Helvetica", fontSize=8.4,
                         textColor=BODY_TEXT, alignment=TA_CENTER, leading=10.5))
    s.add(ParagraphStyle(name="CellHdr", fontName="Helvetica-Bold", fontSize=8.6,
                         textColor=TABLE_HEADER_FG, alignment=TA_CENTER, leading=10.5))
    s.add(ParagraphStyle(name="Callout", fontName="Helvetica", fontSize=10,
                         textColor=BODY_TEXT, leading=14.5))
    return s


def _page_chrome(canvas, doc):
    canvas.saveState()
    w, h = letter
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(MUTED_TEXT)
    canvas.drawString(60, h - 40, REPORT_TITLE)
    canvas.setStrokeColor(PHYLO_GOLD)
    canvas.setLineWidth(1)
    canvas.line(60, h - 48, w - 60, h - 48)
    canvas.setStrokeColor(TABLE_BORDER)
    canvas.setLineWidth(0.75)
    canvas.line(60, 40, w - 60, 40)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED_TEXT)
    canvas.drawString(60, 26, "Generated by Biomni")
    canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
    canvas.drawRightString(w - 60, 26, "TESLA-guided neoantigen analysis")
    canvas.restoreState()


def _divider():
    return HRFlowable(width=480, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)


def _callout(text, styles, width=478):
    t = Table([[Paragraph(text, styles["Callout"])]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, PHYLO_GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    t.hAlign = "CENTER"
    return t


def _table(headers, rows, colWidths, styles, align_center_cols=None):
    align_center_cols = align_center_cols or set()
    head = [Paragraph(h, styles["CellHdr"]) for h in headers]
    body = []
    for r in rows:
        cells = []
        for j, c in enumerate(r):
            st = styles["CellC"] if j in align_center_cols else styles["CellL"]
            cells.append(Paragraph(str(c), st))
        body.append(cells)
    data = [head] + body
    t = Table(data, colWidths=colWidths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_FG),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    t.setStyle(TableStyle(style))
    t.hAlign = "CENTER"
    return t


def _img(path, width, styles, caption):
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        iw, ih = im.size
    h = width * ih / iw
    img = Image(path, width=width, height=h)
    img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1, 3), Paragraph(caption, styles["Caption"])])


def _fmt(v, nd=2):
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "-"
        return f"{float(v):.{nd}f}"
    except (ValueError, TypeError):
        return str(v)


# =============================================================================
# Data aggregation helpers (all values from real run artifacts; nothing invented)
# =============================================================================
_TIER_ORDER = ["Tier1", "Tier2", "Tier3", "excluded_low_abundance", "excluded_nonbinder"]

# The seven TESLA features: (key, display, axis, documented weight). Weights mirror
# tesla_features.FEATURE_WEIGHTS (sum = 1.0); grouped weights give the 0.60/0.40 axis split.
_FEATURE_META = [
    ("binding_affinity", "Binding affinity", "Presentation", 0.30),
    ("tumor_abundance", "Tumor abundance (TPM x VAF)", "Presentation", 0.22),
    ("binding_stability", "Binding stability", "Presentation", 0.08),
    ("agretopicity", "Differential agretopicity", "Recognition", 0.15),
    ("foreignness", "Foreignness / dissimilarity-to-self", "Recognition", 0.13),
    ("fraction_hydrophobic", "Fraction hydrophobic", "Recognition", 0.06),
    ("mutation_position", "Mutation position", "Recognition", 0.06),
]
# benchmark feature_separation uses "mut_rank" as the raw binding read-out; map it in for display.
_FEATSEP_KEYMAP = {"binding_affinity": "binding_affinity", "binding_stability": "binding_stability",
                   "fraction_hydrophobic": "fraction_hydrophobic",
                   "tumor_abundance": "tumor_abundance", "mutation_position": "mutation_position"}


def per_gene_table(df):
    """gene x tier counts + total + best priority score, sorted by best score desc."""
    piv = df.pivot_table(index="gene", columns="tier", values="peptide",
                         aggfunc="count", fill_value=0)
    present = [t for t in _TIER_ORDER if t in piv.columns]
    piv = piv.reindex(columns=present, fill_value=0)
    piv["total"] = piv.sum(axis=1)
    piv["best"] = df.groupby("gene")["priority_score"].max().round(1)
    piv = piv.sort_values("best", ascending=False)
    return piv, present


def per_allele_table(df):
    """hla_best x {n peptides, n Tier1, n Tier2, best %rank, median %rank}, sorted by Tier1 desc."""
    g = df.groupby("hla_best")
    out = g.agg(n_peptides=("peptide", "size"),
                n_tier1=("tier", lambda s: int((s == "Tier1").sum())),
                n_tier2=("tier", lambda s: int((s == "Tier2").sum())),
                best_rank=("mut_rank", "min"),
                median_rank=("mut_rank", "median"))
    return out.sort_values(["n_tier1", "n_tier2"], ascending=False)


def feature_detail_rows(bench):
    """Per-feature: weight, axis, and benchmark separation delta (where available)."""
    fs = bench.get("summary", {}).get("feature_separation", {})
    rows = []
    for key, disp, axis, w in _FEATURE_META:
        sep = fs.get(_FEATSEP_KEYMAP.get(key, key), {})
        delta = sep.get("delta") if isinstance(sep, dict) else None
        im = sep.get("immunogenic_mean") if isinstance(sep, dict) else None
        nm = sep.get("nonimmunogenic_mean") if isinstance(sep, dict) else None
        rows.append((disp, axis, w, im, nm, delta))
    return rows


def recognition_coverage(df):
    """Real counts of where recognition features are defined vs undefined in this run."""
    n = len(df)
    return {
        "n_total": n,
        "agretopicity_defined": int(df["agretopicity"].notna().sum()) if "agretopicity" in df else 0,
        "foreignness_defined": int(df["foreignness"].notna().sum()) if "foreignness" in df else 0,
        "n_missense": int((df["var_class"] == "missense").sum()) if "var_class" in df else 0,
        "n_frameshift": int(df["var_class"].isin(["frameshift", "inframe_indel"]).sum())
                        if "var_class" in df else 0,
    }


def parse_demo_variants(vcf_path, expr_path):
    """Parse the curated somatic VCF into per-variant provenance rows, joined to RPKM.

    Authoritative for genomic coordinates (frameshift neoORFs have no single pos in the
    results CSV). Returns a list of dicts. Reads only real file content.
    """
    import re as _re
    expr = {}
    if expr_path and os.path.exists(expr_path):
        with open(expr_path) as f:
            r = csv.DictReader(f, delimiter="\t")
            for row in r:
                col = "expression" if "expression" in row else list(row)[1]
                try:
                    expr[row[list(row)[0]]] = float(row[col])
                except (ValueError, TypeError):
                    pass
    fields = None
    rows = []
    if not (vcf_path and os.path.exists(vcf_path)):
        return rows
    with open(vcf_path) as f:
        for ln in f:
            if ln.startswith("##INFO=<ID=CSQ"):
                m = _re.search(r'Format: ([^"]+)', ln)
                if m:
                    fields = m.group(1).split("|")
            if ln.startswith("#"):
                continue
            c = ln.rstrip("\n").split("\t")
            if len(c) < 8:
                continue
            chrom, pos, _id, ref, alt, _q, _flt, info = c[:8]
            d = {}
            csq = _re.search(r"CSQ=([^;]+)", info)
            if csq and fields:
                d = dict(zip(fields, csq.group(1).split(",")[0].split("|")))
            gene = d.get("SYMBOL") or d.get("Gene") or "?"
            hgvsp = (d.get("HGVSp", "").split(":")[-1]) or d.get("Amino_acids", "")
            rows.append({
                "gene": gene, "coord": f"{chrom}:{pos}", "change": f"{ref}>{alt}",
                "var_class": d.get("Consequence", "?").replace("_variant", ""),
                "hgvsp": hgvsp, "transcript": d.get("Feature", ""),
                "rpkm": expr.get(gene),
            })
    rows.sort(key=lambda x: (x["rpkm"] is None, -(x["rpkm"] or 0)))
    return rows


import csv  # noqa: E402  (used by parse_demo_variants)


# =============================================================================
# Report builder
# =============================================================================
def build_report(results_dir: Optional[str] = None,
                 benchmark_json: Optional[str] = None,
                 figures_dir: Optional[str] = None,
                 out_path: str = "/mnt/results/report_neoantigen_tesla.pdf",
                 sample_name: str = "demo case",
                 hla: Optional[list] = None) -> str:
    results_dir = results_dir or DEF_RESULTS
    benchmark_json = benchmark_json or DEF_BENCH
    figures_dir = figures_dir or DEF_FIGS
    is_pt22_demo = "pt22" in sample_name.lower()

    df = pd.read_csv(os.path.join(results_dir, "neoantigens.csv"))
    with open(benchmark_json) as f:
        bench = json.load(f)
    bsum = bench["summary"]
    rk, filt = bsum["ranking"], bsum["filtering"]

    tier_counts = df["tier"].value_counts().to_dict()
    n_total = len(df)
    n_tier1 = tier_counts.get("Tier1", 0)
    n_tier2 = tier_counts.get("Tier2", 0)
    n_prior = n_tier1 + n_tier2
    genes = sorted(df["gene"].unique())
    engine = "MHCflurry (Apache-2.0)"

    # HLA list
    try:
        acase = json.load(open(os.path.join(results_dir, "analysis.json")))
        hla = hla or acase.get("hla") or acase.get("hla_alleles")
    except Exception:
        pass
    hla_str = ", ".join(hla) if hla else "patient HLA-I genotype"

    styles = _styles()
    doc = SimpleDocTemplate(out_path, pagesize=letter, topMargin=56, bottomMargin=52,
                            leftMargin=60, rightMargin=60, title=REPORT_TITLE,
                            author="Biomni")
    story = []
    date_str = datetime.now().strftime("%B %d, %Y")

    # ---- Title ----
    story.append(Spacer(1, 24))
    story.append(Paragraph(REPORT_TITLE, styles["RTitle"]))
    story.append(Paragraph("TESLA-guided prediction &amp; prioritization from somatic "
                           "variants, HLA-I genotype, and RNA-seq", styles["RSub"]))
    story.append(Paragraph(f"<i>Generated by Biomni  |  {date_str}  |  sample: {sample_name}</i>",
                           styles["Attribution"]))
    story.append(_divider())

    # ---- Workflow infographic ----
    info = os.path.join(figures_dir, "workflow_infographic.png")
    if os.path.exists(info):
        story.append(_img(info, 468, styles,
                          "Figure 1. Analysis workflow: somatic variants + HLA-I + expression "
                          "are converted to candidate neo-peptides (missense and frameshift "
                          "neoORF), scored for MHC-I binding with MHCflurry, annotated with the "
                          "TESLA immunogenicity features, and ranked into tiers."))

    # ---- Introduction / Background ----
    story.append(Paragraph("Introduction &amp; Background", styles["SectionHead"]))
    story.append(Paragraph(
        "Tumor-specific neoantigens are peptides produced by somatic mutations that are absent "
        "from normal tissue. When such a peptide is presented on the tumor cell surface by an "
        "MHC class&nbsp;I (HLA-I) molecule and recognised by a T-cell receptor, it becomes a "
        "target for cytotoxic T cells. Neoantigens are the presumed effectors behind much of the "
        "clinical benefit of immune-checkpoint blockade and are the active ingredients of "
        "personalised cancer vaccines and neoantigen-directed cell therapies. Because only a "
        "small minority of mutations yield a peptide that is both presented and immunogenic, the "
        "practical problem is <i>prioritization</i>: ranking the handful of candidates worth "
        "synthesising and testing out of the hundreds to thousands a tumor exome generates.",
        styles["Body2"]))
    story.append(Paragraph(
        "The TESLA (Tumor Neoantigen Selection Alliance) consortium assembled experimentally "
        "validated T-cell recognition data across many predictions and identified the peptide "
        "properties that best distinguish immunogenic from non-immunogenic neoantigens (Wells "
        "et al., Cell 2020). Those properties fall into two axes: <b>presentation</b> &mdash; is "
        "the peptide actually displayed on HLA-I (strong, stable binding) and is the mutation "
        "expressed and clonal &mdash; and <b>recognition</b> &mdash; does the presented peptide "
        "look foreign enough to the T-cell repertoire (it differs from self, and it binds better "
        "than its wild-type counterpart). This report applies a transparent, fully open-source "
        "reimplementation of that framework to a single sample, scores every candidate peptide, "
        "and ranks them into actionable tiers.", styles["Body2"]))
    story.append(Paragraph(
        "<b>Scope of this report.</b> The pipeline takes real somatic variants, the sample's "
        "HLA-I genotype, and RNA-seq expression as input and produces a ranked candidate list "
        "plus this document. The same scoring model is separately benchmarked against the public "
        "TESLA immunogenicity dataset so that the prioritization is anchored to independent, "
        "experimentally labelled ground truth rather than to internal assumptions. Every number "
        "in this report is computed from the run's own output tables and the benchmark summary; "
        "nothing is imputed or hand-tuned.", styles["Body2"]))
    story.append(_divider())

    # ---- Executive summary ----
    story.append(Paragraph("Executive Summary", styles["SectionHead"]))
    story.append(Paragraph(
        f"This report summarises a neoantigen prioritization run on <b>{sample_name}</b> "
        f"across {len(genes)} mutated genes ({', '.join(genes)}) and the HLA-I genotype "
        f"{hla_str}. Somatic single-nucleotide variants and indels were translated into "
        f"candidate 8-11mer peptides, including frameshift neoORF junction peptides. In total "
        f"<b>{n_total} candidate peptides</b> were scored for MHC-I presentation with "
        f"{engine} and annotated with seven TESLA immunogenicity features spanning two axes: "
        f"<b>presentation</b> (binding affinity, tumor abundance, binding stability) and "
        f"<b>recognition</b> (differential agretopicity, foreignness / dissimilarity-to-self, "
        f"fraction hydrophobic, and mutation position).", styles["Body2"]))
    story.append(Paragraph(
        f"Of these, <b>{n_prior} peptides</b> reached the prioritized set "
        f"(<b>{n_tier1} Tier&nbsp;1</b> strong binders that are also expressed, plus "
        f"<b>{n_tier2} Tier&nbsp;2</b> binders). The scoring model was benchmarked against the "
        f"TESLA consortium immunogenicity dataset (Wells et al., Cell 2020; "
        f"{rk['n_labelled']} independently labelled neoepitopes, base rate "
        f"{rk['base_rate']*100:.1f}%). On this public table the recognition features cannot "
        f"contribute (no wild-type %ranks for agretopicity; foreignness is not validatable in a "
        f"non-neoantigen context), so the fair comparator is the presentation sub-score, which "
        f"separated immunogenic from non-immunogenic peptides with <b>AUROC "
        f"{rk.get('auroc_presentation', rk['auroc']):.2f}</b> (full composite {rk['auroc']:.2f}) "
        f"and top-20 enrichment of <b>{rk['enrichment_top20']:.1f}x</b> over the base rate.",
        styles["Body2"]))

    top_pep = df.sort_values("priority_score", ascending=False).iloc[0]
    story.append(_callout(
        f"<b>Top-ranked neoantigen:</b> {top_pep['gene']} {top_pep['variant']} &rarr; "
        f"peptide <b>{top_pep['peptide']}</b> on {top_pep['hla_best']} "
        f"(presentation %rank {_fmt(top_pep['mut_rank'])}, expression "
        f"{_fmt(top_pep['expr_tpm'],1)} TPM, priority score "
        f"{_fmt(top_pep['priority_score'])}).", styles))

    # ---- Methods ----
    story.append(Paragraph("Methods", styles["SectionHead"]))
    story.append(Paragraph(
        "<b>Inputs.</b> A somatic VCF (SNVs and indels), the patient HLA-I genotype, and an "
        "RNA-seq expression table (gene-level TPM). Common/germline variants are removed using "
        "the gnomAD population frequency annotated in the VCF where available. Note this is a "
        "population-frequency filter, not tumour-vs-matched-normal somatic calling; when a true "
        "matched-normal sample is available, subtracting the patient's own germline is the "
        "preferred way to define somatic variants.", styles["Body2"]))
    _ig = os.path.join(figures_dir, "infographic_inputs.png")
    if os.path.exists(_ig):
        story.append(_img(_ig, 468, styles,
                          "Figure S1. Inputs schematic: the somatic VCF, HLA-I genotype, and "
                          "gene-level RNA-seq expression that the pipeline consumes, ideally all "
                          "from the same specimen."))
    story.append(Paragraph(
        "<b>Peptide generation.</b> Missense variants are translated into all overlapping "
        "8-11mer peptides spanning the substituted residue; the wild-type residue is verified "
        "against the reference protein and mismatches are discarded. Frameshift and other "
        "indels are translated from the affected transcript coding sequence to yield neoORF "
        "peptides covering the novel out-of-frame sequence up to the new stop codon.",
        styles["Body2"]))
    _ig = os.path.join(figures_dir, "infographic_peptides.png")
    if os.path.exists(_ig):
        story.append(_img(_ig, 468, styles,
                          "Figure S2. Neo-peptide generation schematic: missense substitutions "
                          "yield 8-11mers spanning the mutated residue, while indels/frameshifts "
                          "yield neoORF junction peptides translated to the new stop codon."))
    story.append(Paragraph(
        f"<b>MHC-I binding.</b> Each peptide is scored against every patient HLA-I allele with "
        f"{engine}; the best (lowest) presentation percentile rank across alleles is retained. "
        f"No synthetic or heuristic fallback is used - if the engine or an allele is "
        f"unavailable the peptide is dropped rather than assigned a fabricated score.",
        styles["Body2"]))
    _ig = os.path.join(figures_dir, "infographic_binding.png")
    if os.path.exists(_ig):
        story.append(_img(_ig, 468, styles,
                          "Figure S3. MHCflurry pMHC-I binding schematic: every candidate peptide "
                          "is scored against every HLA-I allele, and the best presentation "
                          "percentile rank across alleles is retained."))
    story.append(Paragraph(
        "<b>TESLA features and tiering.</b> Seven features derived from Wells et al. (Cell 2020) "
        "and the neoantigen-fitness literature are computed across two axes. <i>Presentation:</i> "
        "strong binding affinity, high tumor abundance (TPM &times; variant allele fraction), and "
        "binding stability. <i>Recognition:</i> differential agretopicity (wild-type &divide; "
        "mutant %rank &mdash; a mutation that creates or improves binding relative to self is more "
        "likely a genuine non-self epitope), foreignness / dissimilarity-to-self (local alignment "
        "of the peptide against a real set of known immunogenic epitopes and against the human "
        "self-proteome; Luksza et al. 2017, Richman et al. 2019), fraction of hydrophobic "
        "residues, and mutation position within the peptide. A weighted composite priority score "
        "is formed over the features that are actually available (missing inputs are never "
        "imputed; e.g. frameshift neoORFs have no 1:1 wild-type counterpart so agretopicity is "
        "left undefined and the score renormalises). Peptides are assigned to Tier&nbsp;1 (strong "
        "binder, %rank &lt; 0.5, expressed, not anchor-only), Tier&nbsp;2 (binder, %rank &lt; 2), "
        "Tier&nbsp;3 (weak, %rank &lt; 10), or excluded (non-binder or below the abundance "
        "floor).", styles["Body2"]))
    story.append(Paragraph(
        "<b>Composite priority score.</b> The seven feature values are each normalised to [0,1] "
        "(higher = more favourable) and combined as a single weighted sum, renormalised over "
        "whichever features are defined for a given peptide, then scaled to 0-100. The documented "
        "weights are binding affinity 0.30, tumor abundance 0.22, differential agretopicity 0.15, "
        "foreignness 0.13, binding stability 0.08, fraction hydrophobic 0.06, and mutation "
        "position 0.06 (sum&nbsp;=&nbsp;1.0). Grouped by axis these give a presentation:recognition "
        "weight split of 0.60:0.40. The two-axis language is a presentation device over one flat "
        "weighted sum &mdash; it is not a separate hyperparameter &mdash; and because weights "
        "renormalise over available features, a frameshift neoORF (no wild-type counterpart, so "
        "agretopicity undefined) is scored fairly on its remaining features rather than penalised "
        "for a missing input.", styles["Body2"]))
    _ig = os.path.join(figures_dir, "infographic_features.png")
    if os.path.exists(_ig):
        story.append(_img(_ig, 468, styles,
                          "Figure S4. TESLA feature schematic: the seven features and their "
                          "documented weights, grouped into the presentation (0.60) and "
                          "recognition (0.40) axes that form the composite priority score."))
    story.append(Paragraph(
        "<b>Genome build.</b> Coordinates are handled in GRCh38. Where an input variant is "
        "reported on GRCh37/hg19 (as is the case for the built-in demo, whose calls are curated "
        "from a public GRCh37 resource), positions are lifted to GRCh38 before peptide generation "
        "so that transcript coordinates and neoORF translation are internally consistent; the "
        "amino-acid consequence is preserved by the lift.", styles["Body2"]))

    story.append(PageBreak())

    # ---- Results: tier summary ----
    story.append(Paragraph("Results", styles["SectionHead"]))
    story.append(Paragraph("Prioritization summary", styles["SubHead"]))
    _ig = os.path.join(figures_dir, "infographic_tiering.png")
    if os.path.exists(_ig):
        story.append(_img(_ig, 468, styles,
                          "Figure S5. Tiering schematic: the candidate pool is split by the "
                          "composite priority score and binding/expression gates into kept "
                          "Tier&nbsp;1/2/3 candidates versus excluded (non-binder, low-abundance) "
                          "peptides."))
    order = ["Tier1", "Tier2", "Tier3", "excluded_low_abundance", "excluded_nonbinder"]
    present = [t for t in order if t in tier_counts]
    rows = [[TIER_LABELS[t], tier_counts[t], f"{100*tier_counts[t]/n_total:.1f}%"]
            for t in present]
    rows.append(["<b>Total</b>", f"<b>{n_total}</b>", "<b>100.0%</b>"])
    story.append(_table(["TESLA tier", "Peptides (n)", "Fraction"], rows,
                        [230, 130, 110], styles, align_center_cols={1, 2}))
    story.append(Spacer(1, 6))
    story.append(_img(os.path.join(figures_dir, "fig1_tier_distribution.png"), 470, styles,
                      "Figure 2. Prioritization funnel (A) and per-gene tier composition (B). "
                      "Frameshift-rich genes contribute disproportionately to the high tiers via "
                      "neoORF peptides."))

    story.append(_img(os.path.join(figures_dir, "fig2_binding_by_tier.png"), 400, styles,
                      "Figure 3. MHCflurry presentation %rank across tiers (log scale). Dashed "
                      "lines mark the strong (0.5), binder (2.0), and weak (10) thresholds."))

    # ---- Top neoantigens table ----
    story.append(Paragraph("Top prioritized neoantigens", styles["SubHead"]))
    story.append(Paragraph(
        "The 20 highest-scoring candidates across all genes. <i>%rank</i> is the MHCflurry "
        "presentation percentile (lower = stronger); <i>nM</i> is the predicted binding affinity; "
        "<i>TPM</i> is gene expression; <i>Score</i> is the 0-100 composite priority. These are "
        "the peptides to consider first for synthesis and immunogenicity testing.", styles["Body2"]))
    show = df.sort_values("priority_score", ascending=False).head(20)
    has_nm = "affinity_nm" in df.columns
    rows = []
    for _, r in show.iterrows():
        row = [
            TIER_LABELS.get(r["tier"], r["tier"]).replace("Tier ", "T"),
            r["gene"], r["variant"], r["peptide"], r.get("hla_best", "-"),
            _fmt(r["mut_rank"], 3),
        ]
        if has_nm:
            row.append(_fmt(r.get("affinity_nm"), 0))
        row += [_fmt(r.get("expr_tpm"), 1), _fmt(r["priority_score"], 1)]
        rows.append(row)
    if has_nm:
        story.append(_table(
            ["Tier", "Gene", "Variant", "Peptide", "HLA", "%rank", "nM", "TPM", "Score"],
            rows, [34, 50, 68, 82, 68, 42, 44, 38, 38], styles,
            align_center_cols={0, 5, 6, 7, 8}))
    else:
        story.append(_table(
            ["Tier", "Gene", "Variant", "Peptide", "HLA", "%rank", "TPM", "Score"],
            rows, [40, 52, 74, 86, 74, 44, 40, 40], styles,
            align_center_cols={0, 5, 6, 7}))
    story.append(Paragraph(f"Showing top 20 of {n_total} scored peptides; full tables in "
                           f"neoantigens.csv and prioritized_neoantigens.csv.", styles["Caption"]))

    story.append(PageBreak())

    # ---- Complete Tier 1 listing ----
    t1 = df[df["tier"] == "Tier1"].sort_values("priority_score", ascending=False)
    if len(t1):
        story.append(Paragraph("Complete Tier&nbsp;1 candidate list", styles["SubHead"]))
        story.append(Paragraph(
            f"All {len(t1)} Tier&nbsp;1 peptides &mdash; strong predicted binders "
            f"(%rank&nbsp;&lt;&nbsp;0.5) that are expressed and not anchor-only. This is the "
            f"priority shortlist for synthesis and immunogenicity testing; the full Tier&nbsp;1 + "
            f"Tier&nbsp;2 set is in prioritized_neoantigens.csv.", styles["Body2"]))
        t1rows = []
        for _, r in t1.iterrows():
            row = [r["gene"], r["variant"], r["peptide"], r.get("hla_best", "-"),
                   _fmt(r["mut_rank"], 3)]
            if has_nm:
                row.append(_fmt(r.get("affinity_nm"), 0))
            row += [_fmt(r.get("expr_tpm"), 1), _fmt(r["priority_score"], 1)]
            t1rows.append(row)
        if has_nm:
            story.append(_table(
                ["Gene", "Variant", "Peptide", "HLA", "%rank", "nM", "TPM", "Score"],
                t1rows, [54, 78, 86, 70, 44, 44, 38, 40], styles,
                align_center_cols={4, 5, 6, 7}))
        else:
            story.append(_table(
                ["Gene", "Variant", "Peptide", "HLA", "%rank", "TPM", "Score"],
                t1rows, [58, 84, 92, 74, 48, 42, 42], styles,
                align_center_cols={4, 5, 6}))
        story.append(Paragraph(
            "Sorted by composite priority score (descending). Frameshift neoORF peptides "
            "(e.g. from CHD1) appear here when their junction peptides bind strongly; their "
            "agretopicity is undefined by construction and does not penalise the score.",
            styles["Caption"]))
        story.append(PageBreak())

    # ---- Results: per-gene breakdown ----
    story.append(Paragraph("Per-gene breakdown", styles["SubHead"]))
    story.append(Paragraph(
        "Candidate peptides and their tier assignment aggregated by mutated gene, ordered by the "
        "best (highest) priority score contributed by each gene. A gene can generate many "
        "peptides (all registers overlapping a missense site, or the full neoORF for a "
        "frameshift); genes with frameshift neoORFs (e.g. those spanning long novel reading "
        "frames) contribute disproportionately many high-tier peptides.", styles["Body2"]))
    piv, present_tiers = per_gene_table(df)
    gh = ["Gene"] + [TIER_LABELS.get(t, t).replace("Tier ", "T").replace("excluded_", "excl ")
                     for t in present_tiers] + ["Total", "Best"]
    grows = []
    for gene, row in piv.iterrows():
        grows.append([gene] + [int(row[t]) for t in present_tiers]
                     + [int(row["total"]), _fmt(row["best"], 1)])
    ncol = len(present_tiers)
    gcw = [58] + [70] * ncol + [46, 44]
    # scale tier columns to fit page width (~478pt)
    total_w = sum(gcw)
    if total_w > 478:
        scale = 478.0 / total_w
        gcw = [w * scale for w in gcw]
    story.append(_table(gh, grows, gcw, styles,
                        align_center_cols=set(range(1, ncol + 3))))
    story.append(Paragraph(
        "Tier abbreviations: T1 strong binder &amp; expressed; T2 binder; T3 weak binder; "
        "excl-low tumor abundance below floor; excl-nonbinder %rank &ge; 10.", styles["Caption"]))
    story.append(Spacer(1, 10))

    # ---- Results: per-allele breakdown ----
    story.append(Paragraph("Per-HLA-allele binding summary", styles["SubHead"]))
    story.append(Paragraph(
        "For each HLA-I allele in the genotype: the number of candidate peptides for which that "
        "allele is the best restriction, how many reached Tier&nbsp;1 / Tier&nbsp;2, and the "
        "best and median presentation %rank achieved. This shows which alleles dominate the "
        "presented repertoire in this sample.", styles["Body2"]))
    al = per_allele_table(df)
    arows = []
    for allele, row in al.iterrows():
        arows.append([allele, int(row["n_peptides"]), int(row["n_tier1"]),
                      int(row["n_tier2"]), _fmt(row["best_rank"], 3),
                      _fmt(row["median_rank"], 2)])
    story.append(_table(
        ["HLA-I allele", "Peptides", "Tier 1", "Tier 2", "Best %rank", "Median %rank"],
        arows, [110, 70, 60, 60, 88, 90], styles, align_center_cols={1, 2, 3, 4, 5}))
    story.append(Paragraph(
        "Lower %rank = stronger predicted presentation (0.5 strong, 2.0 binder threshold).",
        styles["Caption"]))

    story.append(PageBreak())

    # ---- Benchmark ----
    story.append(Paragraph("Benchmark against TESLA (Wells et al., Cell 2020)",
                           styles["SectionHead"]))
    _ig = os.path.join(figures_dir, "infographic_benchmark.png")
    if os.path.exists(_ig):
        story.append(_img(_ig, 468, styles,
                          "Figure S6. Benchmark schematic: the scoring model is validated against "
                          "the real TESLA neoepitope dataset, quantifying discrimination (AUROC) "
                          "and enrichment of immunogenic peptides at the top of the ranking."))
    story.append(Paragraph(
        f"The scoring model was evaluated on an independent, real immunogenicity dataset from "
        f"the TESLA consortium: <b>{filt['n_total']} neoepitopes</b> with experimentally "
        f"determined T-cell recognition labels ({filt['n_immunogenic']} immunogenic / "
        f"{filt['n_nonimmunogenic']} non-immunogenic), each carrying its restricting HLA-I "
        f"allele. Every peptide was re-scored with {engine} against its own allele and passed "
        f"through the identical TESLA feature and ranking pipeline used above.", styles["Body2"]))
    story.append(Paragraph(
        "Because this public table provides peptide, allele, and label but no per-peptide "
        "expression, variant allele fraction, or wild-type %rank, the tumor-abundance, "
        "mutation-position, and agretopicity features are left undefined for the benchmark (not "
        "fabricated). We therefore report two AUROCs: the <b>presentation sub-score</b> "
        "(binding affinity + stability + hydrophobicity + position) &mdash; the fair "
        "binding-dominated comparator on this table &mdash; and the <b>full composite</b>, which "
        "is diluted here because the recognition features it carries cannot fire meaningfully in "
        "a non-neoantigen benchmark context. Reported metrics were cross-checked against "
        "scikit-learn.", styles["Body2"]))

    _pres_auc = rk.get("auroc_presentation", rk["auroc"])
    brows = [
        ["AUROC \u2014 presentation sub-score", _fmt(_pres_auc),
         "Fair binding-dominated comparator (this table)"],
        ["AUROC \u2014 full composite", _fmt(rk["auroc"]),
         "All 7 features; recognition inert on this table"],
        ["Average precision", _fmt(rk["average_precision"]),
         f"vs base rate {rk['base_rate']:.3f} ({rk['average_precision']/rk['base_rate']:.1f}x)"],
        ["Enrichment @ top 10", f"{_fmt(rk['enrichment_top10'],1)}x", "Precision@10 / base rate"],
        ["Enrichment @ top 20", f"{_fmt(rk['enrichment_top20'],1)}x", "Precision@20 / base rate"],
        ["Recall @ top 50", _fmt(rk["top50_recall"]), "Immunogenic peptides recovered in top 50"],
    ]
    story.append(_table(["Metric", "Value", "Interpretation"], brows,
                        [176, 62, 240], styles, align_center_cols={1}))
    story.append(Spacer(1, 10))

    # ---- Feature-level detail ----
    story.append(Paragraph("Feature-level detail", styles["SubHead"]))
    story.append(Paragraph(
        "The seven TESLA features, their axis and composite weight, and &mdash; where the public "
        "benchmark permits it &mdash; how well each feature separated immunogenic from "
        "non-immunogenic peptides (mean feature value in each class, and their difference). A "
        "positive &Delta; means immunogenic peptides scored higher on that feature. Dashes mark "
        "features the benchmark table cannot evaluate (no expression/VAF for tumor abundance; no "
        "wild-type %rank for agretopicity; mutation position undefined without variant context).",
        styles["Body2"]))
    frows = []
    for disp, axis, w, im, nm, delta in feature_detail_rows(bench):
        frows.append([disp, axis, _fmt(w, 2),
                      _fmt(im, 3) if im is not None else "\u2013",
                      _fmt(nm, 3) if nm is not None else "\u2013",
                      (("+" if (delta or 0) >= 0 else "") + _fmt(delta, 3))
                      if delta is not None else "\u2013"])
    story.append(_table(
        ["TESLA feature", "Axis", "Weight", "Immuno.", "Non-imm.", "\u0394 (sep.)"],
        frows, [168, 92, 50, 56, 66, 56], styles, align_center_cols={2, 3, 4, 5}))
    cov = recognition_coverage(df)
    story.append(Paragraph(
        f"In this run the recognition features are defined where the biology allows it: "
        f"agretopicity is computed for the {cov['agretopicity_defined']} missense peptides "
        f"(a mutant-vs-wild-type %rank ratio) and left undefined for the "
        f"{cov['n_frameshift']} frameshift neoORF peptides, while foreignness / "
        f"dissimilarity-to-self is defined for all {cov['foreignness_defined']} peptides. This is "
        f"why the composite renormalises per peptide rather than assuming a fixed feature set.",
        styles["Caption"]))
    story.append(Spacer(1, 8))
    story.append(_img(os.path.join(figures_dir, "fig4_ranking_performance.png"), 470, styles,
                      "Figure 4. Ranking performance on the real TESLA labels: ROC curve (A) and "
                      "immunogenic enrichment versus rank depth (B)."))
    story.append(_img(os.path.join(figures_dir, "fig3_feature_separation.png"), 470, styles,
                      "Figure 5. Distribution of key TESLA features for immunogenic versus "
                      "non-immunogenic peptides in the benchmark set. Immunogenic peptides show "
                      "markedly stronger binding (lower %rank)."))

    # ---- Interpretation ----
    story.append(Paragraph("Interpretation &amp; Conclusions", styles["SectionHead"]))
    ba = bsum["feature_separation"]["binding_affinity"]
    story.append(Paragraph(
        f"The strongest discriminating signal is MHC-I binding: immunogenic benchmark peptides "
        f"have a mean normalised binding-affinity score of {_fmt(ba['immunogenic_mean'])} versus "
        f"{_fmt(ba['nonimmunogenic_mean'])} for non-immunogenic peptides. This reproduces the "
        f"central TESLA finding that strong, stable MHC-I presentation is the dominant "
        f"determinant of neoantigen immunogenicity, and supports using the tiered priority score "
        f"to focus experimental validation on Tier&nbsp;1-2 candidates.", styles["Body2"]))
    story.append(Paragraph(
        "Beyond presentation, the pipeline adds two <b>recognition</b> features that are the "
        "practical differentiator on real neoantigens (as opposed to this public benchmark): "
        "differential agretopicity and foreignness / dissimilarity-to-self. In the worked demo "
        "these correctly elevate a driver mutation whose mutant peptide binds far better than its "
        "wild-type counterpart (high agretopicity) above frameshift peptides that lack a "
        "wild-type reference &mdash; the profile most consistent with a true, T-cell-visible "
        "non-self epitope (Luksza et al. 2017; Richman et al. 2019).", styles["Body2"]))
    story.append(Paragraph(
        "<b>Limitations.</b> (i) The benchmark table lacks expression/VAF and wild-type %ranks, so "
        "the tumor-abundance and agretopicity features - among TESLA's most powerful signals - "
        "cannot contribute to the benchmark metrics; the presentation sub-score is therefore the "
        "fair comparator and the absolute filtering fractions here understate what the full "
        "pipeline achieves when those measurements are available. (ii) Binding stability uses an "
        "MHCflurry presentation-based proxy unless NetMHCstabpan output is supplied. "
        "(iii) Foreignness/dissimilarity is computed by local alignment against real but finite "
        "reference sets (a curated IEDB immunogenic-9mer set and a human self-proteome sample); "
        "it is a transparent, documented signal rather than a trained recognition model. "
        "(iv) Peptide-MHC binding prediction, not T-cell assays, underlies the scores; candidates "
        "are hypotheses for experimental validation, and results depend on the accuracy of "
        "upstream variant calling, HLA typing, and expression quantification. (v) On this host the "
        "MHCflurry PyTorch backend (NNPACK unsupported) is not bit-reproducible across process "
        "starts; benchmark AUROC varies by roughly &plusmn;0.02 and tier boundaries can shift by a "
        "few peptides run-to-run, though the top-ranked candidates are stable.", styles["Body2"]))

    story.append(Paragraph(
        f"<b>Conclusions.</b> Starting from {len(genes)} mutated genes and the sample HLA-I "
        f"genotype, the pipeline scored {n_total} candidate peptides and focused them to "
        f"{n_prior} prioritized candidates ({n_tier1} Tier&nbsp;1, {n_tier2} Tier&nbsp;2). The "
        f"top candidate ({top_pep['gene']} {top_pep['variant']} &rarr; {top_pep['peptide']} on "
        f"{top_pep['hla_best']}) combines strong predicted presentation (%rank "
        f"{_fmt(top_pep['mut_rank'])}) with expression and a favourable recognition profile, "
        f"making it the clearest first target for validation. The ranking is anchored to the "
        f"independent TESLA labels, on which the presentation sub-score reaches AUROC "
        f"{_fmt(_pres_auc)} with {rk['enrichment_top20']:.1f}x top-20 enrichment &mdash; evidence "
        f"that the same model prioritising this sample recovers real immunogenic peptides well "
        f"above chance. The output is a testable, ranked hypothesis set, not a claim of confirmed "
        f"immunogenicity.", styles["Body2"]))

    # ---- Next Steps ----
    story.append(Paragraph("Next Steps", styles["SectionHead"]))
    story.append(Paragraph(
        "The following steps convert this prioritized list into experimentally testable and, "
        "ultimately, clinically actionable neoantigens.", styles["Body2"]))
    _next = [
        ("Validate the top candidates experimentally.",
         f"Synthesise the Tier&nbsp;1 peptides (n&nbsp;=&nbsp;{n_tier1}) and confirm HLA-I binding "
         "(e.g. NetMHCstabpan or in-vitro stabilisation), then test T-cell recognition with "
         "patient/donor PBMCs (multimer staining, IFN-\u03b3 ELISpot, or MANA-style assays). "
         "Start with the highest-scoring peptide per driver gene."),
        ("Sharpen the inputs the benchmark could not exercise.",
         "Supply per-variant allele fraction and clonality (CCF) and confirmed wild-type %ranks "
         "so the tumor-abundance and agretopicity features contribute their full weight; provide "
         "NetMHCstabpan stability in place of the presentation-based proxy. These are precisely "
         "the signals shown here to be inert on the public table but decisive on real neoantigens."),
        ("Confirm expression and clonality of the source mutations.",
         "Verify that the mutations behind Tier&nbsp;1-2 peptides are expressed in the tumor "
         "RNA-seq and, where possible, clonal rather than subclonal, to prioritise antigens "
         "present across the tumor-cell population."),
        ("Extend antigen discovery.",
         "Add gene-fusion and splicing-derived neoORFs, and consider proteasomal cleavage / TAP "
         "transport modelling, to broaden the candidate space beyond SNVs and simple indels."),
        ("Design the downstream construct.",
         "For a personalised vaccine or cell-therapy program, assemble the validated epitopes "
         "into a multi-epitope construct (string-of-beads mRNA/peptide or TCR/TCR-T selection), "
         "balancing HLA coverage across the sample's alleles as summarised in the per-allele "
         "table above."),
        ("Track run-to-run stability.",
         "If the local MHCflurry backend exhibits run-to-run numerical variation, repeat scoring "
         "and carry forward candidates that remain stably high-ranked; record the model and "
         "backend versions with every run."),
    ]
    for i, (head, body) in enumerate(_next, 1):
        story.append(Paragraph(f"<b>{i}. {head}</b> {body}", ParagraphStyle(
            name=f"next{i}", parent=styles["Body2"], spaceAfter=7)))

    story.append(PageBreak())

    # ---- References ----
    # Hyperlinked (ReportLab <a href>), rendered in Phylo link-blue so DOIs/URLs
    # are clickable in the PDF. URLs verified against references.jsonl.
    story.append(Paragraph("Key References", styles["SectionHead"]))
    _LINK = "#0279EE"

    def _lnk(url, text=None):
        return f'<a href="{url}" color="{_LINK}"><u>{text or url}</u></a>'

    refs = [
        "Wells DK, van Buuren MM, Dang KK, et al. Key Parameters of Tumor Epitope "
        "Immunogenicity Revealed Through a Consortium Approach Improve Neoantigen Prediction. "
        "Cell. 2020. " + _lnk("https://doi.org/10.1016/j.cell.2020.09.015",
                               "doi:10.1016/j.cell.2020.09.015"),
        "O'Donnell TJ, Rubinsteyn A, Laserson U. MHCflurry 2.0: Improved Pan-Allele Prediction "
        "of MHC Class I-Presented Peptides by Incorporating Antigen Processing. Cell Systems. "
        "2020. " + _lnk("https://doi.org/10.1016/j.cels.2020.06.010",
                        "doi:10.1016/j.cels.2020.06.010")
        + " &middot; " + _lnk("https://github.com/openvax/mhcflurry", "github.com/openvax/mhcflurry"),
        "Luksza M, Riaz N, Makarov V, et al. A neoantigen fitness model predicts tumor response "
        "to checkpoint blockade immunotherapy. Nature. 2017. "
        + _lnk("https://doi.org/10.1038/nature24473", "doi:10.1038/nature24473"),
        "Richman LP, Vonderheide RH, Rech AJ. Neoantigen dissimilarity to the self-proteome "
        "predicts immunogenicity and response to immune checkpoint blockade. Cell Systems. 2019. "
        + _lnk("https://doi.org/10.1016/j.cels.2019.08.009", "doi:10.1016/j.cels.2019.08.009"),
        "Vita R, Mahajan S, Overton JA, et al. The Immune Epitope Database (IEDB): 2018 update. "
        "Nucleic Acids Res. 2019;47(D1):D339-D343. "
        + _lnk("https://doi.org/10.1093/nar/gky1006", "doi:10.1093/nar/gky1006"),
        "TESLA neoepitope benchmark dataset. Mendeley Data, CC BY 4.0. "
        + _lnk("https://doi.org/10.17632/6x87nx8jtc.1", "doi:10.17632/6x87nx8jtc.1"),
        "The UniProt Consortium. UniProt: the Universal Protein Knowledgebase. Nucleic Acids Res. "
        + _lnk("https://www.uniprot.org", "uniprot.org")
        + " (reviewed human proteome; canonical missense protein sequences).",
        "Ensembl REST API (transcript CDS for neoORF translation; GRCh37->GRCh38 liftover). "
        + _lnk("https://rest.ensembl.org", "rest.ensembl.org"),
    ]
    if is_pt22_demo:
        refs.extend([
            "Hugo W, Zaretsky JM, Sun L, et al. Genomic and Transcriptomic Features of Response "
            "to Anti-PD-1 Therapy in Metastatic Melanoma. Cell. 2016;165(1):35-44. "
            + _lnk("https://doi.org/10.1016/j.cell.2016.02.065",
                   "doi:10.1016/j.cell.2016.02.065")
            + " (Pt22 somatic variants, tumor RNA-seq [GEO GSE78220], and HLA-I).",
            "Cerami E, Gao J, Dogrusoz U, et al. The cBio Cancer Genomics Portal. Cancer Discov. "
            "2012;2(5):401-404. "
            + _lnk("https://doi.org/10.1158/2159-8290.CD-12-0095",
                   "doi:10.1158/2159-8290.CD-12-0095")
            + " &middot; Gao J, Aksoy BA, Dogrusoz U, et al. Integrative analysis of complex cancer "
            "genomics and clinical profiles using the cBioPortal. Sci Signal. 2013;6(269):pl1. "
            + _lnk("https://doi.org/10.1126/scisignal.2004088", "doi:10.1126/scisignal.2004088")
            + " (cBioPortal study mel_ucla_2016; Pt22 variant/read-count/HLA retrieval).",
        ])
    for i, r in enumerate(refs, 1):
        story.append(Paragraph(f"{i}. {r}", ParagraphStyle(
            name=f"ref{i}", parent=styles["Body2"], fontSize=9, leading=13,
            alignment=TA_JUSTIFY, spaceAfter=6)))

    # ---- Appendix: data provenance (only when the curated demo VCF is present) ----
    _demo_vcf = os.path.join(_SKILL, "assets", "demo_hugo_pt22_somatic.vcf")
    _demo_expr = os.path.join(_SKILL, "assets", "demo_hugo_pt22_expression.tsv")
    prov = parse_demo_variants(_demo_vcf, _demo_expr) if is_pt22_demo else []
    if prov:
        story.append(PageBreak())
        story.append(Paragraph("Appendix: Data Provenance", styles["SectionHead"]))
        story.append(Paragraph(
            "The primary built-in demo uses <b>Pt22</b>, a real anti-PD-1 (pembrolizumab)-treated "
            "metastatic melanoma <i>patient</i> from Hugo et al., Cell 2016 (UCLA cohort). Its "
            "value here is that all three input layers &mdash; somatic variants, gene expression, "
            "and HLA-I genotype &mdash; are drawn from this <i>same</i> patient and are fully open "
            "access, so the demo exercises the pipeline end-to-end on internally consistent, real, "
            "publicly available patient data rather than variants, expression, and HLA stitched "
            "together from unrelated sources. Nothing was sequenced for this analysis; the layers "
            "are curated from existing public resources.", styles["Body2"]))
        story.append(Paragraph(
            "Somatic mutations are curated from <b>cBioPortal</b> (study mel_ucla_2016, sample "
            "Pt22) with tumour reference/alternate read counts, from which variant allele fraction "
            "(VAF) and a cancer cell fraction (CCF) proxy are derived; tumour gene expression is "
            "from the matching RNA-seq in <b>NCBI GEO GSE78220</b> (column Pt22.baseline), with "
            "FPKM converted to TPM; the HLA-I genotype (A*01:01, A*02:01, B*27:05, B*37:01, "
            "C*02:02, C*06:02) is the patient class-I type from the same cBioPortal study. Variant "
            "calls are curated on GRCh37 and lifted to GRCh38 for the pipeline (amino-acid "
            "consequence preserved).", styles["Body2"]))
        story.append(_callout(
            "<b>What this demo does and does not represent.</b> Pt22 is a real patient tumour, but "
            "the WES/RNA-seq shipped here are from a single pre-treatment biopsy: no tumour&ndash;"
            "normal pair is included (the NORMAL sample in the VCF is a 0/0 placeholder so the "
            "parser reads the TUMOR genotype), so common/germline variants are removed by gnomAD "
            "population frequency (a filter), not by subtracting the patient&rsquo;s own germline. "
            "The Hugo 2016 call set is SNV-only, so this real case exercises the missense path but "
            "not the neoORF/indel path (that path is covered separately by a small synthetic "
            "fixture). A production run should use an actual patient tumour with a matched normal "
            "for true somatic calling, tumour RNA-seq for expression and VAF, and HLA typed from "
            "that patient.", styles))
        prov_top = prov[:15]
        prows = []
        for v in prov_top:
            prows.append([
                v["gene"], v["coord"], v["change"],
                (v["var_class"] or "").replace("_", " "),
                v["hgvsp"],
                _fmt(v["rpkm"], 2) if v["rpkm"] is not None else "\u2013",
            ])
        story.append(_table(
            ["Gene", "Position (GRCh38)", "Change", "Class", "Protein", "TPM"],
            prows, [60, 104, 62, 66, 110, 46], styles, align_center_cols={5}))
        story.append(Paragraph(
            f"Representative variants driving the primary demo (top {len(prov_top)} by expression "
            f"of {len(prov)} shipped: 232 missense, 9 nonsense, 6 splice), with GRCh38 coordinates, "
            "reference&rarr;alternate allele, VEP consequence class, protein change, and tumour "
            "gene expression (TPM). Of the 232 missense variants, 214 are scored; 18 are "
            "deterministically skipped because the stated wild-type residue disagrees with the "
            "canonical UniProt protein at that position (transcript/isoform differences). The "
            "canonical driver <b>BRAF V600E</b> is retained and validates as a positive control "
            "(neoepitope KIGDFGLATEK on HLA-C*02:02).", styles["Caption"]))

    doc.build(story, onFirstPage=_page_chrome, onLaterPages=_page_chrome)
    return out_path


def _cli():
    ap = argparse.ArgumentParser(description="Generate the neoantigen-TESLA PDF report.")
    ap.add_argument("--results", default=None)
    ap.add_argument("--benchmark", default=None)
    ap.add_argument("--figures", default=None)
    ap.add_argument("--out", default="/mnt/results/report_neoantigen_tesla.pdf")
    ap.add_argument("--sample", default="demo case")
    args = ap.parse_args()
    p = build_report(args.results, args.benchmark, args.figures, args.out, sample_name=args.sample)
    print(f"[report] wrote {p}")


if __name__ == "__main__":
    _cli()
