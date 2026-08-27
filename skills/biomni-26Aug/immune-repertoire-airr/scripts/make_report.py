#!/usr/bin/env python3
# =============================================================================
# Phylo-branded PDF report for an immune-repertoire analysis (TCR/BCR, AIRR).
#
# Consumes the outputs of repertoire_analysis.R:
#   <OUT_DIR>/analysis_metrics.json   (receptor, chain, modality, paths, ranges)
#   <OUT_DIR>/tables/*.csv            (sample_summary, diversity_metrics, ...)
#   <OUT_DIR>/figures/*.png           (clonality, diversity, gene usage, overlap)
# Optional:
#   <OUT_DIR>/citations.json          (list of {n,text} from LiteratureSearch)
#   <OUT_DIR>/infographic.png         (schematic summary; generate with GenerateImage)
#
# Report framing (Methods/Results/diversity caption) ADAPTS to modality:
#   single_cell -> Chao1 flagged as extrapolation artifact; trust evenness+raref
#   bulk        -> Chao1 trusted; clonal expansion reported as real biology
# and to receptor (BCR adds SHM/isotype caveats; exact-CDR3 != lineage).
#
# Follows the pdf-report-generation skill: Phylo palette, Helvetica fonts,
# KeepTogether for figure/table+caption, validation gate at the end.
#
# USAGE:  python3 make_report.py \
#           --out-dir /mnt/results/repertoire \
#           --pdf /mnt/results/report_repertoire_analysis.pdf
# =============================================================================
import os, json, argparse, datetime
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, PageBreak, HRFlowable, KeepTogether)

# ---- Phylo brand palette (see pdf-report-generation skill) ----
PHYLO_GOLD    = HexColor("#D4A04A")
HEADING       = HexColor("#111111")
BODY          = HexColor("#2C2A26")
MUTED         = HexColor("#8A8378")
TABLE_HDR_BG  = PHYLO_GOLD
TABLE_HDR_FG  = HexColor("#FFFFFF")
TABLE_ALT     = HexColor("#F9F7F3")
TABLE_BORDER  = HexColor("#D5CFC5")

def build_styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H1x", parent=ss["Heading1"], fontName="Helvetica-Bold",
                          textColor=HEADING, fontSize=16, spaceBefore=10, spaceAfter=6))
    ss.add(ParagraphStyle("H2x", parent=ss["Heading2"], fontName="Helvetica-Bold",
                          textColor=HEADING, fontSize=12.5, spaceBefore=8, spaceAfter=4))
    ss.add(ParagraphStyle("Body2", parent=ss["BodyText"], fontName="Helvetica",
                          textColor=BODY, fontSize=9.7, leading=13.6, alignment=TA_JUSTIFY,
                          spaceAfter=6))
    ss.add(ParagraphStyle("Cap", parent=ss["BodyText"], fontName="Helvetica",
                          textColor=MUTED, fontSize=8.3, leading=10.5, alignment=TA_CENTER,
                          spaceAfter=10))
    ss.add(ParagraphStyle("CellH", parent=ss["BodyText"], fontName="Helvetica-Bold",
                          textColor=TABLE_HDR_FG, fontSize=8.2, leading=10, alignment=TA_CENTER))
    ss.add(ParagraphStyle("CellL", parent=ss["BodyText"], fontName="Helvetica",
                          textColor=BODY, fontSize=8.2, leading=10))
    ss.add(ParagraphStyle("TitleBig", parent=ss["Title"], fontName="Helvetica-Bold",
                          textColor=HEADING, fontSize=21, leading=25, spaceAfter=4))
    ss.add(ParagraphStyle("Sub", parent=ss["BodyText"], fontName="Helvetica",
                          textColor=MUTED, fontSize=11, leading=14, spaceAfter=2))
    return ss

