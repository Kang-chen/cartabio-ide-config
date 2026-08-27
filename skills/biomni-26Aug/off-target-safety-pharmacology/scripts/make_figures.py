#!/usr/bin/env python3
"""
make_figures.py — Generate the data-driven figures for the liability report.

All figures are driven by ACTUAL data files produced upstream; nothing is hardcoded to a
particular compound. Figures adapt to what exists (e.g. if there is no ground truth, the
benchmark ROC panel is skipped and a de-novo ranking panel is drawn instead).

Figures (saved as both .png @300dpi and .svg with editable text):
  fig1_consensus_ranking : horizontal bar of top-N consensus off-target scores, colored
                           by target class, with measured-active markers where known.
  fig2_admet_flags       : key ADMET endpoints (engine-aware; percentile bars only if the
                           engine produced percentiles, else raw-probability bars).
  fig3_benchmark         : Tier A -> ROC curve (+AUC); Tier B -> predicted-vs-measured
                           scatter on the measured overlap; Tier C -> "no ground truth"
                           discovery panel (top consensus with similarity Tc annotation).

Usage:
  python make_figures.py --outdir <outdir> --compound "<NAME>" [--topn 15]
Reads:   <outdir>/data/offtarget_consensus.csv, admet_all_properties.csv, admet_meta.json,
         benchmark_prediction_vs_measured.csv (optional), benchmark_summary.json
Writes:  <outdir>/figures/*.png, *.svg
"""
import argparse, os, sys, json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["axes.spines.top"] = False
matplotlib.rcParams["axes.spines.right"] = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_qc import collapse_orthologs, assert_figure_ok

# agreement -> connector/label color (figure legibility only; not a palette change)
AGR_COLOR = {"Both": "#75A025", "Similarity only": "#D4A04A",
             "DTI only": "#0279EE", "Neither": "#8A8378"}
AGR_SHORT = {"Both": "both", "Similarity only": "sim only",
             "DTI only": "DTI only", "Neither": "neither"}

# Okabe-Ito class palette (matches pdf-report-generation skill)
CLASS_COLORS = {
    "Ion channel": "#D55E00", "GPCR (aminergic)": "#0279EE",
    "Transporter": "#75A025", "Enzyme": "#CC79A7", "Adaptive (kNN)": "#8A8378",
}
DEFAULT_COLOR = "#8A8378"


def _save(fig, outdir, name):
    os.makedirs(f"{outdir}/figures", exist_ok=True)
    for ext in ("png", "svg"):
        fig.savefig(f"{outdir}/figures/{name}.{ext}", dpi=300, bbox_inches="tight")
    plt.close(fig)


def fig_consensus(outdir, compound, topn):
    """Per-engine ranking of predicted OFF-targets.

    The blended consensus is never shown on its own: each target is a dumbbell with the
    similarity probability and the (normalized) DeepPurpose score, a small consensus marker,
    and an explicit agreement state. The intended primary target is excluded (it is on-target),
    and adaptively-added targets are marked (their similarity score is high by construction).
    """
    con = pd.read_csv(f"{outdir}/data/offtarget_consensus.csv")
    if "is_primary" in con.columns:
        con = con[~con["is_primary"].fillna(False).astype(bool)]
    con = con.dropna(subset=["consensus"]).sort_values("consensus", ascending=True).tail(topn)
    n = len(con)
    labels = []
    for _, r in con.iterrows():
        lab = str(r["label"])
        if str(r.get("source", "")) == "adaptive":
            lab += "  \u2020 adaptive"
        labels.append(lab)
    psim = con["P_sim"].fillna(0).to_numpy(dtype=float)
    dpn = con["dp_norm"].fillna(0).to_numpy(dtype=float)
    cons = con["consensus"].fillna(0).to_numpy(dtype=float)
    agr = (con["agreement"].astype(str).tolist() if "agreement" in con.columns
           else [""] * n)
    y = list(range(n))

    fig, ax = plt.subplots(figsize=(7.6, max(3.5, 0.44 * n)))
    for i in range(n):
        ax.plot([min(psim[i], dpn[i]), max(psim[i], dpn[i])], [i, i],
                color="#CCCCCC", lw=1.5, zorder=1)
    ax.scatter(psim, y, marker="o", s=46, color="#D4A04A", edgecolor="white", zorder=3,
               label="Similarity  P(sim)")
    ax.scatter(dpn, y, marker="s", s=40, color="#0279EE", edgecolor="white", zorder=3,
               label="DeepPurpose (norm.)")
    ax.scatter(cons, y, marker="D", s=16, color="#555555", zorder=2, label="Consensus (mean)")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlim(0, 1.42)
    ax.set_xlabel("Engine score (0\u20131)")
    ax.set_title(f"{compound}: top {n} predicted off-targets \u2014 per-engine scores & agreement",
                 loc="left", fontsize=11, fontweight="bold")
    for i, a in enumerate(agr):
        ax.text(1.03, i, AGR_SHORT.get(a, a), va="center", ha="left", fontsize=7.2,
                color=AGR_COLOR.get(a, "#8A8378"))
    ax.text(1.03, n - 0.2 + 0.6, "agreement", va="bottom", ha="left", fontsize=7.2,
            color="#8A8378", style="italic")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12 - 0.015 * (n > 8)),
              ncol=3, fontsize=8, frameon=False)
    _save(fig, outdir, "fig1_consensus_ranking")
    return "fig1_consensus_ranking"


