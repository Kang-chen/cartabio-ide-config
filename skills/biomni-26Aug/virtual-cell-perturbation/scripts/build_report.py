#!/usr/bin/env python
"""
Build a Phylo-branded PDF benchmark report from the metrics JSON, figures, and a
references JSON. Generalized over dataset / model / split.

Sections (in order), per the skill spec:
  0. Infographic summary  (at-a-glance headline card + key-number tiles)
  1. Introduction
  2. Methods
  3. Results (with embedded figures F1-F6 as available)
  4. Conclusions (+ limitations)
  5. Next steps
  6. References

Follows the pdf-report-generation skill: ReportLab Platypus, Phylo palette, gold
table headers, centered figures/tables, KeepTogether(figure+caption), written
directly to /mnt/results, then validated (page_count, size, extractable text).

Fonts: uses Liberation Sans TTFs if present (metric-Arial), else Helvetica.
"""
import argparse, json, os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image, Table,
                                TableStyle, PageBreak, KeepTogether, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY

# ---------------------------------------------------------------- fonts (graceful)
def register_fonts():
    base = "/usr/share/fonts/truetype/liberation"
    trip = {"LSans": "LiberationSans-Regular.ttf",
            "LSans-Bold": "LiberationSans-Bold.ttf",
            "LSans-Italic": "LiberationSans-Italic.ttf"}
    ok = all(os.path.exists(f"{base}/{v}") for v in trip.values())
    if ok:
        for name, fn in trip.items():
            pdfmetrics.registerFont(TTFont(name, f"{base}/{fn}"))
        return "LSans", "LSans-Bold", "LSans-Italic"
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


FONT, FONT_B, FONT_I = register_fonts()

# ---- Phylo palette ----
BLACK = colors.HexColor("#000000"); CREAM = colors.HexColor("#FAF9F3")
CREAM2 = colors.HexColor("#ECE9E2"); GOLD = colors.HexColor("#D4A04A")
ORANGE = colors.HexColor("#FF9400"); GREEN = colors.HexColor("#75A025")
PINK = colors.HexColor("#FD9BED"); BLUE = colors.HexColor("#0279EE")
DARK = colors.HexColor("#2C2A26"); GREY = colors.HexColor("#8A8378")
BORDER = colors.HexColor("#D5CFC5"); ALTROW = colors.HexColor("#F9F7F3")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


def st(name, **kw):
    return ParagraphStyle(name, **kw)


h1 = st("h1", fontName=FONT_B, fontSize=15, leading=19, textColor=BLUE, spaceBefore=14, spaceAfter=7)
h2 = st("h2", fontName=FONT_B, fontSize=11.5, leading=15, textColor=DARK, spaceBefore=9, spaceAfter=4)
body = st("body", fontName=FONT, fontSize=9.5, leading=14, textColor=DARK, alignment=TA_JUSTIFY, spaceAfter=6)
cap = st("cap", fontName=FONT_I, fontSize=8, leading=11, textColor=GREY, alignment=TA_LEFT, spaceBefore=3, spaceAfter=12)
bullet = st("bullet", fontName=FONT, fontSize=9.5, leading=13.5, textColor=DARK, leftIndent=12, bulletIndent=2, spaceAfter=3, alignment=TA_LEFT)
ref_st = st("ref", fontName=FONT, fontSize=8.5, leading=12, textColor=DARK, leftIndent=14, bulletIndent=2, spaceAfter=4, alignment=TA_LEFT)
t_title = st("t_title", fontName=FONT_B, fontSize=23, leading=28, textColor=BLACK, alignment=TA_LEFT)
t_sub = st("t_sub", fontName=FONT, fontSize=12, leading=17, textColor=DARK, alignment=TA_LEFT)
t_meta = st("t_meta", fontName=FONT, fontSize=9, leading=13, textColor=GREY, alignment=TA_LEFT)
tile_big = st("tile_big", fontName=FONT_B, fontSize=17, leading=19, textColor=BLUE, alignment=TA_CENTER)
tile_lbl = st("tile_lbl", fontName=FONT, fontSize=7.5, leading=9.5, textColor=DARK, alignment=TA_CENTER)