HDR_LABELS = {
    "n_clonotypes":"n clonotypes", "total_abundance":"total abundance",
    "singleton_frac":"singleton frac", "shannon_entropy":"Shannon entropy",
    "clonality_index":"clonality index", "InvSimpson":"Inv Simpson",
    "GiniSimpson":"Gini-Simpson", "p_value":"p value", "median1":"median grp1",
    "median2":"median grp2", "CellSource":"cell source",
}
def pretty(c): return HDR_LABELS.get(c, str(c).replace("_", " "))

def make_helpers(styles):
    def para(t, s="Body2"): return Paragraph(t, styles[s])
    def divider():
        return HRFlowable(width=480, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)
    def df_table(df, colwidths=None, maxrows=30):
        df = df.copy()
        if len(df) > maxrows: df = df.head(maxrows)
        hdr = [Paragraph(f"<b>{pretty(c)}</b>", styles["CellH"]) for c in df.columns]
        rows = [[Paragraph("" if pd.isna(v) else str(v), styles["CellL"]) for v in r]
                for r in df.values.tolist()]
        t = Table([hdr]+rows, colWidths=colwidths, repeatRows=1)
        sty = [("BACKGROUND",(0,0),(-1,0),TABLE_HDR_BG),("TEXTCOLOR",(0,0),(-1,0),TABLE_HDR_FG),
               ("GRID",(0,0),(-1,-1),0.5,TABLE_BORDER),("BOX",(0,0),(-1,-1),0.75,TABLE_BORDER),
               ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
               ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
               ("VALIGN",(0,0),(-1,-1),"MIDDLE")]
        for i in range(2, len(rows)+1, 2): sty.append(("BACKGROUND",(0,i),(-1,i),TABLE_ALT))
        t.setStyle(TableStyle(sty)); t.hAlign="CENTER"
        return t
    def table_block(df, colwidths, caption, maxrows=30):
        return KeepTogether([df_table(df, colwidths, maxrows), Spacer(1,4), para(caption,"Cap")])
    def fig(path, caption, w=460):
        if not path or not os.path.exists(path): return para(f"[missing figure: {os.path.basename(str(path))}]","Cap")
        from PIL import Image as PILImage
        iw, ih = PILImage.open(path).size
        img = Image(path, width=w, height=ih*(w/iw)); img.hAlign="CENTER"
        return KeepTogether([img, Spacer(1,4), para(caption,"Cap")])
    return para, divider, df_table, table_block, fig

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True, help="repertoire_analysis.R OUT_DIR")
    ap.add_argument("--pdf", default=None, help="output PDF path (default: <out-dir>/report_repertoire_analysis.pdf)")
    ap.add_argument("--title", default="Immune Repertoire Analysis")
    args = ap.parse_args()

    OUT_DIR = args.out_dir
    PDF = args.pdf or os.path.join(OUT_DIR, "report_repertoire_analysis.pdf")
    TAB = os.path.join(OUT_DIR, "tables"); FIG = os.path.join(OUT_DIR, "figures")

    M = {}
    mp = os.path.join(OUT_DIR, "analysis_metrics.json")
    if os.path.exists(mp): M = json.load(open(mp))
    receptor = M.get("receptor","TCR"); chain = M.get("chain","TRB")
    modality = M.get("modality","single_cell"); nS = M.get("n_samples","?")
    is_sc = (modality == "single_cell"); is_bcr = (receptor == "BCR")
    abund_word = "cells" if is_sc else "templates/reads"

    def csv(name):
        p = os.path.join(TAB, name)
        return pd.read_csv(p) if os.path.exists(p) else None
    def fpath(name):
        p = os.path.join(FIG, name)
        return p if os.path.exists(p) else None

    citations = []
    cp = os.path.join(OUT_DIR, "citations.json")
    if os.path.exists(cp):
        try: citations = json.load(open(cp))
        except Exception: citations = []

    styles = build_styles()
    para, divider, df_table, table_block, fig = make_helpers(styles)

    def header_footer(canvas, doc):
        canvas.saveState(); w,h = letter
        canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
        canvas.drawString(60, h-40, "Immune Repertoire Analysis")
        canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60, h-48, w-60, h-48)
        canvas.setStrokeColor(TABLE_BORDER); canvas.setLineWidth(0.75); canvas.line(60, 40, w-60, 40)
        canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
        canvas.drawCentredString(w/2, 26, f"Page {doc.page}")
        canvas.restoreState()

    story = []
    # ---- Title ----
    story.append(Paragraph(args.title, styles["TitleBig"]))
    story.append(Paragraph(f"Clonality, Diversity, V/J Gene Usage & Repertoire Overlap "
                           f"({receptor}, {chain} chain, {modality.replace('_','-')} modality)", styles["Sub"]))
    story.append(Paragraph(f"Generated by Biomni | {datetime.date.today():%B %d, %Y}", styles["Sub"]))
    story.append(divider())

    # ---- Infographic (optional, generated with GenerateImage) ----
    ig = os.path.join(OUT_DIR, "infographic.png")
    if os.path.exists(ig):
        story.append(fig(ig, "Figure 0. Visual summary of the analysis workflow and headline findings.", w=470))

    # ---- Summary ----
    summ = csv("sample_summary.csv")
    story.append(Paragraph("Summary", styles["H1x"]))
    tot = int(summ["n_clonotypes"].sum()) if summ is not None else 0
    rng = f'{summ["n_clonotypes"].min():,}\u2013{summ["n_clonotypes"].max():,}' if summ is not None else "n/a"
    story.append(para(
        f"We analyzed <b>{nS} {receptor} repertoire samples</b> ({modality.replace('_','-')} data, {chain} chain) "
        f"with the R package <b>immunarch</b>, comprising <b>{tot:,} clonotypes</b> ({rng} per sample). "
        f"We quantified clonal architecture (clonal-space homeostasis, top-clone dominance, a clonality index), "
        f"repertoire diversity (Chao1 richness, Shannon entropy, inverse Simpson, Gini\u2013Simpson, D50, and "
        f"rarefaction), {chain[:-1]}V/{chain[:-1]}J gene usage with cross-sample similarity clustering, and pairwise "
        f"repertoire overlap (shared clonotypes and the Morisita\u2013Horn index)."))
    flagged = csv("overlap_flagged_pairs.csv")
    if flagged is not None and len(flagged):
        pr = flagged.iloc[0]
        story.append(para(
            f"<b>Notable overlap signal.</b> The overlap analysis flags at least one sample pair "
            f"(<b>{pr['SampleA']} \u2194 {pr['SampleB']}, {int(pr['shared'])} shared clonotypes</b>) far above the "
            f"cross-sample background. Because an individual\u2019s repertoire is essentially private, this indicates "
            f"the pair likely shares a biological source (same donor, replicate, or contamination) rather than "
            f"coincidental public-clonotype sharing \u2014 a useful internal consistency check."))

    # ---- Introduction ----
    story.append(Paragraph("Introduction", styles["H1x"]))
    rec_txt = ("T-cell receptor (TCR)" if receptor=="TCR" else
               "B-cell receptor (BCR)" if receptor=="BCR" else "immune receptor")
    story.append(para(
        f"The adaptive immune repertoire is the collection of antigen-receptor sequences generated by somatic "
        f"V(D)J recombination. The hypervariable CDR3 region is a practical fingerprint of a clonotype. AIRR "
        f"sequencing enumerates clonotypes and their abundances, enabling quantitative description along "
        f"complementary axes: <b>clonality</b> (dominance by expanded clones), <b>diversity</b> (richness and "
        f"evenness), and <b>gene usage</b> (V/J germline segment recombination biases), plus <b>overlap</b> "
        f"(shared clonotypes between samples). Here we profile {nS} {rec_txt} sample(s)."))
    if citations:
        cite_ids = ", ".join(f"[{c.get('n',i+1)}]" for i,c in enumerate(citations[:6]))
        story.append(para(f"Background and methods draw on the repertoire-analysis literature {cite_ids} and the "
                          f"immunarch package."))

    # ---- Methods ----
    story.append(Paragraph("Methods", styles["H1x"]))
    story.append(Paragraph("Data", styles["H2x"]))
    story.append(para(
        f"Repertoire files were loaded with immunarch <font face='Courier'>repLoad</font> (format auto-detected: "
        f"10x Cell Ranger, MiXCR, Adaptive/immunoSEQ, or AIRR-C). Analysis used the {chain} chain. Clonotype "
        f"abundance is measured in {abund_word}."))
    if summ is not None:
        ncol = summ.shape[1]; w = [max(46, int(452/ncol))]*ncol
        sf_note = ("singleton frac = fraction of clonotypes observed once (diagnostic of modality); "
                   if "singleton_frac" in summ.columns else "")
        story.append(table_block(summ, w,
            f"<b>Table 1.</b> Cohort composition and per-sample summary. n clonotypes = unique {chain} clonotypes; "
            f"{sf_note}clonality index = 1 \u2212 normalized Shannon entropy (higher = more clonal)."))
    story.append(Paragraph("Analysis", styles["H2x"]))
    story.append(para(
        "<b>Clonality:</b> clonal-space homeostasis and top-clone occupied space "
        "(<font face='Courier'>repClonality</font>) and a per-sample clonality index. "
        "<b>Diversity:</b> Chao1, Shannon, inverse Simpson, Gini\u2013Simpson, D50 and rarefaction "
        "(<font face='Courier'>repDiversity</font>). <b>Gene usage:</b> normalized V/J segment frequencies "
        "(<font face='Courier'>geneUsage</font>) with Jensen\u2013Shannon and correlation similarity "
        "(<font face='Courier'>geneUsageAnalysis</font>). <b>Overlap:</b> shared clonotypes and Morisita\u2013Horn "
        "index (<font face='Courier'>repOverlap</font>). <b>Statistics:</b> where groups are defined, two-sided "
        "Wilcoxon rank-sum tests compare scalar metrics; exact p-values are reported and small-n comparisons are "
        "flagged exploratory."))

    story.append(PageBreak())
    # ---- Results ----
    story.append(Paragraph("Results", styles["H1x"]))

    story.append(Paragraph("Clonal architecture", styles["H2x"]))
    if is_sc:
        clon_note = ("In single-cell data each clonotype maps to cells, so clonality reflects clonal-space "
                     "structure rather than PCR-amplified expansion magnitude.")
    else:
        clon_note = ("In bulk data clonal abundance reflects template counts, so expanded clones and hyperexpanded "
                     "bins represent genuine clonal expansion.")
    story.append(para("Clonal-space homeostasis partitions each repertoire by clone size; top-clone occupancy "
                      "summarizes dominance by the most abundant clonotypes. " + clon_note))
    story.append(fig(fpath("clonality_homeostasis.png"), "Figure 1. Clonal-space homeostasis: repertoire fraction occupied by clones grouped by relative abundance."))
    story.append(fig(fpath("clonality_top_clones.png"), "Figure 2. Repertoire space occupied by the top 10, 100, and 1000 clonotypes per sample."))

    story.append(Paragraph("Diversity", styles["H2x"]))
    div = csv("diversity_metrics.csv")
    story.append(para(
        "Diversity was quantified with complementary estimators (Table 2). Richness estimators (Chao1) extrapolate "
        "total clonotype richness; evenness-sensitive indices (Shannon, inverse Simpson, Gini\u2013Simpson) "
        "down-weight dominance; D50 is the number of clones accounting for half the repertoire. Rarefaction "
        "(Figure 4) shows richness as a function of sampling depth."))
    if is_sc:
        story.append(para(
            "<b>Caveat on Chao1 (single-cell).</b> Chao1 estimates unseen richness from the singleton/doubleton "
            f"ratio. In single-cell repertoires almost every clonotype is observed once, inflating Chao1 "
            f"(here up to {M.get('chao1_max','~1e5'):,} vs. {M.get('obs_clonotypes_max','observed')} observed "
            f"clonotypes). Treat Chao1 as an upper-bound extrapolation artifact; the evenness indices and "
            f"rarefaction are more reliable for ranking samples." if isinstance(M.get('chao1_max'), (int,float))
            else "ratio. In single-cell repertoires almost every clonotype is observed once, inflating Chao1 far "
            "above observed richness; treat it as an upper-bound artifact and rely on evenness indices and "
            "rarefaction."))
    if div is not None:
        ncol = div.shape[1]; w = [max(44, int(478/ncol))]*ncol
        cap = ("<b>Table 2.</b> Diversity metrics per sample. " +
               ("Chao1 is inflated in single-cell data (see caveat above); evenness indices and rarefaction are "
                "more reliable here." if is_sc else
                "In bulk data Chao1 and richness estimators are reliable."))
        story.append(table_block(div.round(3), w, cap))
    story.append(fig(fpath("diversity_all_metrics.png"), "Figure 3. Diversity metrics across samples."))
    story.append(fig(fpath("diversity_rarefaction.png"), "Figure 4. Rarefaction curves (estimated richness vs. sampled units); controls for depth differences."))

    story.append(Paragraph("V/J gene usage", styles["H2x"]))
    story.append(para(
        f"Normalized {chain[:-1]}V and {chain[:-1]}J segment usage describes germline recombination biases. "
        f"Cross-sample similarity of usage vectors (Figures 7\u20138) groups samples with similar germline usage; "
        f"technical factors (platform, cell source) and biology can both structure this similarity."))
    story.append(fig(fpath(f"vgene_usage_{chain}V.png"), f"Figure 5. Normalized {chain[:-1]}V segment usage per sample."))
    story.append(fig(fpath(f"jgene_usage_{chain}J.png"), f"Figure 6. Normalized {chain[:-1]}J segment usage per sample."))
    story.append(fig(fpath("vgene_usage_JS_heatmap.png"), f"Figure 7. Pairwise Jensen\u2013Shannon divergence of {chain[:-1]}V usage (lower = more similar)."))
    story.append(fig(fpath("vgene_usage_cor_heatmap.png"), f"Figure 8. Pairwise correlation of {chain[:-1]}V usage profiles."))

    story.append(Paragraph("Repertoire overlap", styles["H2x"]))
    story.append(para(
        "Overlap quantifies shared (public) clonotypes between samples. The number of shared clonotypes (Figure 9) "
        "is sensitive to repertoire size; the Morisita\u2013Horn index (Figure 10) is an abundance-weighted, "
        "size-normalized similarity."))
    if flagged is not None and len(flagged):
        pr = flagged.iloc[0]
        story.append(para(
            f"The analysis isolates <b>{pr['SampleA']} \u2194 {pr['SampleB']} ({int(pr['shared'])} shared "
            f"clonotypes)</b> far above background, a strong signal of a shared biological source (same donor / "
            f"replicate). Remaining pairs show only the low baseline of public-clonotype sharing expected between "
            f"unrelated repertoires \u2014 a built-in positive control that the overlap workflow detects true "
            f"repertoire identity."))
    story.append(fig(fpath("overlap_public_heatmap.png"), "Figure 9. Number of shared clonotypes between each pair of samples (log-scaled color)."))
    story.append(fig(fpath("overlap_morisita_heatmap.png"), "Figure 10. Morisita\u2013Horn overlap index (abundance-weighted, size-normalized)."))

    wil = csv("wilcoxon_tests.csv")
    if wil is not None and len(wil):
        story.append(Paragraph("Group comparison", styles["H2x"]))
        ncol = wil.shape[1]; w = [max(40, int(478/ncol))]*ncol
        story.append(table_block(wil.round(4), w,
            "<b>Table 3.</b> Two-sided Wilcoxon rank-sum tests of diversity metrics between groups. Exact p-values; "
            "small-n comparisons are exploratory (limited power, cannot yield small p-values)."))

    # ---- Conclusions ----
    story.append(Paragraph("Conclusions", styles["H1x"]))
    concl = (f"This analysis delivers a complete, reproducible AIRR/{receptor} repertoire workflow covering "
             f"clonality, diversity, gene usage, and overlap, with per-sample tables and publication-quality "
             f"figures. ")
    concl += ("Samples are described by evenness-based diversity and clonal-space structure (single-cell Chao1 "
              "treated as an extrapolation artifact). " if is_sc else
              "Richness and clonal-expansion metrics are interpreted as genuine biology in these bulk data. ")
    if flagged is not None and len(flagged):
        concl += "The overlap analysis provides an internal positive control by isolating high-overlap same-source pairs."
    story.append(para(concl))

    lim = ("<b>Limitations.</b> ")
    if is_sc:
        lim += ("(i) Single-cell richness estimators (Chao1) are inflated by singleton dominance; rely on evenness "
                "indices and rarefaction. ")
    else:
        lim += ("(i) Bulk abundance reflects template/read counts and is sensitive to sequencing depth and PCR; "
                "depth-normalize before cross-sample richness comparison. ")
    if is_bcr:
        lim += ("(ii) For BCR, somatic hypermutation means an exact-CDR3 clonotype is NOT a clonal lineage, and "
                "isotype/class-switch structure is not modeled here; full lineage reconstruction is out of scope. ")
    lim += ("Group comparisons are underpowered at small sample sizes and should be treated as exploratory. Analysis "
            f"is restricted to the {chain} chain and high-confidence productive sequences.")
    story.append(para(lim))

    # ---- Next steps ----
    story.append(Paragraph("Next steps", styles["H1x"]))
    ns = ("Apply the same pipeline to a cohort with a defined biological contrast (e.g., responders vs. "
          "non-responders, pre/post-treatment) and adequate per-group sample sizes to move from description to "
          "inference. ")
    if is_sc:
        ns += ("Integrate paired single-cell gene expression to link clonal expansion with T/B-cell phenotype. ")
    else:
        ns += ("Add depth-matched subsampling and clone-tracking across timepoints. ")
    ns += ("Annotate public/expanded CDR3s against antigen-specificity databases (e.g., McPAS-TCR, VDJdb) to assign "
           "putative antigen associations, and consider receptor structure prediction (ImmuneBuilder) for clones "
           "of interest.")
    story.append(para(ns))

    # ---- References ----
    if citations:
        story.append(Paragraph("References", styles["H1x"]))
        for i, c in enumerate(citations):
            story.append(para(f"[{c.get('n', i+1)}] {c.get('text','')}", "Cap"))

    story.append(divider())
    story.append(para("Analysis performed with immunarch (ImmunoMind Team, 2019; Zenodo doi:10.5281/zenodo.3367200) "
                      f"in R (immunarch {M.get('immunarch_version','0.9.1')}). Result tables and both PNG and "
                      "editable SVG figures accompany this report.", "Cap"))

    doc = SimpleDocTemplate(PDF, pagesize=letter, topMargin=56, bottomMargin=52,
                            leftMargin=60, rightMargin=60)
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)

    # ---- Validation gate ----
    from pypdf import PdfReader
    r = PdfReader(PDF); npages = len(r.pages); size = os.path.getsize(PDF)
    txt = "\n".join((p.extract_text() or "") for p in r.pages)
    assert npages >= 5, f"too few pages: {npages}"
    assert size > 20000, f"PDF too small: {size}"
    assert len(txt.strip()) > 200, "no extractable text"
    print(f"PDF written: {PDF}")
    print(f"pages={npages} size={size:,} bytes")
    print("VALIDATION_OK")

if __name__ == "__main__":
    main()
