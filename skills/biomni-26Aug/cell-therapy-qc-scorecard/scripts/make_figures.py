"""
============================================================================
MAKE FIGURES  —  cell-therapy scRNA-seq QC release scorecard
============================================================================

Generate the four report figures (PNG + SVG). The AGENT must media-check each
saved PNG with Read(mode="media_output_check") and regenerate if a panel is
blank/clipped (a recurring failure — see caveat #5 dtype-safe masks).

Figures
  fig1_qc_distributions      per-unit violins: counts, genes, mito, doublet score
  fig2_scorecard_heatmap     unit x module heatmap, GREEN/AMBER/RED, call text in cells
  fig3_module_umap_overlays  per-unit UMAP colored by target/pluri/offtarget/mature
  fig4_crosslot_comparison   grouped bars of headline metrics with threshold lines

Functions
  - setup_style()
  - fig1_qc_distributions(units, cfg)
  - fig2_scorecard_heatmap(calls_df, cfg)
  - fig3_module_umap_overlays(units, cfg)        (skips units without UMAP)
  - fig4_crosslot_comparison(metrics_df, cfg)
  - make_all_figures(units, metrics_df, calls_df, cfg) -> list[png paths]

Usage
  from make_figures import make_all_figures
  pngs = make_all_figures(units, metrics_df, calls_df, cfg)
"""

import os
from typing import Dict, List

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# Phylo palette
GREEN = "#75A025"; ORANGE = "#FF9400"; RED = "#D62728"; BLUE = "#0279EE"; GOLD = "#D4A04A"
CALL_COLOR = {"GREEN": GREEN, "AMBER": ORANGE, "RED": RED, "NA": "#BBBBBB"}


def B(ad, col):
    """dtype-safe boolean mask (int 0/1 obs -> bool)."""
    return ad.obs[col].values.astype(bool)


def setup_style():
    matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
    matplotlib.rcParams["svg.fonttype"] = "none"
    matplotlib.rcParams["figure.dpi"] = 120


def _save(fig, cfg, stem):
    setup_style()
    d = cfg["dirs"]["figures"]
    png = os.path.join(d, f"{stem}.png")
    svg = os.path.join(d, f"{stem}.svg")
    fig.savefig(png, bbox_inches="tight", dpi=300)
    try:
        fig.savefig(svg, bbox_inches="tight")
    except Exception:
        pass
    plt.close(fig)
    print(f"  ✓ {stem} -> {png}")
    return png


def fig1_qc_distributions(units: Dict, cfg: Dict) -> str:
    names = list(units)
    metrics = [("total_counts", "UMI counts", True),
               ("n_genes_by_counts", "Genes", True),
               ("pct_counts_mt", "Mito %", False),
               ("doublet_score", "Doublet score", False)]
    # scale width with number of units so x-labels don't crowd
    per_panel_w = max(4.2, 1.0 + 0.55 * len(names))
    fig, axes = plt.subplots(1, len(metrics), figsize=(per_panel_w * len(metrics), 4.6))
    for ax, (col, label, logy) in zip(np.atleast_1d(axes), metrics):
        data, labels = [], []
        for nm in names:
            obs = units[nm].obs
            if col in obs:
                data.append(obs[col].values); labels.append(nm)
        if data:
            parts = ax.violinplot(data, showmedians=True)
            for pc in parts["bodies"]:
                pc.set_facecolor(BLUE); pc.set_alpha(0.6)
            ax.set_xticks(range(1, len(labels) + 1))
            ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=10)
            ax.tick_params(axis="y", labelsize=9)
            if logy:
                ax.set_yscale("log")
        ax.set_title(label, fontsize=13, fontweight="bold")
    fig.suptitle("Per-unit QC distributions", fontsize=15, y=1.03)
    fig.tight_layout()
    return _save(fig, cfg, "fig1_qc_distributions")