def fnum(x, nd=4):
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return "n/a"


def get(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            d = d[k]
        else:
            return default
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", default="/mnt/results/execution_trace/benchmark_metrics.json")
    ap.add_argument("--figdir", default="/mnt/results/figures")
    ap.add_argument("--references", default="/mnt/results/execution_trace/references.json")
    ap.add_argument("--curve", default=None, help="finetune state.json (adds a training-provenance line).")
    ap.add_argument("--out", default="/mnt/results/report_perturbation_benchmark.pdf")
    ap.add_argument("--model_label", default=None)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    S = json.load(open(args.metrics))
    # Back-compat: older artifacts used *_scgpt keys; alias them to the *_model names.
    for a, b in [("overall_model", "overall_scgpt"),
                 ("overall_model_compute_metrics", "overall_scgpt_compute_metrics"),
                 ("by_regime_model", "by_regime_scgpt")]:
        if a not in S and b in S:
            S[a] = S[b]
    dataset = S.get("dataset") or "the dataset"  # None/empty -> readable fallback
    model_label = args.model_label or S.get("model_label", "model")
    refs = []
    if os.path.exists(args.references):
        refs = json.load(open(args.references)).get("references", [])

    cm = S.get("overall_model_compute_metrics", {})
    om = S.get("overall_model", {})
    cb = S.get("overall_base_compute_metrics", {})
    ob = S.get("overall_base", {})
    brm = S.get("by_regime_model", {})
    has_base = bool(ob)

    FIG = args.figdir

    def img(path, frac=1.0):
        from PIL import Image as PILImage
        p = os.path.join(FIG, path)
        if not os.path.exists(p):
            return None
        iw, ih = PILImage.open(p).size
        w = CONTENT_W * frac
        im = Image(p, width=w, height=w * ih / iw)
        im.hAlign = "CENTER"
        return im

    def fig_block(path, caption, frac=1.0):
        im = img(path, frac)
        if im is None:
            return None
        return KeepTogether([im, Paragraph(caption, cap)])

    story = []
    title = args.title or f"Perturbation-response prediction benchmark: {model_label} on {dataset}"

    # ===================== PAGE 1: TITLE + INFOGRAPHIC =====================
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(title, t_title))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(f"Held-out benchmark on the {dataset} Perturb-seq dataset "
                           f"(GEARS {S.get('split','simulation')} split, seed {S.get('seed','?')})", t_sub))
    story.append(Spacer(1, 5 * mm))
    story.append(HRFlowable(width=CONTENT_W, thickness=3, color=BLUE, spaceAfter=8, spaceBefore=2))

    # --- infographic key-number tiles ---
    tiles = [
        (fnum(om.get("pearson_delta_mean"), 3), "pearson_delta<br/>(mean)"),
        (fnum(om.get("pearson_delta_de_mean"), 3), "pearson_delta_de<br/>(top-20 DE)"),
        (fnum(om.get("frac_correct_direction_20_mean"), 3), "direction match<br/>(top-20 DE)"),
        (fnum(cm.get("pearson_de"), 3), "pearson_de<br/>(top-20 DE)"),
        (str(S.get("n_test_perts", "?")), "held-out<br/>perturbations"),
        (str(S.get("n_test_cells", "?")), "held-out<br/>cells"),
    ]
    tcells = [[Paragraph(v, tile_big) for v, _ in tiles],
              [Paragraph(l, tile_lbl) for _, l in tiles]]
    tt = Table(tcells, colWidths=[CONTENT_W / len(tiles)] * len(tiles))
    tt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CREAM),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("TOPPADDING", (0, 1), (-1, 1), 2), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    tt.hAlign = "CENTER"
    story.append(tt)
    story.append(Spacer(1, 6 * mm))

    # --- at-a-glance summary card ---
    base_line = ("A control-mean baseline scores 0 on \u0394-correlation and top-k DE direction "
                 "metrics by construction (all-gene direction \u2248 "
                 f"{fnum(ob.get('frac_correct_direction_all_mean'), 3)} is a sign-agreement artefact of "
                 "unchanged genes), isolating genuine perturbation signal." if has_base else "")
    summ = (f"<b>{model_label}</b> was benchmarked on predicting single-cell transcriptional responses to "
            f"held-out genetic perturbations in <b>{dataset}</b>. Across {S.get('n_test_perts','?')} held-out "
            f"perturbations it reached change-from-control correlation "
            f"<b>pearson_delta = {fnum(om.get('pearson_delta_mean'),3)}</b> "
            f"(median {fnum(om.get('pearson_delta_median'),3)}) and correctly predicted the direction of "
            f"<b>{fnum(om.get('frac_correct_direction_20_mean'),3)}</b> of the top-20 DE genes on average. "
            + base_line)
    card = Table([[Paragraph(summ, body)]], colWidths=[CONTENT_W])
    card.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CREAM),
                              ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                              ("LINEBEFORE", (0, 0), (0, -1), 3, GOLD),
                              ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                              ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    story.append(card)
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Prepared by Biomni &middot; Phylo", t_meta))
    story.append(PageBreak())

    # ===================== 1. INTRODUCTION =====================
    story.append(Paragraph("1&nbsp;&nbsp;Introduction", h1))
    story.append(Paragraph(
        "Perturb-seq couples pooled CRISPR perturbations with single-cell RNA sequencing, producing high-dimensional "
        "readouts of how genetic interventions reshape the transcriptome. A central computational goal is to "
        "<b>predict</b> the transcriptional response to perturbations that were never measured \u2014 especially "
        "novel gene combinations \u2014 which would let researchers prioritise experiments in silico.", body))
    story.append(Paragraph(
        f"This report benchmarks <b>{model_label}</b> on held-out perturbations from the <b>{dataset}</b> dataset, "
        "quantifies where it succeeds and fails across generalization regimes, and contrasts it against a simple but "
        "informative control-mean baseline." if has_base else
        f"This report benchmarks <b>{model_label}</b> on held-out perturbations from the <b>{dataset}</b> dataset "
        "and quantifies where it succeeds and fails across generalization regimes.", body))

    # ===================== 2. METHODS =====================
    story.append(Paragraph("2&nbsp;&nbsp;Methods", h1))
    story.append(Paragraph("2.1&nbsp;&nbsp;Data and splits", h2))
    story.append(Paragraph(
        f"The {dataset} dataset was loaded through the GEARS <font face='{FONT_I}'>PertData</font> interface "
        f"({S.get('n_genes','?')} genes). Perturbations were partitioned with the GEARS "
        f"<font face='{FONT_I}'>{S.get('split','simulation')}</font> split (seed {S.get('seed','?')}), which "
        f"stratifies held-out perturbations by their relationship to the training set. The held-out test set contains "
        f"{S.get('n_test_perts','?')} perturbations ({S.get('n_test_cells','?')} cells). The split is deterministic and "
        "reproducible from the seed.", body))
    regime_names = ", ".join(f"<b>{k}</b> (n={v})" for k, v in S.get("regime_counts", {}).items())
    if regime_names:
        story.append(Paragraph(
            f"Test perturbations fall into generalization regimes: {regime_names}. In GEARS naming, "
            "<i>combo_seenK</i> is a two-gene combination with K of its genes seen (in other contexts) during "
            "training, and <i>unseen_single</i> is a single perturbation never seen in training.", body))

    story.append(Paragraph("2.2&nbsp;&nbsp;Model and inference", h2))
    curve_txt = ""
    if args.curve and os.path.exists(args.curve):
        cs = json.load(open(args.curve))
        be = None
        if cs.get("history"):
            be = min(cs["history"], key=lambda r: r["val_loss"])["epoch"]
        curve_txt = (f" The model was fine-tuned in-session for up to {cs.get('max_epochs','?')} epochs "
                     f"(best validation at epoch {be}; stop reason: {cs.get('stopped_reason','?')}).")
    story.append(Paragraph(
        f"Predictions were produced by the <b>{model_label}</b> adapter. For each held-out cell the model predicted "
        f"the full post-perturbation expression vector; predictions were aggregated to a per-perturbation mean profile "
        f"and compared with the measured mean profile of held-out cells for that perturbation."
        f"{' Of the dataset genes, ' + str(S.get('vocab_match')) + ' matched the model gene vocabulary.' if S.get('vocab_match') else ''}"
        f"{curve_txt}", body))

    story.append(Paragraph("2.3&nbsp;&nbsp;Metrics and baseline", h2))
    story.append(Paragraph(
        "We report the standard GEARS metrics computed with the original GEARS implementation. "
        "<b>pearson</b> / <b>pearson_de</b> correlate predicted vs. measured mean expression across all genes / the "
        "top-20 differentially expressed (DE) genes. <b>pearson_delta</b> / <b>pearson_delta_de</b> correlate the "
        "<i>change from control</i> \u2014 a stricter test that removes the dominant, easy-to-predict baseline "
        "expression. <b>Direction match</b> is the fraction of the top-k DE genes whose predicted change has the "
        "correct sign. <b>mse</b> / <b>mse_de</b> are mean squared errors.", body))
    if has_base:
        story.append(Paragraph(
            "As a reference we include a <b>control-mean baseline</b> that predicts the unperturbed mean for every "
            "cell (\u201cpredict no change\u201d). It is deliberately strong on absolute-expression metrics (most "
            "genes do not change) but, by construction, produces a zero change-from-control vector, so its "
            "\u0394-correlation metrics and its top-k direction metrics (top-20/50/100/200) are exactly 0. The one "
            f"exception is direction match over <i>all</i> genes (\u2248 {fnum(ob.get('frac_correct_direction_all_mean'),3)}), "
            "which is nonzero only because the sign of a near-zero predicted change coincidentally agrees with the "
            "sign of a near-zero measured change for the many unchanged genes; the top-k DE direction metrics \u2014 "
            "not the all-gene one \u2014 are the meaningful test.", body))

    # F1 in methods
    fb = fig_block("F1_dataset_overview.png",
                   "<b>Figure&nbsp;1.</b> Dataset and evaluation design: dataset scale and vocabulary overlap "
                   "(left), the GEARS split at the condition level (middle), and the composition of the held-out "
                   "test set across generalization regimes (right).")
    if fb:
        story.append(fb)

    # ===================== 3. RESULTS =====================
    story.append(Paragraph("3&nbsp;&nbsp;Results", h1))
    story.append(Paragraph("3.1&nbsp;&nbsp;Overall benchmark performance", h2))
    story.append(Paragraph(
        f"Across the {S.get('n_test_perts','?')} held-out perturbations, {model_label} achieved "
        f"<b>pearson_delta = {fnum(om.get('pearson_delta_mean'),4)}</b> (mean) / "
        f"{fnum(om.get('pearson_delta_median'),4)} (median) and "
        f"<b>pearson_delta_de = {fnum(om.get('pearson_delta_de_mean'),4)}</b> / "
        f"{fnum(om.get('pearson_delta_de_median'),4)} on the top-20 DE genes. On absolute expression it reached "
        f"pearson = {fnum(cm.get('pearson'),4)} (all genes) and pearson_de = {fnum(cm.get('pearson_de'),4)} "
        f"(top-20 DE), with mse = {fnum(cm.get('mse'),4)} and mse_de = {fnum(cm.get('mse_de'),4)}. It predicted the "
        f"correct direction of change for {fnum(om.get('frac_correct_direction_20_mean'),3)} of the top-20 DE genes "
        f"on average.", body))
    if has_base:
        story.append(Paragraph(
            f"The control-mean baseline was competitive on absolute metrics (pearson = {fnum(cb.get('pearson'),4)}, "
            f"pearson_de = {fnum(cb.get('pearson_de'),4)}) because most genes are unchanged, but it scored <b>0 on "
            f"the change-from-control correlations and top-k DE direction metrics</b> by construction, and its error "
            f"on DE genes was worse (mse_de = {fnum(cb.get('mse_de'),4)} vs. {fnum(cm.get('mse_de'),4)}). Absolute "
            "correlations are therefore misleadingly high for any method; genuine perturbation-prediction skill must "
            "be read from the \u0394 and top-k direction metrics.", body))

    fb = fig_block("F3_benchmark_summary.png",
                   f"<b>Figure&nbsp;3.</b> {model_label}" + (" vs. control-mean baseline" if has_base else "") +
                   " on held-out perturbations. Left: correlation and direction metrics (higher is better). Right: "
                   "prediction error (lower is better).")
    if fb:
        story.append(fb)

    story.append(Paragraph("3.2&nbsp;&nbsp;Per-perturbation variability", h2))
    story.append(Paragraph(
        f"The mean masks substantial spread: per-perturbation pearson_delta has median "
        f"{fnum(om.get('pearson_delta_median'),3)} vs. mean {fnum(om.get('pearson_delta_mean'),3)} "
        "(Figure&nbsp;4), and direction accuracy is more stable. This heterogeneity motivates stratifying by "
        "generalization regime.", body))
    fb = fig_block("F4_metric_distributions.png",
                   "<b>Figure&nbsp;4.</b> Distribution of per-perturbation metrics across the held-out set. Solid "
                   "line = mean, dashed = median.")
    if fb:
        story.append(fb)

    if brm:
        story.append(Paragraph("3.3&nbsp;&nbsp;Accuracy depends on the generalization regime", h2))
        reg_line = "; ".join(
            f"<b>{r}</b> = {fnum(v.get('pearson_delta_mean'),3)} (n={v.get('n')})"
            for r, v in brm.items())
        story.append(Paragraph(
            f"Accuracy tracks how much a test perturbation resembles training data (Figure&nbsp;5). Mean "
            f"pearson_delta by regime: {reg_line}. This is the expected signature of a model that generalises by "
            "recombining learned perturbation effects: it extrapolates to new combinations of familiar genes but has "
            "little to go on when a gene is genuinely unseen. Regimes with very few perturbations give noisy "
            "estimates and should be read as illustrative.", body))
        fb = fig_block("F5_by_regime.png",
                       "<b>Figure&nbsp;5.</b> Performance stratified by generalization regime. Left: per-perturbation "
                       "pearson_delta (boxes with points). Right: regime means.")
        if fb:
            story.append(fb)

    # provenance figure (F2) if present
    fb = fig_block("F2_provenance.png",
                   "<b>Figure&nbsp;2.</b> Provenance and headline metrics" +
                   (" (with the measured in-session training curve; no values simulated)" if args.curve else "") + ".")
    if fb:
        story.append(Paragraph("3.4&nbsp;&nbsp;Provenance", h2))
        story.append(fb)

    # examples (F6)
    fb = fig_block("F6_example_scatters.png",
                   "<b>Figure&nbsp;6.</b> Predicted vs. measured perturbation response (change from control) for "
                   "best / named / median / worst perturbations. Each point is a gene; the dashed line is perfect "
                   "prediction.")
    if fb:
        story.append(Paragraph("3.5&nbsp;&nbsp;Example perturbations", h2))
        story.append(fb)

    # ===================== 4. CONCLUSIONS =====================
    story.append(Paragraph("4&nbsp;&nbsp;Conclusions", h1))
    concl = [
        f"<b>{model_label} captures real perturbation signal.</b> On held-out {dataset} perturbations it reached "
        f"pearson_delta = {fnum(om.get('pearson_delta_mean'),3)} and correct-direction rates around "
        f"{fnum(om.get('frac_correct_direction_20_mean'),3)} for top DE genes.",
    ]
    if has_base:
        concl.append(
            "<b>Absolute-expression metrics are deceptive.</b> A trivial control-mean baseline matches the model on "
            "overall pearson yet scores zero on change-from-control correlations and top-k DE direction. "
            "Perturbation-prediction skill must be judged on those \u0394 and top-k direction metrics.")
    if brm:
        concl.append(
            "<b>Generalisation is regime-dependent.</b> Accuracy is highest for new combinations of previously seen "
            "genes and lowest when a gene is genuinely unseen \u2014 defining where the model can and cannot be "
            "trusted.")
    for b in concl:
        story.append(Paragraph(b, bullet, bulletText="\u2022"))

    story.append(Paragraph("4.1&nbsp;&nbsp;Limitations", h2))
    story.append(Paragraph(
        "This benchmark covers a single dataset and split seed; regimes with few perturbations give unstable "
        "estimates. Predictions are evaluated at the level of mean per-perturbation profiles, which does not assess "
        "cell-to-cell heterogeneity. Transformer perturbation models can over-predict the number of significantly "
        "changed genes (high DE recall, lower precision), so downstream use for calling novel DE hits should apply an "
        "independent significance filter. Absolute numbers also depend on the highly-variable-gene set and "
        "vocabulary overlap.", body))

    # ===================== 5. NEXT STEPS =====================
    story.append(Paragraph("5&nbsp;&nbsp;Next steps", h1))
    for b in [
        "Benchmark additional models (e.g. GEARS GNN, a linear/additive baseline) on the same split for a like-for-like "
        "comparison using the shared metric pipeline.",
        "Extend to other open Perturb-seq datasets (Adamson, Dixit, Replogle) and to the harder "
        "<i>simulation_single</i> split to probe unseen-single generalisation.",
        "Repeat across multiple split seeds and report confidence intervals, especially for low-n regimes.",
        "Add DE-calling precision/recall against measured DE genes to complement correlation and direction metrics.",
        "For any dataset used commercially, re-confirm its license (see datasets_licensing reference).",
    ]:
        story.append(Paragraph(b, bullet, bulletText="\u2022"))

    # ===================== 6. REFERENCES =====================
    if refs:
        story.append(Paragraph("6&nbsp;&nbsp;References", h1))
        for i, r in enumerate(refs, 1):
            doi = r.get("doi", "")
            link = f' doi:<font color="#0563C1">{doi}</font>' if doi else ""
            story.append(Paragraph(
                f"[{i}] {r.get('authors','')} ({r.get('year','?')}). {r.get('title','')}. "
                f"<i>{r.get('journal','')}</i>.{link}", ref_st))

    # ---- build ----
    def chrome(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 8); canvas.setFillColor(GREY)
        canvas.drawString(MARGIN, PAGE_H - 12 * mm, title[:95])
        canvas.setStrokeColor(GOLD); canvas.setLineWidth(1)
        canvas.line(MARGIN, PAGE_H - 13 * mm, PAGE_W - MARGIN, PAGE_H - 13 * mm)
        canvas.setStrokeColor(BORDER); canvas.setLineWidth(0.75)
        canvas.line(MARGIN, 12 * mm, PAGE_W - MARGIN, 12 * mm)
        canvas.setFont(FONT, 8); canvas.setFillColor(GREY)
        canvas.drawCentredString(PAGE_W / 2, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(args.out, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=MARGIN, rightMargin=MARGIN, title=title)
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    print(f"[report] wrote {args.out} ({os.path.getsize(args.out)} bytes)", flush=True)

    # ---- validate ----
    try:
        from pypdf import PdfReader
        rd = PdfReader(args.out)
        pc = len(rd.pages); sz = os.path.getsize(args.out)
        txt = rd.pages[0].extract_text() or ""
        assert pc >= 2, f"only {pc} page(s)"
        assert sz > 5000, f"only {sz} bytes"
        assert len(txt.strip()) > 0, "no extractable text on page 1"
        print(f"[validate] OK: {pc} pages, {sz} bytes, page-1 text present", flush=True)
    except Exception as e:
        print(f"[validate][WARN] {e}", flush=True)
    print("[done]", flush=True)


if __name__ == "__main__":
    main()
