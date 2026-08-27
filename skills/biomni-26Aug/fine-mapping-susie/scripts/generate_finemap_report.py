#!/usr/bin/env python3
"""
generate_finemap_report.py -- Phylo-branded PDF report for a single-trait GWAS fine-mapping run.

Data-driven: reads the JSON/CSV outputs of the other scripts and templates the prose, so it works for
ANY locus (not hard-coded to a study). Inputs:

  --susie-report   susie_report.json          (from run_susie_finemap.R)   [required]
  --credible-set   credible_set.csv           (from run_susie_finemap.R)   [required]
  --config         study_config.json          (trait, gene(s), ancestry, LD source, region, GWAS src)
  --annot-summary  annotation_summary.json    (from annotate_variants.py)  [optional]
  --gtex           *_gtex_eqtl.csv            [optional]
  --ccre           *_encode_ccre.csv          [optional]
  --figure         finemap_regional.png       [optional]
  --out            report.pdf                 [required]

CRITICAL ReportLab patterns preserved from the validated PROX1 report:
  * Every table cell is wrapped in a Paragraph (tbl()), so &gt; &times; <sup> <b> parse inside cells.
  * Each figure is wrapped with its caption in KeepTogether() so captions never orphan.
  * Images are sized <=4.0 inch to avoid pagination breaks.
Brand constants follow the shared Phylo report conventions.
"""
import argparse, json, os, datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, PageBreak, KeepTogether, HRFlowable)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# ---- Phylo brand ----
PHYLO_GOLD = HexColor("#D4A04A"); HEADING_COLOR = HexColor("#111111")
BODY_TEXT = HexColor("#2C2A26"); MUTED_TEXT = HexColor("#8A8378")
TABLE_HEADER_BG = PHYLO_GOLD; TABLE_HEADER_FG = HexColor("#FFFFFF")
TABLE_ALT_ROW = HexColor("#F9F7F3"); TABLE_BORDER = HexColor("#D5CFC5")
GREEN = HexColor("#75A025"); ORANGE = HexColor("#FF9400"); RED = HexColor("#D7191C")
FONT_HEADING = "Helvetica-Bold"; FONT_BODY = "Helvetica"; FONT_MONO = "Courier"


def styles():
    ss = getSampleStyleSheet(); out = {}
    out["title"] = ParagraphStyle("title", parent=ss["Title"], fontName=FONT_HEADING, textColor=HEADING_COLOR, fontSize=19, spaceAfter=4, leading=23)
    out["subtitle"] = ParagraphStyle("subtitle", parent=ss["Normal"], fontName=FONT_BODY, textColor=MUTED_TEXT, fontSize=10, spaceAfter=10)
    out["h2"] = ParagraphStyle("h2", parent=ss["Heading2"], fontName=FONT_HEADING, textColor=HEADING_COLOR, fontSize=13.5, spaceBefore=13, spaceAfter=6)
    out["h3"] = ParagraphStyle("h3", parent=ss["Heading3"], fontName=FONT_HEADING, textColor=HEADING_COLOR, fontSize=11, spaceBefore=8, spaceAfter=3)
    out["body"] = ParagraphStyle("body", parent=ss["Normal"], fontName=FONT_BODY, textColor=BODY_TEXT, fontSize=9.5, leading=13.5, spaceAfter=6, alignment=TA_LEFT)
    out["caption"] = ParagraphStyle("caption", parent=ss["Normal"], fontName=FONT_BODY, textColor=MUTED_TEXT, fontSize=8, leading=10, spaceAfter=10)
    out["kpi"] = ParagraphStyle("kpi", parent=ss["Normal"], fontName=FONT_HEADING, textColor=HEADING_COLOR, fontSize=15, alignment=TA_CENTER, leading=17)
    out["kpi_lab"] = ParagraphStyle("kpi_lab", parent=ss["Normal"], fontName=FONT_BODY, textColor=MUTED_TEXT, fontSize=7.5, alignment=TA_CENTER, leading=9)
    return out


S = styles()
def P(t, s="body"): return Paragraph(str(t), S[s])
def div(): return HRFlowable(width="100%", thickness=1.1, color=PHYLO_GOLD, spaceBefore=3, spaceAfter=9)