def fig2_scorecard_heatmap(calls_df: pd.DataFrame, cfg: Dict) -> str:
    mod_cols = [c for c in calls_df.columns if c not in ("unit",)]
    # order: modules then OVERALL last
    ordered = [c for c in mod_cols if c != "OVERALL"] + (["OVERALL"] if "OVERALL" in mod_cols else [])
    units = calls_df["unit"].tolist()
    M = np.array([[CALL_ORDER_IDX.get(calls_df.iloc[i][c], -1) for c in ordered]
                  for i in range(len(units))])
    fig, ax = plt.subplots(figsize=(1.6 * len(ordered) + 2, 0.7 * len(units) + 2))
    # draw colored cells with text
    for i in range(len(units)):
        for j, c in enumerate(ordered):
            call = calls_df.iloc[i][c]
            ax.add_patch(plt.Rectangle((j, len(units) - 1 - i), 1, 1,
                         facecolor=CALL_COLOR.get(call, "#BBBBBB"),
                         edgecolor="white", lw=2))
            ax.text(j + 0.5, len(units) - 1 - i + 0.5, call, ha="center", va="center",
                    fontsize=8, color="white", fontweight="bold")
    ax.set_xlim(0, len(ordered)); ax.set_ylim(0, len(units))
    ax.set_xticks([j + 0.5 for j in range(len(ordered))])
    ax.set_xticklabels([c.replace("_", "\n") for c in ordered], fontsize=8)
    ax.set_yticks([len(units) - 1 - i + 0.5 for i in range(len(units))])
    ax.set_yticklabels(units, fontsize=9)
    ax.set_xticks(np.arange(len(ordered)), minor=True)
    ax.tick_params(length=0)
    ax.set_title("Release scorecard (overall = worst active module)", fontsize=12, pad=12)
    legend = [Patch(facecolor=CALL_COLOR[k], label=k) for k in ("GREEN", "AMBER", "RED")]
    ax.legend(handles=legend, bbox_to_anchor=(1.02, 1), loc="upper left", frameon=False)
    fig.text(0.5, -0.04, "Thresholds are defaults (see 06_thresholds_reference.csv), "
             "not universal release standards.", ha="center", fontsize=7.5, color="#666666")
    fig.tight_layout()
    return _save(fig, cfg, "fig2_scorecard_heatmap")


def fig3_module_umap_overlays(units: Dict, cfg: Dict) -> str:
    overlays = [("is_target", "Target identity"),
                ("is_residual_pluripotent", "Residual pluripotent"),
                ("is_offtarget_any", "Off-target"),
                ("is_mature", "Mature")]
    have_umap = [nm for nm in units if "X_umap" in units[nm].obsm]
    if not have_umap:
        print("  ⚠ no UMAP in any unit — skipping fig3 (run clustering/UMAP to enable)")
        return ""
    ncol = len(overlays); nrow = len(have_umap)
    fig, axes = plt.subplots(nrow, ncol, figsize=(3.2 * ncol, 3.0 * nrow), squeeze=False)
    for r, nm in enumerate(have_umap):
        ad = units[nm]
        xy = ad.obsm["X_umap"]
        for c, (col, label) in enumerate(overlays):
            ax = axes[r][c]
            ax.scatter(xy[:, 0], xy[:, 1], s=2, c="#DDDDDD", linewidths=0)
            if col in ad.obs:
                mask = B(ad, col)  # dtype-safe (caveat #5)
                ax.scatter(xy[mask, 0], xy[mask, 1], s=3, c=BLUE, linewidths=0)
                ax.set_title(f"{nm}\n{label} ({mask.sum()})", fontsize=8)
            else:
                ax.set_title(f"{nm}\n{label} (n/a)", fontsize=8)
            ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle("Per-unit module overlays (UMAP)", fontsize=13, y=1.005)
    fig.tight_layout()
    return _save(fig, cfg, "fig3_module_umap_overlays")


