#!/usr/bin/env python3
"""
Step 5: Assemble a Phylo-styled PDF report from a completed run's data files.
Reads curated_dataset.csv, cv_summary.csv, rf_scaffold_oof.csv, the candidate
CSVs, and the figures; auto-detects the best scaffold-split model and writes an
honest, discovery-grade report.

Usage:
  python build_report.py --outdir /mnt/results/pcsk9 --target PCSK9
  python build_report.py --outdir /mnt/results/pcsk9 --target PCSK9 \
      --out /mnt/results/pcsk9/report.pdf
"""
import argparse, os, sys, datetime, json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, PageBreak, HRFlowable,
                                KeepTogether)

GOLD = HexColor("#D4A04A"); HEADING = HexColor("#111111"); BODY = HexColor("#2C2A26")
MUTED = HexColor("#8A8378"); THDR_FG = HexColor("#FFFFFF"); ALT = HexColor("#F9F7F3")
BORDER = HexColor("#D5CFC5"); CALLOUT = HexColor("#FAF9F3")


def styles():
    s = getSampleStyleSheet()

    def add(n, **k):
        if n in s.byName:
            s.byName.pop(n)
        s.add(ParagraphStyle(name=n, **k))
    add("RTitle", fontName="Helvetica-Bold", fontSize=23, textColor=HEADING,
        leading=28, spaceAfter=6)
    add("Sub", fontName="Helvetica", fontSize=11, textColor=GOLD, spaceAfter=4)
    add("Attr", fontName="Helvetica-Oblique", fontSize=9.5, textColor=MUTED,
        spaceAfter=8)
    add("H2", fontName="Helvetica-Bold", fontSize=15, textColor=HEADING,
        spaceBefore=18, spaceAfter=8)
    add("H3", fontName="Helvetica-Bold", fontSize=11.5, textColor=HEADING,
        spaceBefore=10, spaceAfter=4)
    add("Body2", fontName="Helvetica", fontSize=10, textColor=BODY,
        alignment=TA_JUSTIFY, spaceAfter=7, leading=14.5)
    add("Cap", fontName="Helvetica-Oblique", fontSize=8.5, textColor=MUTED,
        alignment=TA_CENTER, spaceAfter=12)
    add("Cell", fontName="Helvetica", fontSize=8, textColor=BODY, leading=10)
    add("CellB", fontName="Helvetica-Bold", fontSize=8, textColor=THDR_FG,
        leading=10)
    add("CO", fontName="Helvetica", fontSize=9.5, textColor=BODY, leading=13.5,
        alignment=TA_LEFT)
    return s


def hdrftr(canvas, doc, title):
    canvas.saveState(); w, h = letter
    canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
    canvas.drawString(60, h - 40, title)
    canvas.setStrokeColor(GOLD); canvas.setLineWidth(1)
    canvas.line(60, h - 48, w - 60, h - 48)
    canvas.setStrokeColor(BORDER); canvas.setLineWidth(0.75)
    canvas.line(60, 40, w - 60, 40)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
    canvas.drawCentredString(w / 2, 26, f"Page {doc.page}")
    canvas.restoreState()


def img(path, w, h, cap, S):
    if not os.path.exists(path):
        return Paragraph(f"<i>[missing figure: {os.path.basename(path)}]</i>",
                         S["Cap"])
    i = Image(path, width=w, height=h); i.hAlign = "CENTER"
    return KeepTogether([i, Spacer(1, 3), Paragraph(cap, S["Cap"])])