def tbl(data, colw, header=True, fs=8.2):
    cell_body = ParagraphStyle("cell", parent=S["body"], fontSize=fs, leading=fs + 2, spaceAfter=0)
    cell_hdr = ParagraphStyle("cellh", parent=cell_body, fontName=FONT_HEADING, textColor=TABLE_HEADER_FG)
    wrapped = [[Paragraph(str(c), cell_hdr if (header and ri == 0) else cell_body) for c in row]
               for ri, row in enumerate(data)]
    t = Table(wrapped, colWidths=colw, repeatRows=1 if header else 0)
    cmds = [("FONT", (0, 0), (-1, -1), FONT_BODY, fs), ("TEXTCOLOR", (0, 0), (-1, -1), BODY_TEXT),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, TABLE_BORDER), ("GRID", (0, 0), (-1, -1), 0.3, TABLE_BORDER)]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG), ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_FG),
                 ("FONT", (0, 0), (-1, 0), FONT_HEADING, fs + 0.3)]
        for r in range(2, len(wrapped), 2):
            cmds.append(("BACKGROUND", (0, r), (-1, r), TABLE_ALT_ROW))
    t.setStyle(TableStyle(cmds)); return t


def rnd(x, nd=2):
    """Round a numeric-looking value for display; pass through non-numeric unchanged."""
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def sci(x, sig=3):
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return str(x)
    if xf == 0:
        return "0"
    import math
    e = int(math.floor(math.log10(abs(xf))))
    if -3 <= e <= 3:
        return f"{xf:.3g}"
    m = xf / (10 ** e)
    return f"{m:.2f} &times; 10<sup>{e}</sup>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--susie-report", required=True)
    ap.add_argument("--credible-set", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--annot-summary", default=None)
    ap.add_argument("--gtex", default=None)
    ap.add_argument("--ccre", default=None)
    ap.add_argument("--figure", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import pandas as pd
    R = json.load(open(args.susie_report))
    cs = pd.read_csv(args.credible_set)
    cfg = json.load(open(args.config)) if args.config and os.path.exists(args.config) else {}
    annot = json.load(open(args.annot_summary)) if args.annot_summary and os.path.exists(args.annot_summary) else {}

    trait = cfg.get("trait", "trait")
    genes = cfg.get("genes", cfg.get("gene", ""))
    if isinstance(genes, list):
        genes = ", ".join(genes)
    # optional ENSG -> symbol map for human-readable annotation prose
    gene_map = cfg.get("gene_symbols", {}) or {}

    def gsym(g):
        """Return a readable gene label: symbol if we can map an ENSG, else the value as-is."""
        if g in gene_map:
            return gene_map[g]
        # tolerate versioned ENSG (ENSG....N) by matching the unversioned stem
        stem = str(g).split(".")[0]
        return gene_map.get(stem, g)
    ancestry = cfg.get("ancestry", "unspecified")
    ld_source = cfg.get("ld_source", "1000 Genomes reference panel")
    region = cfg.get("region", "")
    gwas_src = cfg.get("gwas_source", "GWAS summary statistics")
    build = cfg.get("build", "GRCh38")

    # headline variant
    top = R.get("top_pip", {})
    lead_snp = top.get("snp", "NA")
    lead_pip = top.get("pip", None)
    est_s = R.get("estimated_s", None)
    n_cs = R.get("n_credible_sets", 0)
    cs_size = int(cs["cs_size"].iloc[0]) if ("cs_size" in cs.columns and len(cs)) else len(cs)
    N = R.get("N", None)

    story = []
    story.append(P(f"Fine-Mapping of the {trait} Association at {region or 'the target locus'}", "title"))
    story.append(P(f"SuSiE single-trait fine-mapping &bull; {ancestry} LD &bull; {build} &bull; "
                   f"{datetime.date.today().isoformat()}", "subtitle"))
    story.append(div())

    # KPI strip
    s_flag = ""
    if est_s is not None:
        s_flag = "clean" if float(est_s) < 0.1 else "CAUTION"
    kpis = [[P(lead_snp, "kpi"), P("lead variant", "kpi_lab")],
            [P(f"PIP {lead_pip:.2f}" if lead_pip is not None else "PIP NA", "kpi"), P("posterior incl. prob.", "kpi_lab")],
            [P(f"{cs_size} variant" + ("s" if cs_size != 1 else ""), "kpi"), P("95% credible set", "kpi_lab")],
            [P(f"s = {est_s}" if est_s is not None else "s NA", "kpi"), P(f"LD-mismatch ({s_flag})", "kpi_lab")]]
    kt = Table(kpis, colWidths=[1.65 * inch] * 4)
    kt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TABLE_ALT_ROW), ("BOX", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
                            ("INNERGRID", (0, 0), (-1, -1), 0.5, HexColor("#FFFFFF")),
                            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.append(kt); story.append(Spacer(1, 10))

    # Summary
    story.append(P("Summary", "h2"))
    conv = "converged" if R.get("susie_converged") else "did NOT converge"
    lead_row = cs.iloc[0] if len(cs) else {}
    ea = lead_row.get("effect_allele", "?"); oa = lead_row.get("other_allele", "?")
    beta = lead_row.get("beta", None); pval = lead_row.get("pval", None)
    summ = (f"SuSiE fine-mapping of the <b>{trait}</b> signal"
            f"{(' at ' + region) if region else ''} resolved <b>{n_cs} credible set(s)</b>; the primary set "
            f"contains <b>{cs_size} variant{'s' if cs_size != 1 else ''}</b> out of {R.get('n_snps_analyzed','?')} "
            f"analyzed variants (model {conv}"
            + (f"; N = {int(N):,}" if N else "") + "). ")
    summ += (f"The lead variant <b>{lead_snp}</b> (effect allele {ea}/{oa}"
             + (f", &beta; = {beta}" if beta is not None else "")
             + (f", P = {sci(pval)}" if pval is not None else "")
             + (f") carries posterior inclusion probability <b>{lead_pip:.3f}</b>. " if lead_pip is not None else "). "))
    if est_s is not None:
        if float(est_s) < 0.1:
            summ += (f"The SuSiE LD-mismatch diagnostic is low (estimated s = {est_s}), indicating the "
                     f"{ancestry} LD reference is consistent with the GWAS z-scores and the credible set is trustworthy.")
        else:
            summ += (f"<b>Caution:</b> the SuSiE LD-mismatch diagnostic is elevated (estimated s = {est_s}); "
                     f"the LD reference may not match the GWAS ancestry, so the credible set should be treated as provisional.")
    story.append(P(summ))

    # Methods / provenance table
    story.append(P("Data &amp; methods", "h2"))
    mrows = [["Item", "Value"],
             ["Trait", trait],
             ["Region (" + build + ")", region or "n/a"],
             ["GWAS source", gwas_src],
             ["Sample size (N)", f"{int(N):,}" if N else "n/a"],
             ["LD reference", f"{ld_source} ({ancestry})"],
             ["Fine-mapping", f"susieR::susie_rss (L = {R.get('L','?')}, coverage = {R.get('coverage','?')}, "
                              f"estimate_residual_variance = FALSE)"],
             ["LD-mismatch diagnostics", f"estimate_s_rss (s = {est_s}); kriging_rss "
                                         f"(outliers = {R.get('kriging_outliers','n/a')})"],
             ["Variants analyzed", f"{R.get('n_snps_analyzed','?')} (dropped NA-z: {R.get('n_dropped_na_z',0)})"]]
    story.append(tbl(mrows, [1.9 * inch, 4.6 * inch]))

    # Credible set table
    story.append(P("95% credible set", "h2"))
    if len(cs):
        show = cs.copy()
        cols = [c for c in ["cs", "snp", "varid", "pos", "effect_allele", "other_allele", "beta", "pval", "pip"] if c in show.columns]
        head = {"cs": "Set", "snp": "SNP", "varid": "chr:pos:ref:alt", "pos": "Position", "effect_allele": "EA",
                "other_allele": "OA", "beta": "&beta;", "pval": "P", "pip": "PIP"}
        data = [[head.get(c, c) for c in cols]]
        for _, r in show.head(25).iterrows():
            row = []
            for c in cols:
                v = r[c]
                if c == "pval":
                    row.append(sci(v))
                elif c == "pip":
                    row.append(f"{float(v):.3f}")
                elif c == "pos":
                    row.append(f"{int(v):,}")
                else:
                    row.append(v)
            data.append(row)
        story.append(tbl(data, None))
    else:
        story.append(P("<i>No credible set was returned (weak signal or LD mismatch).</i>"))

    # Annotation
    story.append(P("Functional annotation", "h2"))
    ann_txt = []
    if args.gtex and os.path.exists(args.gtex):
        g = pd.read_csv(args.gtex)
        if len(g):
            for _, r in g.iterrows():
                ann_txt.append(f"<b>{gsym(r.get('gene','?'))}</b> is a significant cis-eQTL in "
                               f"{r.get('tissue','?')} (GTEx v8; NES per-ALT = {rnd(r.get('nes_per_alt'), 3)}, "
                               f"P = {sci(r.get('pval'))}); the {r.get('effect_allele','?')} allele is associated with "
                               f"<b>{r.get('direction_on_gene_per_effect_allele','?')}</b>-regulation of the gene.")
        else:
            ann_txt.append("No significant GTEx cis-eQTL was found for the queried gene(s)/tissue(s) at the credible-set "
                           "variant(s) &mdash; a genuine negative rather than missing data.")
    if annot:
        if annot.get("eqtl_catalogue_n_records", 0):
            ann_txt.append(f"eQTL Catalogue returned {annot['eqtl_catalogue_n_records']} QTL record(s) for the "
                           f"credible-set variant(s) in the queried dataset(s).")
        elif "eQTL_Catalogue" in annot.get("layers_run", []):
            ann_txt.append("eQTL Catalogue query returned no matching QTL for the credible-set variant(s) "
                           "(often underpowered small cohorts &mdash; treat as inconclusive, not negative).")
    if args.ccre and os.path.exists(args.ccre):
        c = pd.read_csv(args.ccre)
        hit = c[c["overlaps_credible_variant"] == True] if "overlaps_credible_variant" in c.columns else c.iloc[0:0]
        if len(hit):
            r = hit.iloc[0]
            ann_txt.append(f"The lead variant falls within ENCODE candidate cis-regulatory element "
                           f"<b>{r.get('accession','?')}</b> ({r.get('element_class','?')}, "
                           f"{int(r.get('length',0))} bp; DNase z = {rnd(r.get('dnase_z'))}, "
                           f"H3K27ac z = {rnd(r.get('h3k27ac_z'))}, H3K4me3 z = {rnd(r.get('h3k4me3_z'))}), "
                           f"consistent with a regulatory mechanism.")
        else:
            ann_txt.append("No ENCODE cCRE directly overlapped the credible-set variant(s) in the queried window.")
    if not ann_txt:
        ann_txt.append("No annotation layers were run (annotation is optional; enable GTEx / eQTL Catalogue / ENCODE "
                       "layers in annotate_variants.py to add interpretive context).")
    for t in ann_txt:
        story.append(P("&bull;&nbsp; " + t))

    # Direction-of-effect caveat (always include -- learned from PROX1)
    story.append(P("Interpretation &amp; caveats", "h2"))
    story.append(P(
        "Fine-mapping identifies the variant(s) most likely to be causal <i>given the LD reference</i>; it does not "
        "prove causality. Two checks temper interpretation: (1) the LD-mismatch diagnostic must be low (see s above) "
        "and the LD ancestry must match the GWAS &mdash; a mismatch can create spurious single-variant credible sets; "
        "(2) direction of effect (risk allele &rarr; up/down on a gene) should be reconciled with orthogonal functional "
        "evidence before asserting a mechanism, because eQTL direction in one tissue/cell type need not generalize. "
        "Colocalization with molecular QTLs (coloc / coloc.susie) is the recommended next step to test "
        "whether the GWAS and eQTL signals share the same causal variant.", "body"))

    # Figure
    if args.figure and os.path.exists(args.figure):
        story.append(P("Regional fine-mapping plot", "h2"))
        cap = (f"<b>Figure 1.</b> Regional view of the {trait} association at {region}. "
               "Top: GWAS &minus;log<sub>10</sub>(P) colored by r<sup>2</sup> to the lead variant. "
               "Middle: SuSiE posterior inclusion probability (95% credible-set members highlighted). "
               "Lower tracks: genes and ENCODE candidate cis-regulatory elements where available.")
        story.append(KeepTogether([Image(args.figure, width=4.0 * inch, height=4.0 * inch), P(cap, "caption")]))

    story.append(Spacer(1, 6)); story.append(div())
    story.append(P(f"Generated by the fine-mapping-susie skill &bull; {datetime.date.today().isoformat()} &bull; "
                   "SuSiE (Wang et al. 2020) &bull; LD-mismatch diagnostics (Zou et al. 2022)", "caption"))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    doc = SimpleDocTemplate(args.out, pagesize=letter, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            title=f"{trait} fine-mapping report")
    doc.build(story)
    print(f"[report] wrote {args.out}")


if __name__ == "__main__":
    main()