def fig4_crosslot_comparison(metrics_df: pd.DataFrame, cfg: Dict) -> str:
    thr = cfg["thresholds"]
    panels = [("pct_target_purity", "Target purity %", thr["purity_pct"]),
              ("pct_residual_pluripotent", "Residual pluri %", thr["resid_pluri_pct"]),
              ("pct_offtarget", "Off-target %", thr["offtarget_pct"]),
              ("pct_mature_of_target", "Maturity %", thr["maturity_pct"]),
              ("pct_true_contaminant", "Contamination %", thr["contam_pct"]),
              ("retention_pct", "Retention %", thr["retention_pct"])]
    panels = [p for p in panels if p[0] in metrics_df.columns]
    ncol = min(3, len(panels)) or 1
    nrow = int(np.ceil(len(panels) / ncol))
    units = metrics_df["unit"].tolist()
    per_w = max(5.2, 1.6 + 0.7 * len(units))
    fig, axes = plt.subplots(nrow, ncol, figsize=(per_w * ncol, 3.9 * nrow), squeeze=False)
    for i, (col, label, t) in enumerate(panels):
        ax = axes[i // ncol][i % ncol]
        vals = np.clip(metrics_df[col].fillna(0).values.astype(float), 0, None)
        x = np.arange(len(units))
        bars = ax.bar(x, vals, color=BLUE, alpha=0.85, width=0.6)
        # threshold lines
        ax.axhline(t["green"], color=GREEN, ls="--", lw=1.2)
        ax.axhline(t["red"], color=RED, ls="--", lw=1.2)
        # headroom so bar labels AND threshold labels are always in-range
        ymax = max(vals.max(), t["green"], t["red"]) * 1.30 + 1e-6
        ax.set_ylim(0, ymax)
        # threshold labels pinned to the LEFT inside the axes (never collide w/ bars)
        ax.text(-0.45, t["green"], f"G {t['green']:g}", color=GREEN, fontsize=8.5,
                va="center", ha="left", fontweight="bold",
                bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.5))
        ax.text(-0.45, t["red"], f"R {t['red']:g}", color=RED, fontsize=8.5,
                va="center", ha="left", fontweight="bold",
                bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.5))
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + ymax * 0.01, f"{v:.1f}",
                    ha="center", va="bottom", fontsize=9)
        ax.set_xlim(-0.7, len(units) - 0.3)
        ax.set_xticks(x)
        ax.set_xticklabels(units, rotation=35, ha="right", fontsize=9.5)
        ax.tick_params(axis="y", labelsize=9)
        ax.set_title(label, fontsize=12, fontweight="bold")
    # hide unused axes
    for k in range(len(panels), nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    fig.suptitle("Cross-lot comparison (dashed = GREEN / RED thresholds)",
                 fontsize=15, y=1.02)
    fig.tight_layout()
    return _save(fig, cfg, "fig4_crosslot_comparison")


# index used by the heatmap
CALL_ORDER_IDX = {"GREEN": 0, "AMBER": 1, "RED": 2, "NA": -1}


def make_all_figures(units: Dict, metrics_df: pd.DataFrame,
                     calls_df: pd.DataFrame, cfg: Dict) -> List[str]:
    setup_style()
    pngs = []
    pngs.append(fig1_qc_distributions(units, cfg))
    pngs.append(fig2_scorecard_heatmap(calls_df, cfg))
    f3 = fig3_module_umap_overlays(units, cfg)
    if f3:
        pngs.append(f3)
    pngs.append(fig4_crosslot_comparison(metrics_df, cfg))
    print(f"✓ figures generated: {[os.path.basename(p) for p in pngs if p]}")
    print("  → MEDIA-CHECK each PNG with Read(mode='media_output_check') and "
          "regenerate if any panel is blank/clipped.")
    return [p for p in pngs if p]


if __name__ == "__main__":
    print("make_figures.py — import and call make_all_figures(units, metrics_df, calls_df, cfg).")