def fig_admet(outdir, compound):
    meta = json.load(open(f"{outdir}/data/admet_meta.json"))
    df = pd.read_csv(f"{outdir}/data/admet_all_properties.csv")
    has_pct = meta.get("has_percentiles", False)
    # pick decision-relevant endpoints if present (name-tolerant)
    wanted = ["herg", "cyp3a4", "cyp2d6", "cyp2c9", "clintox", "clinical tox",
              "bbb", "bioavailab", "pgp", "ppbr"]
    df["_l"] = df["property"].astype(str).str.lower()
    keep = df[df["_l"].apply(lambda s: any(w in s for w in wanted))].copy()
    if keep.empty:
        keep = df.head(10).copy()
    col = "percentile" if (has_pct and keep["percentile"].notna().any()) else "value"
    keep = keep.dropna(subset=[col]).sort_values(col)
    fig, ax = plt.subplots(figsize=(7.2, max(3.0, 0.42 * len(keep))))
    vals = keep[col].astype(float)
    # color by risk if percentile, else neutral gold
    if col == "percentile":
        colors = ["#D55E00" if v >= 90 else "#E69F00" if v >= 50 else "#56B4E9"
                  for v in vals]
        xlab = "ChEMBL approved-drug percentile"
        ax.axvline(90, ls="--", lw=0.8, color="#999999")
    else:
        colors = "#D4A04A"
        xlab = "Model output (probability or predicted value)"
    ax.barh(keep["property"].astype(str), vals, color=colors, edgecolor="white")
    ax.set_xlabel(xlab)
    eng = meta.get("engine", "?")
    ax.set_title(f"{compound}: ADMET flags  —  engine: {eng}", loc="left",
                 fontsize=11, fontweight="bold")
    _save(fig, outdir, "fig2_admet_flags")
    return "fig2_admet_flags"


def _offtarget(df):
    """Drop the primary target / orthologs so the benchmark shows OFF-target recovery only."""
    if "is_primary" in df.columns:
        return df[~df["is_primary"].fillna(False).astype(bool)].copy()
    return df.copy()


