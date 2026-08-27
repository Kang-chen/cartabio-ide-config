"""
make_figures.py — Adaptive figure set for the cancer-cohort-genomics skill.

Reproduces the four proven figures, but each figure is CONDITIONAL so the set
adapts to the inputs:
  F1 ranked_bars         : always (one panel per cohort present)
  F2 cross_cohort_scatter: only when >=2 cohorts share matched cancer types
  F3 mutation_vs_cna     : always (stacked mutation vs amplification per type)
  F4 hotspot_landscape   : only when the gene has recurrent hotspots
                           (analyze_alterations.has_recurrent_hotspots)

All figures use the Phylo palette, Liberation Sans, and export BOTH .svg and .png.
Each figure MUST be media-checked by the agent after saving (see SKILL.md).

The functions take tidy DataFrames (from analyze_alterations) so they are
gene-agnostic. `gene` is only used for titles/labels.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Phylo / colorblind-friendly palette (consistent with the KRAS report)
COL_MUT = "#0279EE"    # mutation (blue)
COL_AMP = "#FF9400"    # amplification (orange)
COL_TCGA = "#75A025"   # TCGA (green)
COL_MSK = "#FD9BED"    # MSK (pink)
# Okabe-Ito for hotspot codons
CODON_COLORS = {
    "G12": "#0072B2", "G13": "#009E73", "Q61": "#E69F00",
    "A146": "#CC79A7", "K117": "#56B4E9", "Other": "#999999",
}

matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"


def _save(fig, out_stem: str):
    """Save a figure as both PNG (media-check) and SVG (editable)."""
    fig.savefig(f"{out_stem}.png", dpi=150, bbox_inches="tight")
    fig.savefig(f"{out_stem}.svg", bbox_inches="tight")
    plt.close(fig)
    return [f"{out_stem}.png", f"{out_stem}.svg"]


def fig_ranked_bars(freq_df, gene: str, out_stem: str, top_n: int = 25,
                    value_col: str = "any_freq_pct"):
    """F1: horizontal ranked bars of alteration frequency by cancer type, one
    subplot per cohort. Stable types only (N>=20) are shown; low-N flagged out."""
    cohorts = [c for c in freq_df["cohort"].unique()]
    fig, axes = plt.subplots(1, len(cohorts), figsize=(7 * len(cohorts), 8),
                             squeeze=False)
    for ax, cohort in zip(axes[0], cohorts):
        sub = (freq_df[(freq_df["cohort"] == cohort) & (freq_df["stable"] == "yes")]
               .sort_values(value_col, ascending=True).tail(top_n))
        color = COL_TCGA if "TCGA" in cohort.upper() else COL_MSK
        ax.barh(sub["cancer_type"], sub[value_col], color=color)
        ax.set_xlabel(f"{gene} altered (% of profiled samples)")
        ax.set_title(f"{cohort}")
        ax.grid(axis="x", alpha=0.3)
    fig.suptitle(f"{gene} alteration frequency by cancer type", fontweight="bold")
    fig.tight_layout()
    return _save(fig, out_stem)


def fig_cross_cohort_scatter(matched_df, gene: str, out_stem: str):
    """F2: scatter of matched cancer-type frequencies across two cohorts.

    matched_df columns: cancer_type, x_freq, y_freq, x_label, y_label.
    Only call when >=2 cohorts share matched types (else skip).
    """
    import numpy as np
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(matched_df["x_freq"], matched_df["y_freq"], s=60,
               color=COL_MUT, edgecolor="black", linewidth=0.5, zorder=3)
    lim = max(matched_df["x_freq"].max(), matched_df["y_freq"].max()) * 1.1
    ax.plot([0, lim], [0, lim], "--", color="grey", zorder=1, label="y = x")
    for _, r in matched_df.iterrows():
        ax.annotate(r["cancer_type"], (r["x_freq"], r["y_freq"]),
                    fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel(f"{matched_df['x_label'].iloc[0]} — {gene} altered %")
    ax.set_ylabel(f"{matched_df['y_label'].iloc[0]} — {gene} altered %")
    ax.set_title(f"Cross-cohort concordance of {gene} alteration frequency")
    if len(matched_df) >= 3:
        r = np.corrcoef(matched_df["x_freq"], matched_df["y_freq"])[0, 1]
        ax.text(0.05, 0.95, f"Pearson r = {r:.3f}", transform=ax.transAxes,
                va="top", fontsize=10)
    ax.legend(loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return _save(fig, out_stem)


def fig_mutation_vs_cna(freq_df, gene: str, out_stem: str, cohort: str,
                        top_n: int = 20):
    """F3: stacked bars separating mutation vs amplification contribution per
    cancer type (single cohort). Uses mutation-only + amp-only components derived
    from the common-denominator counts so bars are comparable."""
    sub = (freq_df[(freq_df["cohort"] == cohort) & (freq_df["stable"] == "yes")]
           .sort_values("any_freq_pct", ascending=False).head(top_n))
    fig, ax = plt.subplots(figsize=(max(8, 0.5 * len(sub)), 6))
    xpos = range(len(sub))
    ax.bar(xpos, sub["mut_freq_pct"], color=COL_MUT, label="Mutation")
    ax.bar(xpos, sub["amp_freq_pct"], bottom=sub["mut_freq_pct"],
           color=COL_AMP, label="Amplification")
    ax.set_ylabel(f"{gene} altered (%)")
    ax.set_title(f"{gene}: mutation vs amplification by cancer type ({cohort})")
    ax.set_xticks(list(xpos))
    ax.set_xticklabels(sub["cancer_type"], rotation=60, ha="right", fontsize=8)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, out_stem)


def fig_hotspot_landscape(hotspot_df, gene: str, out_stem: str, top_alleles: int = 12):
    """F4: two-panel hotspot figure. Panel A = codon-bin composition; Panel B =
    top specific alleles. Only call when has_recurrent_hotspots() is True.

    hotspot_df columns: bin, allele, count.
    """
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(13, 5))

    # Panel A: codon bin totals
    bin_tot = (hotspot_df.groupby("bin")["count"].sum()
               .sort_values(ascending=False))
    colors = [CODON_COLORS.get(b, "#999999") for b in bin_tot.index]
    xposA = range(len(bin_tot))
    axA.bar(xposA, bin_tot.values, color=colors)
    axA.set_ylabel(f"{gene}-mutant records")
    axA.set_title(f"{gene} hotspot codon distribution")
    axA.set_xticks(list(xposA))
    axA.set_xticklabels(bin_tot.index, rotation=30, ha="right")

    # Panel B: top specific alleles
    alleles = hotspot_df.sort_values("count", ascending=False).head(top_alleles)
    axB.barh(alleles["allele"][::-1], alleles["count"][::-1], color=COL_MUT)
    axB.set_xlabel("records")
    axB.set_title(f"Top recurrent {gene} alleles")

    fig.tight_layout()
    return _save(fig, out_stem)