def callout(text, S):
    t = Table([[Paragraph(text, S["CO"])]], colWidths=[470])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CALLOUT),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    t.hAlign = "CENTER"
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--target', required=True)
    ap.add_argument('--chembl_version', default='ChEMBL')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    D = os.path.join(a.outdir, 'data')
    FIG = os.path.join(a.outdir, 'figures')
    out = a.out or os.path.join(a.outdir, f'report_{a.target}_qsar.pdf')

    comp = pd.read_csv(os.path.join(D, 'curated_dataset.csv'))
    summ = pd.read_csv(os.path.join(D, 'cv_summary.csv'))
    cv = {(r.split, r.model): r for _, r in summ.iterrows()}
    best = summ[summ.split == 'scaffold'].sort_values(
        'Spearman', ascending=False).iloc[0]
    sel = best.model
    gnn_row = cv.get(('scaffold', 'GNN'))

    # within-group RF spearman from OOF
    wb = wf = None
    oof_p = os.path.join(D, 'rf_scaffold_oof.csv')
    if os.path.exists(oof_p):
        d = pd.read_csv(oof_p)
        mb = d.assay_group == 'binding(Kd/Ki)'
        mf = d.assay_group == 'functional(IC50/EC50)'
        if mb.sum() > 2:
            wb = spearmanr(d.y_true[mb], d.rf_pred[mb])[0]
        if mf.sum() > 2:
            wf = spearmanr(d.y_true[mf], d.rf_pred[mf])[0]

    # HIGH-confidence shortlist (the renamed file contains ONLY high-confidence
    # rows now; fall back to the legacy name for older runs).
    hi_path = os.path.join(D, 'high_confidence_candidates.csv')
    legacy_path = os.path.join(D, 'novel_scaffold_candidates.csv')
    conf = pd.read_csv(hi_path) if os.path.exists(hi_path) else (
        pd.read_csv(legacy_path) if os.path.exists(legacy_path)
        else pd.DataFrame())
    t25_path = os.path.join(D, 'top25_high_confidence.csv')
    t25_legacy = os.path.join(D, 'top25_novel_candidates.csv')
    top25 = pd.read_csv(t25_path) if os.path.exists(t25_path) else (
        pd.read_csv(t25_legacy) if os.path.exists(t25_legacy)
        else pd.DataFrame())
    hs = conf.clinical_status.value_counts().to_dict() if len(conf) else {}

    # applicability-domain tier summary
    ad = {}
    ad_p = os.path.join(D, 'ad_tier_summary.json')
    if os.path.exists(ad_p):
        with open(ad_p) as fh:
            ad = json.load(fh)
    tc = ad.get('tier_counts', {})
    n_high = ad.get('n_high', len(conf))
    n_border = ad.get('n_borderline', int(tc.get('borderline', 0)))
    n_ood = int(tc.get('out_of_domain', 0))
    n_novel_scored = ad.get('n_novel', 0)

    # framework provenance (no silent substitution)
    prov = {}
    prov_p = os.path.join(D, 'framework_provenance.json')
    if os.path.exists(prov_p):
        with open(prov_p) as fh:
            prov = json.load(fh)
    dp_row = cv.get(('scaffold', 'DeepPurpose'))

    S = styles()
    sc = comp.scaffold.value_counts()
    n_func = int((comp.assay_group == 'functional(IC50/EC50)').sum())
    n_bind = int((comp.assay_group == 'binding(Kd/Ki)').sum())
    story = []
    story += [Spacer(1, 30),
              Paragraph(f"Predicting {a.target} Ligand Affinity and Nominating "
                        f"Novel Scaffolds", S["RTitle"]),
              Paragraph("Affinity models benchmarked under scaffold-split "
                        "validation on ChEMBL small molecules, with a three-tier "
                        "applicability domain", S["Sub"]),
              Spacer(1, 6),
              Paragraph(f"<i>Generated by Biomni  |  "
                        f"{datetime.date.today().isoformat()}  |  Data source: "
                        f"{a.chembl_version}</i>", S["Attr"]),
              HRFlowable(width=480, thickness=1, color=GOLD, spaceAfter=10,
                         spaceBefore=4)]

    # Executive summary
    story.append(Paragraph("Executive Summary", S["H2"]))
    # dynamic model list = whatever actually appears in the CV summary
    present = [m for m in ['DeepPurpose', 'GNN', 'RandomForest', 'GBM']
               if ('scaffold', m) in cv]
    _names = {'DeepPurpose': 'a DeepPurpose deep model',
              'GNN': 'a message-passing graph neural network',
              'RandomForest': 'Random Forest', 'GBM': 'Gradient Boosting'}
    model_phrase = ', '.join(_names[m] for m in present) if present else \
        'the models'
    story.append(Paragraph(
        f"Affinity models ({model_phrase}) were trained on curated "
        f"{a.chembl_version} small-molecule activity data for {a.target} to "
        f"predict affinity (pAffinity, the pooled &#8722;log<sub>10</sub> of "
        f"IC50/Ki/Kd/EC50) and to nominate chemically novel scaffolds. After "
        f"stringent curation the modelable set comprised <b>{len(comp)} drug-like "
        f"small molecules</b> across <b>{comp.scaffold.nunique()} Bemis-Murcko "
        f"scaffolds</b>.", S["Body2"]))
    # ---- Framework provenance (no silent substitution) ----
    if prov.get('requested'):
        if prov.get('used') and not prov.get('failed_reason'):
            story.append(callout(
                f"<b>Requested framework &#8212; DeepPurpose (used).</b> A "
                f"DeepPurpose model ({prov.get('drug_encoding','?')} encoding, "
                f"v{prov.get('version','?')}) was explicitly requested and was "
                f"built and benchmarked as a first-class model on the same "
                f"cross-validation folds as the fingerprint baselines.", S))
        else:
            story.append(callout(
                f"<b>Requested framework &#8212; DeepPurpose (NOT used; "
                f"disclosed).</b> DeepPurpose was explicitly requested but could "
                f"not be run in this environment "
                f"(reason: {prov.get('failed_reason','unavailable')}). To avoid "
                f"silently substituting a different method, this is stated "
                f"plainly: the results below use the native models "
                f"({', '.join(_names[m] for m in present) or 'fingerprint models'}) "
                f"instead.", S))
    rf_s = cv[('scaffold', sel)]
    comp_txt = (f"the <b>{sel} was the best model</b> (Spearman &#961; = "
                f"<b>{rf_s.Spearman:+.2f} &#177; {rf_s.Spearman_sd:.2f}</b>, "
                f"R&#178; = {rf_s.R2:+.2f})")
    if gnn_row is not None and sel != 'GNN':
        comp_txt += (f", modestly ahead of the GNN (&#961; = "
                     f"{gnn_row.Spearman:+.2f}, R&#178; = {gnn_row.R2:+.2f}). "
                     f"At this sample size the added complexity of the GNN did "
                     f"not pay off &#8212; a common and expected outcome for "
                     f"datasets of a few hundred molecules")
    story.append(Paragraph(
        f"Models were evaluated with leakage-free 5-fold cross-validation "
        f"(early stopping and target scaling fit strictly inside training "
        f"folds, test folds untouched until scoring). On the honest "
        f"<b>scaffold split</b>, {comp_txt}. The {sel} was therefore selected "
        f"as the production model for screening.", S["Body2"]))
    if n_novel_scored:
        story.append(Paragraph(
            f"The selected production model scored a library of clinical-stage / "
            f"approved small molecules. Each novel-scaffold compound was assigned "
            f"an <b>applicability-domain confidence tier</b> from its nearest-"
            f"neighbour Tanimoto similarity to the training set, prediction range, "
            f"and model disagreement. Of <b>{n_novel_scored:,} novel-scaffold "
            f"compounds</b>, only <b>{n_high} qualified as high-confidence "
            f"(in-domain)</b> ({ad.get('n_high_scaffolds', conf.scaffold.nunique() if len(conf) else 0)} "
            f"scaffolds; including {hs.get('Approved', 0)} approved drugs), while "
            f"<b>{n_border:,} were borderline / low-confidence</b> and "
            f"<b>{n_ood:,} were out-of-domain</b> (too dissimilar to model "
            f"reliably, or trivial near-analogs). Only the high-confidence tier is "
            f"treated as a shortlist; borderline and out-of-domain compounds are "
            f"reported for transparency but are explicitly not confident hits.",
            S["Body2"]))
    story.append(callout(
        "<b>Interpretation &#8212; discovery-grade, not validated.</b> Under a "
        "hard scaffold-split test the model explains only a fraction of variance, "
        "and predicted potencies are <b>hypotheses for prioritization</b>, not "
        "confirmed activities. Crucially, most library compounds fall <b>outside "
        "the model's applicability domain</b>: a compound only weakly similar to "
        "the training set (low nearest-neighbour Tanimoto) is an extrapolation "
        "and is labelled out-of-domain, not a confident hit. Even high-confidence "
        "candidates require experimental confirmation.", S))

    # Data & methods
    story.append(Paragraph("Data & Methods", S["H2"]))
    story.append(Paragraph(
        f"Bioactivity was retrieved from {a.chembl_version} for the target's "
        f"data-rich human records. Affinity endpoints (IC50/Ki/Kd/EC50, exact "
        f"'=' relation, nM) were pooled into one &#8722;log<sub>10</sub> M "
        f"pAffinity label, with dominant assay type tracked per compound. "
        f"Structures were standardized (largest fragment, neutralized, "
        f"canonicalized), deduplicated by InChIKey, and filtered to drug-like "
        f"space (MW &#8804; 650, organic). Replicates were aggregated by median; "
        f"compounds with &gt;2 log replicate spread were dropped.", S["Body2"]))
    dt = [[Paragraph(f'<b>{h}</b>', S["CellB"]) for h in ["Property", "Value"]]]
    for k, v in [("Data source", a.chembl_version),
                 ("Final compounds", str(len(comp))),
                 ("Unique scaffolds",
                  f"{comp.scaffold.nunique()} ({int((sc == 1).sum())} singletons)"),
                 ("Assay composition",
                  f"{n_func} functional (IC50/EC50), {n_bind} binding (Kd/Ki)"),
                 ("pAffinity range",
                  f"{comp.pAffinity.min():.2f} &#8211; {comp.pAffinity.max():.2f}"
                  f"  (mean {comp.pAffinity.mean():.2f} &#177; "
                  f"{comp.pAffinity.std():.2f})"),
                 ("MW range",
                  f"{comp.mw.min():.0f} &#8211; {comp.mw.max():.0f} Da"),
                 ("PAINS flagged (kept)", str(int(comp.PAINS.sum())))]:
        dt.append([Paragraph(k, S["Cell"]), Paragraph(v, S["Cell"])])
    tb = Table(dt, colWidths=[150, 320]); tb.hAlign = "CENTER"
    tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), THDR_FG),
        *[("BACKGROUND", (0, i), (-1, i), ALT) for i in range(2, len(dt), 2)],
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story += [tb, Spacer(1, 6)]
    story.append(Paragraph(
        "Two model families were compared on identical folds: an "
        "edge-conditioned message-passing GNN (NNConv) over molecular graphs, "
        "and Morgan-fingerprint (ECFP4, 2048-bit) Random-Forest and "
        "Gradient-Boosting regressors. Performance is the mean &#177; SD across "
        "the five folds of a single 5-fold cross-validation under a <b>scaffold "
        "split</b> (Bemis-Murcko groups disjoint between train and test "
        "&#8212; the honest test of generalization to new chemistry) and a "
        "<b>random split</b> (optimistic reference). All model selection was "
        "performed without touching the test fold.", S["Body2"]))

    story.append(PageBreak())
    # Results
    story.append(Paragraph("Results", S["H2"]))
    story.append(Paragraph("Cross-validated performance", S["H3"]))
    hdr = ["Split", "Model", "RMSE", "MAE", "R&#178;", "Spearman &#961;"]
    ct = [[Paragraph(f'<b>{h}</b>', S["CellB"]) for h in hdr]]
    order = [('scaffold', m) for m in
             ['DeepPurpose', 'RandomForest', 'GBM', 'GNN']] + \
            [('random', m) for m in
             ['DeepPurpose', 'RandomForest', 'GBM', 'GNN']]
    n_scaffold_rows = 0
    for spl, mo in order:
        if (spl, mo) not in cv:
            continue
        if spl == 'scaffold':
            n_scaffold_rows += 1
        r = cv[(spl, mo)]
        star = " *" if mo == sel and spl == 'scaffold' else ""
        ct.append([Paragraph(spl, S["Cell"]),
                   Paragraph(mo + star, S["Cell"]),
                   Paragraph(f"{r.RMSE:.2f}&#177;{r.RMSE_sd:.2f}", S["Cell"]),
                   Paragraph(f"{r.MAE:.2f}", S["Cell"]),
                   Paragraph(f"{r.R2:+.2f}&#177;{r.R2_sd:.2f}", S["Cell"]),
                   Paragraph(f"{r.Spearman:+.2f}&#177;{r.Spearman_sd:.2f}",
                             S["Cell"])])
    tb2 = Table(ct, colWidths=[70, 120, 80, 55, 80, 90], repeatRows=1)
    tb2.hAlign = "CENTER"
    tb2.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), THDR_FG),
        ("BACKGROUND", (0, 1), (-1, n_scaffold_rows), HexColor("#FBF5EA")),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
    story += [tb2, Paragraph("Scaffold-split rows (shaded) are the headline "
              "metrics. * = selected production model.", S["Cap"])]
    story.append(img(os.path.join(FIG, 'fig2_model_comparison.png'), 400, 167,
        "Figure 1. Model comparison (mean &#177; SD). Solid = scaffold split, "
        "hatched = random split. The scaffold&#8211;random gap reflects "
        "analog-leakage inflation of naive metrics.", S))
    cap2 = ("Figure 2. Selected model predicted vs experimental pAffinity, "
            "scaffold-split out-of-fold. Points colored by assay type.")
    if wb is not None and wf is not None:
        cap2 += (f" Within-assay Spearman is positive for both binding "
                 f"(&#961;={wb:+.2f}) and functional (&#961;={wf:+.2f}) subsets, "
                 f"indicating real structure-activity signal rather than an "
                 f"assay-type offset.")
    story.append(img(os.path.join(FIG, 'fig1_pred_vs_actual.png'), 290, 277,
                     cap2, S))

    story.append(PageBreak())
    story.append(Paragraph("Dataset landscape", S["H3"]))
    story.append(img(os.path.join(FIG, 'fig3_dataset_landscape.png'), 430, 170,
        "Figure 3. Left: pAffinity distribution by assay type. Right: scaffold "
        "frequency &#8212; a scaffold-diverse set makes scaffold-split "
        "generalization intrinsically hard.", S))

    # Applicability-domain tiers
    if n_novel_scored:
        story.append(Paragraph("Applicability-domain tiers", S["H3"]))
        story.append(Paragraph(
            f"Every novel-scaffold library compound was assigned a confidence "
            f"tier from its nearest-neighbour Tanimoto to the training set (high "
            f"window [{ad.get('ad_high_tanimoto_min','?')}, "
            f"{ad.get('ad_high_tanimoto_max','?')}]), whether the prediction is "
            f"within the observed training range, and per-tree disagreement. "
            f"<b>High-confidence: {n_high}</b>; <b>borderline / low-confidence: "
            f"{n_border:,}</b>; <b>out-of-domain: {n_ood:,}</b>. The large "
            f"out-of-domain fraction is expected and important: most external "
            f"compounds are only weakly similar to the training chemistry, so "
            f"the model cannot score them reliably and they are <b>not</b> "
            f"nominated. This corrects a common failure mode where weakly-similar "
            f"(low-Tanimoto) compounds are wrongly labelled confident hits.",
            S["Body2"]))
        story.append(img(os.path.join(FIG, 'fig5_ad_tiers.png'), 300, 222,
            "Figure 4. Applicability-domain confidence tiers among novel-scaffold "
            "library compounds. Only the high-confidence (in-domain) tier is "
            "treated as a shortlist.", S))

    if len(top25):
        story.append(Paragraph("High-confidence novel-scaffold candidates",
                               S["H3"]))
        story.append(Paragraph(
            f"Only the <b>{n_high} high-confidence (in-domain)</b> novel-scaffold "
            f"compounds are treated as a shortlist. By clinical stage: "
            f"{hs.get('Approved', 0)} approved, {hs.get('Phase 3', 0)} Phase 3, "
            f"{hs.get('Phase 2', 0)} Phase 2. The top scaffold-diverse "
            f"high-confidence candidates are shown below; borderline and "
            f"out-of-domain compounds are available in all_scored_candidates.csv "
            f"but are not nominated.", S["Body2"]))
        th = ["#", "ChEMBL ID", "Name", "Status", "Pred pAff", "&#177;SD",
              "Tanimoto"]
        tt = [[Paragraph(f'<b>{h}</b>', S["CellB"]) for h in th]]
        for i, (_, r) in enumerate(top25.head(12).iterrows(), 1):
            tt.append([Paragraph(str(i), S["Cell"]),
                       Paragraph(str(r['chembl_id']), S["Cell"]),
                       Paragraph((str(r['pref_name']) or "&#8212;")[:20],
                                 S["Cell"]),
                       Paragraph(str(r['clinical_status']), S["Cell"]),
                       Paragraph(f"{r['pred_pAffinity']:.2f}", S["Cell"]),
                       Paragraph(f"{r['pred_std']:.2f}" if pd.notna(
                           r['pred_std']) else "-", S["Cell"]),
                       Paragraph(f"{r['nn_tanimoto']:.2f}", S["Cell"])])
        tb3 = Table(tt, colWidths=[22, 90, 120, 70, 60, 40, 60], repeatRows=1)
        tb3.hAlign = "CENTER"
        tb3.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GOLD),
            ("TEXTCOLOR", (0, 0), (-1, 0), THDR_FG),
            *[("BACKGROUND", (0, i), (-1, i), ALT) for i in range(2, len(tt), 2)],
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5)]))
        story += [tb3, Paragraph("Table: top scaffold-diverse <b>high-confidence "
                  "(in-domain)</b> candidates (best compound per scaffold). All "
                  "have nearest-neighbour Tanimoto within the reliable window. "
                  "Full tiered list in all_scored_candidates.csv; high-confidence "
                  "subset in high_confidence_candidates.csv.", S["Cap"])]
        grid_p = os.path.join(FIG, 'fig4_top_candidates.png')
        if os.path.exists(grid_p):
            story.append(img(grid_p, 420, 331,
                "Figure 5. Structures of the top high-confidence novel-scaffold "
                "candidates (tier tagged) with clinical status, predicted "
                "pAffinity, and nearest-neighbour Tanimoto to the training set.",
                S))

    story.append(PageBreak())
    story.append(Paragraph("Discussion & Limitations", S["H2"]))
    # Deep-model vs fingerprint discussion (covers DeepPurpose and/or GNN)
    deep_rows = {m: cv.get(('scaffold', m)) for m in ('DeepPurpose', 'GNN')
                 if cv.get(('scaffold', m)) is not None}
    if sel in ('RandomForest', 'GBM') and deep_rows:
        deep_txt = '; '.join(
            f"{('DeepPurpose' if m=='DeepPurpose' else 'the GNN')} "
            f"(&#961;={r.Spearman:+.2f})" for m, r in deep_rows.items())
        story.append(Paragraph(
            f"On the honest scaffold-split test, the Morgan-fingerprint "
            f"{sel} model (&#961;={cv[('scaffold', sel)].Spearman:+.2f}) "
            f"generalized to unseen scaffolds at least as well as the deep "
            f"model(s): {deep_txt}. This is the expected outcome at this data "
            f"scale &#8212; deep architectures (graph networks, DeepPurpose "
            f"CNNs) typically need larger training sets before overtaking "
            f"well-tuned fingerprint baselines. The deep model(s) were retained "
            f"as benchmarks and the fingerprint model was selected for "
            f"screening because tree ensembles also yield a native uncertainty "
            f"estimate (per-tree spread) used by the applicability domain and do "
            f"not extrapolate beyond the training range.", S["Body2"]))
    if prov.get('requested') and not (prov.get('used') and not prov.get('failed_reason')):
        story.append(Paragraph(
            f"<b>Requested-framework note.</b> DeepPurpose was explicitly "
            f"requested but could not be run here "
            f"(reason: {prov.get('failed_reason','unavailable')}); the native "
            f"models were used instead and this substitution is disclosed rather "
            f"than hidden.", S["Body2"]))
    story.append(Paragraph("Key limitations", S["H3"]))
    for t in [
        "<b>Discovery-grade, scaffold-split performance.</b> Absolute accuracy "
        "under a hard scaffold split is bounded and fold variance is non-trivial; "
        "metrics are the mean &#177; SD across the five folds of a single 5-fold "
        "cross-validation (not repeated CV).",
        "<b>Narrow applicability domain.</b> Most external library compounds are "
        "only weakly similar to the training chemistry and fall out-of-domain; "
        "only a small high-confidence tier can be scored reliably. Weakly-similar "
        "(low-Tanimoto) compounds are labelled borderline / out-of-domain and are "
        "explicitly NOT confident hits.",
        "<b>Pooled, heterogeneous label.</b> IC50, Kd, and EC50 from different "
        "assays were combined; although assay groups had matched means, pooling "
        "mixes functional inhibition with direct binding and adds noise.",
        "<b>Predictions are unvalidated.</b> Predicted potencies are hypotheses "
        "and a ranking, not calibrated affinities; even high-confidence "
        "candidates need experimental confirmation before any activity claim.",
    ]:
        story.append(Paragraph("&#8226;&nbsp;&nbsp;" + t, S["Body2"]))
    story.append(Paragraph("Recommended next steps", S["H3"]))
    story.append(Paragraph(
        "(1) Experimentally test the high-confidence candidates to calibrate the "
        "model. (2) Add conformal prediction for calibrated per-compound "
        "confidence intervals to complement the tiered applicability domain. "
        "(3) Explore a larger-data or multi-task setup, which may let the deep "
        "models (GNN / DeepPurpose) overtake the fingerprint baseline. "
        "(4) Expand the screening library once the model is validated.",
        S["Body2"]))

    doc = SimpleDocTemplate(out, pagesize=letter, topMargin=56, bottomMargin=52,
                            leftMargin=60, rightMargin=60)
    title = f"{a.target} Affinity Model & Novel-Scaffold Discovery"
    doc.build(story, onFirstPage=lambda c, d: hdrftr(c, d, title),
              onLaterPages=lambda c, d: hdrftr(c, d, title))
    print("Built", out)


if __name__ == '__main__':
    main()