def fig_benchmark(outdir, compound):
    summ = json.load(open(f"{outdir}/data/benchmark_summary.json"))
    tier = summ.get("tier", "C")
    bpath = f"{outdir}/data/benchmark_prediction_vs_measured.csv"
    fig, ax = plt.subplots(figsize=(6.4, 5.0))
    if tier == "A" and os.path.exists(bpath):
        from sklearn.metrics import roc_curve
        b = pd.read_csv(bpath)
        sub = b[b["measured"] == True].copy() if "measured" in b else b.copy()
        sub = _offtarget(sub)   # off-target recovery only (matches benchmark metrics)
        y = sub["measured_active"].astype(int).values
        score = sub["consensus"].fillna(sub.get("P_sim", 0)).values
        fpr, tpr, _ = roc_curve(y, score)
        auc = summ["metrics"].get("roc_auc_consensus")
        auc_sim = summ["metrics"].get("roc_auc_similarity")
        ax.plot(fpr, tpr, color="#0279EE", lw=2, label=f"Consensus (AUC={auc})")
        if auc_sim is not None:
            from sklearn.metrics import roc_curve as rc2
            f2, t2, _ = rc2(y, sub["P_sim"].fillna(0).values)
            ax.plot(f2, t2, color="#D4A04A", lw=1.6, ls="-",
                    label=f"Similarity (AUC={auc_sim})")
        ax.plot([0, 1], [0, 1], ls="--", color="#999999", lw=1)
        ax.set_xlabel("False positive rate"); ax.set_ylabel("True positive rate")
        ax.set_title(f"{compound}: recovery of measured off-targets (Tier A)",
                     loc="left", fontsize=11, fontweight="bold")
        ax.legend(loc="lower right", fontsize=9, frameon=False)
    elif tier == "B" and os.path.exists(bpath):
        b = pd.read_csv(bpath)
        sub = _offtarget(b[b.get("measured", False) == True].copy())
        # collapse ortholog pairs (same ChEMBL pref_name) to ONE point so they don't plot on
        # top of each other with colliding labels
        prefcol = "chembl_pref_name" if "chembl_pref_name" in sub.columns else "label"
        sub = collapse_orthologs(sub, prefcol, by="measured_pchembl")
        xvals = sub["consensus"].fillna(0)
        ax.scatter(xvals, sub["measured_pchembl"], s=42,
                   color="#0279EE", alpha=0.85, edgecolor="white", zorder=3)
        xmax = float(xvals.max()) if len(xvals) else 1.0
        ax.set_xlim(-0.02, max(1.0, xmax + 0.40))
        # de-collide labels: alternate the vertical text offset, anchor by x-position
        order = np.argsort(sub["measured_pchembl"].to_numpy(dtype=float))
        for rank, idx in enumerate(order):
            r = sub.iloc[int(idx)]
            xr = float(r["consensus"]) if pd.notna(r["consensus"]) else 0.0
            lab = str(r["label"])
            north = int(r.get("n_orthologs", 1) or 1)
            if north > 1:
                lab += f" (+{north - 1} ortholog)"
            right_half = xr > (ax.get_xlim()[1] * 0.55)
            dy = 4 if rank % 2 == 0 else -10
            ax.annotate(lab, (xr, float(r["measured_pchembl"])), fontsize=7,
                        ha="right" if right_half else "left",
                        xytext=(-5 if right_half else 5, dy),
                        textcoords="offset points", annotation_clip=False)
        ax.axhline(6, ls="--", color="#999999", lw=0.8)
        ax.set_xlabel("Predicted consensus score")
        ax.set_ylabel("Measured pChEMBL (median)")
        ax.set_title(f"{compound}: predicted vs measured off-targets (Tier B, partial)",
                     loc="left", fontsize=11, fontweight="bold")
    else:
        con = pd.read_csv(f"{outdir}/data/offtarget_consensus.csv")
        con = _offtarget(con).dropna(subset=["consensus"]).sort_values("consensus").tail(12)
        colors = [CLASS_COLORS.get(c, DEFAULT_COLOR) for c in con.get("target_class", [])]
        ax.barh(con["label"].astype(str), con["consensus"], color=colors, edgecolor="white")
        ax.set_xlim(0, 1); ax.set_xlabel("Consensus score")
        ax.set_title(f"{compound}: de novo off-target predictions (Tier C, unvalidated)",
                     loc="left", fontsize=11, fontweight="bold")
    _save(fig, outdir, "fig3_benchmark")
    return "fig3_benchmark", tier


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--compound", required=True)
    ap.add_argument("--topn", type=int, default=15)
    args = ap.parse_args()
    made = []
    made.append(fig_consensus(args.outdir, args.compound, args.topn))
    made.append(fig_admet(args.outdir, args.compound))
    fb, tier = fig_benchmark(args.outdir, args.compound)
    made.append(fb)
    # legibility gate: refuse a blank / degenerate figure (raises loudly on failure)
    qc = {name: assert_figure_ok(f"{args.outdir}/figures/{name}.png") for name in made}
    json.dump(qc, open(f"{args.outdir}/figures/figure_qc.json", "w"), indent=2)
    print(json.dumps({"status": "ok", "figures": made, "benchmark_tier": tier, "qc": qc}))


if __name__ == "__main__":
    main()
