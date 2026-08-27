#!/usr/bin/env python3
"""generate_plots.py — publication-quality figures for the neoantigen-TESLA skill.

Produces four data-driven figures (PNG + SVG) from real pipeline outputs:

  fig1_tier_distribution   -- prioritization funnel: candidates by TESLA tier and by gene
  fig2_binding_by_tier     -- MHCflurry %rank distribution across tiers (log scale)
  fig3_feature_separation  -- TESLA feature values: immunogenic vs non-immunogenic
                              (from the REAL TESLA benchmark set, 714 labelled peptides)
  fig4_ranking_performance -- ROC curve + top-K enrichment on the real benchmark

All figures are rendered from data on disk (the analysis CSV and the benchmark
fixture JSON) — nothing is simulated. Colours follow a colour-blind-safe palette.

CLI:
    python generate_plots.py \
        --neoantigens <neoantigens.csv> \
        --benchmark   <benchmark_summary.json> \
        --outdir      <figures/>

If arguments are omitted, the packaged demo fixtures are used.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# ---- style ---------------------------------------------------------------
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"       # keep SVG text editable
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["axes.spines.top"] = False
matplotlib.rcParams["axes.spines.right"] = False
matplotlib.rcParams["figure.dpi"] = 120

# Colour-blind-safe (Okabe-Ito derived) tier palette
TIER_ORDER = ["Tier1", "Tier2", "Tier3", "excluded_low_abundance", "excluded_nonbinder"]
TIER_COLORS = {
    "Tier1": "#009E73",                 # green (top)
    "Tier2": "#0072B2",                 # blue
    "Tier3": "#56B4E9",                 # light blue
    "excluded_low_abundance": "#E69F00",  # orange
    "excluded_nonbinder": "#BBBBBB",    # grey
}
TIER_LABELS = {
    "Tier1": "Tier 1",
    "Tier2": "Tier 2",
    "Tier3": "Tier 3",
    "excluded_low_abundance": "Excl. low abundance",
    "excluded_nonbinder": "Excl. non-binder",
}
POS_COLOR = "#D55E00"   # immunogenic
NEG_COLOR = "#999999"   # non-immunogenic

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
DEFAULT_NEO = os.path.join(_SKILL, "tests", "fixtures", "demo_results", "neoantigens.csv")
DEFAULT_BENCH = os.path.join(_SKILL, "tests", "fixtures", "benchmark_summary.json")


def _save(fig, outdir: str, name: str) -> list[str]:
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for ext in ("png", "svg"):
        p = os.path.join(outdir, f"{name}.{ext}")
        fig.savefig(p, bbox_inches="tight", dpi=200 if ext == "png" else None)
        paths.append(p)
    plt.close(fig)
    return paths


# =============================================================================
# Fig 1 — tier distribution (funnel + per-gene stacked bar)
# =============================================================================
def fig_tier_distribution(df: pd.DataFrame, outdir: str) -> list[str]:
    present = [t for t in TIER_ORDER if t in set(df["tier"])]
    counts = df["tier"].value_counts().reindex(present).fillna(0).astype(int)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.4),
                                   gridspec_kw={"width_ratios": [1, 1.25]})

    # (a) horizontal funnel of counts
    ypos = np.arange(len(present))[::-1]
    ax1.barh(ypos, counts.values, color=[TIER_COLORS[t] for t in present],
             edgecolor="white", height=0.72)
    for y, t in zip(ypos, present):
        ax1.text(counts[t] + max(counts.values) * 0.01, y, str(counts[t]),
                 va="center", ha="left", fontsize=10, fontweight="bold")
    ax1.set_yticks(ypos)
    ax1.set_yticklabels([TIER_LABELS[t] for t in present], fontsize=10)
    ax1.set_xlabel("Candidate peptides (n)", fontsize=10)
    ax1.set_title("A. Prioritization funnel", fontsize=11, fontweight="bold", loc="left")
    ax1.set_xlim(0, max(counts.values) * 1.15)

    # (b) per-gene composition of PRIORITIZED candidates (Tier1/2/3 only).
    # The exclusions are already summarised in the Panel A funnel; showing them
    # per gene both swamps the scale and adds no signal. Real mutanomes can have
    # hundreds of mutated genes, so we rank genes carrying prioritized candidates
    # by that count, keep the top N, and fold the remaining prioritized-carrying
    # genes into a single "other (N genes)" bar. Counts are unchanged; this only
    # limits how many bars are drawn and which tiers are stacked.
    TOP_N_GENES = 20
    prio_tiers = [t for t in ("Tier1", "Tier2", "Tier3") if t in present]
    dprio = df[df["tier"].isin(prio_tiers)]
    _rank = (dprio.groupby("gene").size().sort_values(ascending=False))
    _prio_genes = _rank.index.tolist()
    _top = _prio_genes[:TOP_N_GENES]
    _n_other = len(_prio_genes) - len(_top)
    genes = _top + ([f"other ({_n_other} genes)"] if _n_other > 0 else [])
    _other_set = set(_prio_genes) - set(_top)
    bottom = np.zeros(len(genes))
    x = np.arange(len(genes))
    for t in prio_tiers:
        vals = [int(((dprio.gene == g) & (dprio.tier == t)).sum()) for g in _top]
        if _n_other > 0:
            vals.append(int(((dprio.gene.isin(_other_set)) & (dprio.tier == t)).sum()))
        ax2.bar(x, vals, bottom=bottom, color=TIER_COLORS[t],
                edgecolor="white", linewidth=0.4, label=TIER_LABELS[t])
        bottom += np.array(vals)
    ax2.set_xticks(x)
    # rotate labels so many-gene cases stay legible
    _grot = 0 if len(genes) <= 6 else 45
    _gfs = 10 if len(genes) <= 8 else 8.5
    ax2.set_xticklabels(genes, fontsize=_gfs, rotation=_grot,
                        ha=("center" if _grot == 0 else "right"),
                        rotation_mode=("default" if _grot == 0 else "anchor"))
    ax2.set_ylabel("Prioritized candidates (n)", fontsize=10)
    ax2.set_title("B. Prioritized-tier composition per gene", fontsize=11, fontweight="bold", loc="left")
    ax2.legend(fontsize=8, frameon=False, ncol=1, loc="upper right",
               bbox_to_anchor=(1.32, 1.0))

    fig.suptitle("Neoantigen prioritization by TESLA tier", fontsize=12.5,
                 fontweight="bold", y=1.02)
    fig.tight_layout()
    return _save(fig, outdir, "fig1_tier_distribution")


# =============================================================================
# Fig 2 — binding %rank distribution by tier
# =============================================================================
def fig_binding_by_tier(df: pd.DataFrame, outdir: str) -> list[str]:
    d = df.dropna(subset=["mut_rank"]).copy()
    d = d[d["mut_rank"] > 0]
    present = [t for t in TIER_ORDER if t in set(d["tier"])]

    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    data = [d.loc[d.tier == t, "mut_rank"].values for t in present]
    positions = np.arange(len(present))

    bp = ax.boxplot(data, positions=positions, widths=0.55, patch_artist=True,
                    showfliers=False, medianprops=dict(color="black", linewidth=1.4),
                    whiskerprops=dict(color="#555555"), capprops=dict(color="#555555"))
    for patch, t in zip(bp["boxes"], present):
        patch.set_facecolor(TIER_COLORS[t])
        patch.set_alpha(0.85)
        patch.set_edgecolor("white")
    # jittered points
    rng = np.random.default_rng(0)
    for i, t in enumerate(present):
        y = d.loc[d.tier == t, "mut_rank"].values
        xj = i + (rng.random(len(y)) - 0.5) * 0.32
        ax.scatter(xj, y, s=10, color="#222222", alpha=0.35, zorder=3, linewidths=0)

    # threshold guides (labels placed just left of the right frame edge)
    ax.set_xlim(-0.6, len(present) - 0.5 + 0.9)  # extra right margin for labels
    for thr, lab, c in [(0.5, "strong (0.5)", "#009E73"),
                        (2.0, "binder (2.0)", "#0072B2"),
                        (10.0, "weak (10)", "#E69F00")]:
        ax.axhline(thr, ls="--", lw=1, color=c, alpha=0.8)
        ax.text(len(present) - 0.35, thr, lab, fontsize=7.5, color=c,
                va="bottom", ha="right")

    ax.set_yscale("log")
    ax.set_xticks(positions)
    ax.set_xticklabels([TIER_LABELS[t] for t in present], fontsize=9, rotation=20, ha="right")
    ax.set_ylabel("MHCflurry presentation %rank (log)", fontsize=10)
    ax.set_title("MHCflurry binding strength across TESLA tiers",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()
    return _save(fig, outdir, "fig2_binding_by_tier")


# =============================================================================
# Fig 3 — TESLA feature separation on the real benchmark
# =============================================================================
def fig_feature_separation(bench: dict, outdir: str) -> list[str]:
    scored = pd.DataFrame(bench["scored"])
    scored["label"] = scored["label"].astype(int)
    sep = bench["summary"]["feature_separation"]

    # Feature columns available per-peptide in the fixture. mut_rank is the raw
    # binding %rank (lower = better); binding_affinity_score is its normalised
    # TESLA feature (higher = better); fraction_hydrophobic is the recognition proxy.
    candidate_panels = [
        ("mut_rank", "MHCflurry %rank\n(lower = better)", True),
        ("binding_affinity_score", "Binding affinity score", False),
        ("binding_stability", "Binding stability score", False),
        ("foreignness", "Foreignness /\ndissimilarity-to-self", False),
        ("agretopicity_score", "Agretopicity score", False),
        ("fraction_hydrophobic", "Fraction hydrophobic", False),
    ]
    panels = [p for p in candidate_panels if p[0] in scored.columns
              and scored[p[0]].notna().any()]
    fig, axes = plt.subplots(1, len(panels), figsize=(4.1 * len(panels), 4.3))
    if len(panels) == 1:
        axes = [axes]

    for ax, (col, title, logy) in zip(axes, panels):
        pos = scored.loc[scored.label == 1, col].dropna().astype(float).values
        neg = scored.loc[scored.label == 0, col].dropna().astype(float).values
        if logy:
            pos = np.clip(pos, 1e-3, None)
            neg = np.clip(neg, 1e-3, None)
        parts = ax.violinplot([neg, pos], positions=[0, 1], showextrema=False,
                              widths=0.85)
        for b, c in zip(parts["bodies"], [NEG_COLOR, POS_COLOR]):
            b.set_facecolor(c)
            b.set_alpha(0.45)
            b.set_edgecolor(c)
        # overlay medians and jittered points
        rng = np.random.default_rng(1)
        for i, (arr, c) in enumerate([(neg, NEG_COLOR), (pos, POS_COLOR)]):
            xj = i + (rng.random(len(arr)) - 0.5) * 0.22
            ax.scatter(xj, arr, s=8, color=c, alpha=0.5, linewidths=0, zorder=3)
            ax.hlines(np.median(arr), i - 0.3, i + 0.3, color="black", lw=1.6, zorder=4)
        if logy:
            ax.set_yscale("log")
        ax.set_xticks([0, 1])
        ax.set_xticklabels([f"Non\n(n={len(neg)})", f"Immuno.\n(n={len(pos)})"], fontsize=9)
        ax.set_title(title, fontsize=10.5, fontweight="bold")

    fig.suptitle("TESLA feature separation on the real benchmark "
                 "(714 labelled neoepitopes, Wells et al. 2020)",
                 fontsize=11.5, fontweight="bold", y=1.03)
    fig.tight_layout()
    return _save(fig, outdir, "fig3_feature_separation")


# =============================================================================
# Fig 4 — ranking performance (ROC + top-K enrichment)
# =============================================================================
def _roc_curve(y: np.ndarray, s: np.ndarray):
    order = np.argsort(-s)
    y = y[order]
    P = y.sum()
    N = len(y) - P
    tpr = np.concatenate([[0], np.cumsum(y) / max(P, 1)])
    fpr = np.concatenate([[0], np.cumsum(1 - y) / max(N, 1)])
    return fpr, tpr


def fig_ranking_performance(bench: dict, outdir: str) -> list[str]:
    scored = pd.DataFrame(bench["scored"])
    y = scored["label"].astype(int).values
    s = scored["priority_score"].astype(float).values
    rk = bench["summary"]["ranking"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.4))

    # (a) ROC — plot BOTH the full composite and the presentation sub-score. On this public
    #     table the recognition features (agretopicity, foreignness) cannot fire meaningfully
    #     (no WT %ranks; non-neoantigen context), so the presentation sub-score is the fair
    #     binding-dominated comparator; the composite is shown for full transparency.
    fpr, tpr = _roc_curve(y, s)
    ax1.plot(fpr, tpr, color="#0072B2", lw=2.2,
             label=f"Full composite (AUROC = {rk['auroc']:.2f})")
    # presentation sub-score, recomputed from component columns with the benchmark weights
    pw = {"binding_affinity_score": 0.55, "binding_stability": 0.30,
          "fraction_hydrophobic": 0.075}
    if all(c in scored.columns for c in pw):
        comp = scored[list(pw)].astype(float)
        wsum = comp.notna().mul(pd.Series(pw)).sum(axis=1)
        pres = (comp.fillna(0.0).mul(pd.Series(pw)).sum(axis=1) / wsum.replace(0, np.nan))
        mask = pres.notna().values
        if mask.sum() > 0 and "auroc_presentation" in rk:
            fpr_p, tpr_p = _roc_curve(y[mask], pres.values[mask])
            ax1.plot(fpr_p, tpr_p, color="#D55E00", lw=2.2, ls="-",
                     label=f"Presentation sub-score (AUROC = {rk['auroc_presentation']:.2f})")
    ax1.plot([0, 1], [0, 1], ls="--", color="#999999", lw=1, label="Random")
    ax1.set_xlabel("False positive rate", fontsize=10)
    ax1.set_ylabel("True positive rate", fontsize=10)
    ax1.set_title("A. ROC — immunogenicity ranking", fontsize=11, fontweight="bold", loc="left")
    ax1.legend(fontsize=8.5, frameon=False, loc="lower right")
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1.02)

    # (b) enrichment at top-K
    order = np.argsort(-s)
    y_sorted = y[order]
    base = y.mean()
    ks = np.arange(1, len(y) + 1)
    prec_at_k = np.cumsum(y_sorted) / ks
    enr = prec_at_k / base
    ax2.plot(ks, enr, color="#D55E00", lw=2.0)
    ax2.axhline(1.0, ls="--", color="#999999", lw=1)
    ax2.text(1.2, 1.35, "random (1x)", fontsize=8, color="#666666", va="bottom", ha="left")
    # annotate reported top-K enrichments
    for k, key in [(10, "enrichment_top10"), (20, "enrichment_top20")]:
        if key in rk and rk[key] is not None:
            ax2.scatter([k], [rk[key]], color="#0072B2", zorder=5, s=36)
            ax2.annotate(f"top{k}: {rk[key]:.1f}×", (k, rk[key]),
                         textcoords="offset points", xytext=(8, 6),
                         fontsize=8.5, color="#0072B2", fontweight="bold")
    ax2.set_xscale("log")
    ax2.set_xlabel("Top-K ranked peptides (log)", fontsize=10)
    ax2.set_ylabel("Enrichment over base rate", fontsize=10)
    ax2.set_title("B. Immunogenic enrichment vs. rank depth", fontsize=11,
                  fontweight="bold", loc="left")

    fig.suptitle(f"Ranking performance on real TESLA labels "
                 f"(n={rk['n_labelled']}, base rate {rk['base_rate']:.1%})",
                 fontsize=12, fontweight="bold", y=1.02)
    fig.tight_layout()
    return _save(fig, outdir, "fig4_ranking_performance")


# =============================================================================
# Driver
# =============================================================================
def generate_all(neoantigens_csv: Optional[str] = None,
                 benchmark_json: Optional[str] = None,
                 outdir: str = "figures") -> dict:
    neoantigens_csv = neoantigens_csv or DEFAULT_NEO
    benchmark_json = benchmark_json or DEFAULT_BENCH
    df = pd.read_csv(neoantigens_csv)
    with open(benchmark_json) as f:
        bench = json.load(f)

    out = {}
    out["fig1"] = fig_tier_distribution(df, outdir)
    out["fig2"] = fig_binding_by_tier(df, outdir)
    out["fig3"] = fig_feature_separation(bench, outdir)
    out["fig4"] = fig_ranking_performance(bench, outdir)
    for k, v in out.items():
        print(f"[plots] {k}: {v[0]}")
    return out


def _cli():
    ap = argparse.ArgumentParser(description="Generate neoantigen-TESLA figures.")
    ap.add_argument("--neoantigens", default=None, help="neoantigens.csv from export_results")
    ap.add_argument("--benchmark", default=None, help="benchmark_summary.json fixture")
    ap.add_argument("--outdir", default="figures", help="output directory")
    args = ap.parse_args()
    generate_all(args.neoantigens, args.benchmark, args.outdir)


if __name__ == "__main__":
    _cli()
