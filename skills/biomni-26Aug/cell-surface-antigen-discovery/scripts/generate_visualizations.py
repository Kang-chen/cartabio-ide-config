#!/usr/bin/env python3
"""Visualizations for cell-surface target discovery (PNG + SVG, 300 DPI).

Per skill convention: seaborn `ticks` style + Helvetica, with graceful SVG fallback.
Each plot is wrapped in try/except so one failure does not block the others.
"""

import os

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

sns.set_style("ticks")
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Helvetica", "Arial", "DejaVu Sans"]

_TIER_COLORS = {"Tier 1": "#1b7837", "Tier 2": "#e08214", "Tier 3": "#999999"}


def _save_fig(fig, base_path, dpi=300):
    png = base_path if base_path.endswith(".png") else base_path + ".png"
    fig.savefig(png, dpi=dpi, bbox_inches="tight")
    print(f"   Saved: {png}")
    svg = png[:-4] + ".svg"
    try:
        fig.savefig(svg, bbox_inches="tight")
        print(f"   Saved: {svg}")
    except Exception:  # noqa: BLE001
        print("   (SVG export failed — PNG available)")
    plt.close(fig)


def _ranked(scores):
    if isinstance(scores, dict):
        return scores["ranked"], scores.get("harness"), scores.get("metrics", {})
    return scores, None, {}


def plot_compartment_heatmap(ranked, output_dir, top_n=25):
    top = ranked.head(top_n).set_index("gene_symbol")
    mat = top[["epithelial_mean", "caf_mean", "immune_mean"]].copy()
    mat.columns = ["Epithelial/\nmalignant", "CAF", "Immune"]
    mat = np.log2(mat.fillna(0) + 1e-3)
    figsize = (5.5, max(4, 0.32 * len(mat)))
    try:
        # Preferred: seaborn clustermap (order preserved; needs scipy).
        g = sns.clustermap(mat, row_cluster=False, col_cluster=False, cmap="YlGnBu_r",
                           figsize=figsize, cbar_kws={"label": "log2(mean expr + 1e-3)"},
                           linewidths=0.3)
        g.ax_heatmap.set_xlabel("")
        g.ax_heatmap.set_ylabel("")
        g.fig.suptitle("Compartment expression of top surface candidates", y=1.02,
                       fontweight="bold")
        _save_fig(g.fig, os.path.join(output_dir, "compartment_specificity_heatmap.png"))
    except Exception:  # noqa: BLE001 (e.g. scipy missing) -> seaborn heatmap fallback
        fig, ax = plt.subplots(figsize=figsize)
        sns.heatmap(mat, cmap="YlGnBu_r", linewidths=0.3, ax=ax,
                    cbar_kws={"label": "log2(mean expr + 1e-3)"})
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_title("Compartment expression of top surface candidates", fontweight="bold")
        _save_fig(fig, os.path.join(output_dir, "compartment_specificity_heatmap.png"))


def plot_therapeutic_index_map(ranked, output_dir):
    fig, ax = plt.subplots(figsize=(7.5, 6))
    d = ranked.copy()
    d["x"] = np.log10(d["spec_vs_tme"].clip(lower=1e-3))
    d["y"] = d["safety_score"]
    plotted = d.dropna(subset=["x", "y"])
    for tier, sub in plotted.groupby("tier"):
        ax.scatter(sub["x"], sub["y"], s=40, alpha=0.75, label=tier,
                   color=_TIER_COLORS.get(tier, "#999999"),
                   edgecolor="black", linewidth=0.4)
    # Label known targets + the top candidates.
    to_label = plotted[(plotted["is_known_target"]) | (plotted["rank"] <= 12)]
    for _, r in to_label.iterrows():
        ax.annotate(r["gene_symbol"], (r["x"], r["y"]), fontsize=7,
                    xytext=(3, 3), textcoords="offset points",
                    fontweight="bold" if r["is_known_target"] else "normal")
    ax.axvline(np.log10(5), ls="--", c="grey", lw=0.8)
    ax.axhline(0.6, ls="--", c="grey", lw=0.8)
    ax.set_xlabel("Tumor specificity  log10(epithelial / TME enrichment)")
    ax.set_ylabel("Normal-tissue safety score (1 = safe)")
    ax.set_title("Therapeutic-index map\n(top-right = specific + safe; bold = known targets)",
                 fontweight="bold")
    ax.legend(title="Tier", frameon=False, loc="lower left")
    sns.despine(ax=ax)
    _save_fig(fig, os.path.join(output_dir, "therapeutic_index_map.png"))


def plot_validation_harness(harness, metrics, output_dir):
    if harness is None or harness.empty:
        return
    d = harness[harness["scored"] == True].copy()  # noqa: E712
    if d.empty:
        return
    d = d.sort_values("rank")
    colors = ["#762a83" if str(c) == "not_a_target" else "#1b7837"
              for c in d["clinical_status"]]
    fig, ax = plt.subplots(figsize=(7, max(3.5, 0.3 * len(d))))
    ax.barh(d["gene_symbol"], d["rank"], color=colors, edgecolor="black", linewidth=0.4)
    ax.invert_yaxis()
    ax.set_xlabel("Rank among all scored candidates (lower = better)")
    rec = metrics.get("recall_at_20_str", "n/a") if metrics else "n/a"
    ax.set_title(f"Validation harness — where known targets rank\nrecall@20 = {rec} "
                 "(green = validated target, purple = cautionary control)", fontweight="bold")
    sns.despine(ax=ax)
    _save_fig(fig, os.path.join(output_dir, "validation_harness_ranks.png"))


def plot_top_targets(ranked, output_dir, top_n=20):
    top = ranked.head(top_n).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7, max(3.5, 0.32 * len(top))))
    ax.barh(top["gene_symbol"], top["final_score"],
            color=[_TIER_COLORS.get(t, "#999999") for t in top["tier"]],
            edgecolor="black", linewidth=0.4)
    for known, g, s in zip(top["is_known_target"], top["gene_symbol"], top["final_score"]):
        if known:
            ax.text(s + 0.01, g, "*", va="center", fontsize=11, fontweight="bold")
    ax.set_xlabel("Composite surface-target score")
    ax.set_title("Top ranked surface-target candidates (* = known validated target)",
                 fontweight="bold")
    ax.set_xlim(0, max(1.0, top["final_score"].max() * 1.1))
    sns.despine(ax=ax)
    _save_fig(fig, os.path.join(output_dir, "top_targets_ranking.png"))


def generate_all_plots(spec_df, surf_df, ann_df, ti_df, scores, known_targets_df,
                       output_dir="results"):
    os.makedirs(output_dir, exist_ok=True)
    ranked, harness, metrics = _ranked(scores)
    n_ok = 0
    for name, fn in [
        ("compartment heatmap", lambda: plot_compartment_heatmap(ranked, output_dir)),
        ("therapeutic-index map", lambda: plot_therapeutic_index_map(ranked, output_dir)),
        ("validation harness", lambda: plot_validation_harness(harness, metrics, output_dir)),
        ("top targets", lambda: plot_top_targets(ranked, output_dir)),
    ]:
        try:
            fn()
            n_ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"   WARNING: '{name}' plot failed: {exc}")
    print(f"✓ All plots generated successfully! {n_ok} visualization(s) saved")


if __name__ == "__main__":
    print("generate_visualizations: call generate_all_plots(...). See assets/eval/static_test.py.")
