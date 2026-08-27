#!/usr/bin/env python3
"""
make_figures.py  —  the four data figures for the `tissue-expression-specificity` skill.

Generalized (gene-parameterized) from a validated GCGR run. Each figure is saved as
BOTH .png (dpi=200) and .svg with editable text, using the Phylo palette and
Liberation Sans. Every figure should pass a `media_output_check` before it goes in
the report (see SKILL.md).

Figures
  1. Ranked tissue bars         (GTEx + HPA, high-baseline highlighted)   -> always
  2. Cross-atlas concordance    (log2 GTEx vs log2 HPA, Spearman rho)     -> both atlases only
  3. Tau / specificity summary  (tau bars + safety-flag summary table)    -> always
  4. Safety organ heatmap       (organ x {GTEx, HPA}, log-scaled)         -> always

Call `make_all_figures(res, figdir)` with the dict from tissue_expression.run_analysis().
Figures degrade gracefully when only one atlas is available.
"""

from __future__ import annotations
import os
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Patch

matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"     # keep SVG text editable
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["axes.linewidth"] = 0.8

# Phylo palette
GOLD = "#D4A04A"; BLUE = "#0279EE"; GREEN = "#75A025"; ORANGE = "#FF9400"
PINK = "#FD9BED"; BLACK = "#000000"; WARM_GRAY = "#ECE9E2"; OFFWHITE = "#FAF9F3"
MUTED = "#8A8378"; RED = "#C0392B"
HEAT_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "phylo_heat", ["#FAF9F3", "#E9ED4C", "#FF9400", "#C0392B"])


def _save(fig, figdir, name):
    os.makedirs(figdir, exist_ok=True)
    png = f"{figdir}/{name}.png"; svg = f"{figdir}/{name}.svg"
    fig.savefig(png, dpi=200, bbox_inches="tight", facecolor="white")
    fig.savefig(svg, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  saved {name}.png / .svg")
    return png


# ---------------------------------------------------------------------------
# FIGURE 1 — ranked tissue bars (top-N per atlas), high-baseline highlighted
# ---------------------------------------------------------------------------
def fig_ranked_bars(res, figdir, top_n=20):
    gene = res["gene"]
    panels = []
    if res.get("gtex"): panels.append(("GTEx", res["gtex"]["df"], "tissue_gtex", "median_tpm", "median TPM", ABS := 25.0))
    if res.get("hpa"):  panels.append(("HPA consensus", res["hpa"]["df"], "tissue_hpa", "ntpm", "nTPM", 25.0))
    if not panels:
        return None
    fig, axes = plt.subplots(1, len(panels), figsize=(7.0 * len(panels), 8))
    if len(panels) == 1:
        axes = [axes]
    for ax, (title, df, ncol, vcol, unit, _) in zip(axes, panels):
        d = df.sort_values(vcol, ascending=False).head(top_n).iloc[::-1]
        colors = [RED if v >= 25 else (ORANGE if v >= 10 else BLUE) for v in d[vcol]]
        ax.barh(range(len(d)), d[vcol], color=colors, edgecolor="white", linewidth=0.6)
        ax.set_yticks(range(len(d)))
        ax.set_yticklabels([t.replace("_", " ") for t in d[ncol]], fontsize=8)
        ax.set_xlabel(f"Expression ({unit})", fontsize=10)
        ax.set_title(f"{title}", fontsize=12, fontweight="bold", loc="left")
        ax.spines[["top", "right"]].set_visible(False)
    legend = [Patch(facecolor=RED, label="High (>=25)"),
              Patch(facecolor=ORANGE, label="Moderate (>=10)"),
              Patch(facecolor=BLUE, label="Low (<10)")]
    fig.legend(handles=legend, loc="lower center", ncol=3, frameon=False,
               bbox_to_anchor=(0.5, -0.02), fontsize=9)
    fig.suptitle(f"{gene}: tissue expression ranking (top {top_n})",
                 fontsize=14, fontweight="bold", y=1.01)
    fig.tight_layout()
    return _save(fig, figdir, f"fig1_ranked_tissue_bars")


# ---------------------------------------------------------------------------
# FIGURE 2 — cross-atlas concordance scatter  (needs both atlases)
# ---------------------------------------------------------------------------
def fig_concordance(res, figdir):
    conc = res.get("concordance")
    if not conc or conc["merged"].empty:
        print("  [fig2] skipped — concordance requires both atlases")
        return None
    merged = conc["merged"]; stats = conc["stats"]; gene = res["gene"]
    fig, ax = plt.subplots(figsize=(8, 7))
    x, y = merged["log2_gtex"], merged["log2_hpa"]
    maxv = merged[["gtex_tpm", "hpa_ntpm"]].max(axis=1)
    pt_colors = [RED if v >= 25 else (ORANGE if v >= 10 else BLUE) for v in maxv]
    ax.scatter(x, y, c=pt_colors, s=70, edgecolor="white", linewidth=0.8, zorder=3, alpha=0.9)
    if len(merged) >= 2:
        m, b = np.polyfit(x, y, 1)
        xs = np.linspace(x.min(), x.max(), 50)
        ax.plot(xs, m * xs + b, color=MUTED, ls="--", lw=1.2, zorder=2)
    # label notable organs
    thr = np.percentile(maxv, 70) if len(maxv) else 0
    for _, row in merged.iterrows():
        if row["gtex_tpm"] >= max(5, thr) or row["hpa_ntpm"] >= max(5, thr):
            ax.annotate(row["organ"], (row["log2_gtex"], row["log2_hpa"]),
                        fontsize=8, xytext=(5, 4), textcoords="offset points", color=BLACK)
    ax.set_xlabel("GTEx  log2(median TPM + 1)", fontsize=11)
    ax.set_ylabel("HPA  log2(nTPM + 1)", fontsize=11)
    rho = stats.get("spearman_rho"); n = stats.get("n_organs")
    subtitle = f"Spearman rho = {rho}" if rho is not None else "Spearman rho = n/a"
    ax.set_title(f"{gene}: cross-atlas concordance ({n} shared organs)\n{subtitle}",
                 fontsize=12, fontweight="bold", loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    legend = [Patch(facecolor=RED, label="High (>=25)"),
              Patch(facecolor=ORANGE, label="Moderate (>=10)"),
              Patch(facecolor=BLUE, label="Low (<10)")]
    ax.legend(handles=legend, loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    return _save(fig, figdir, "fig2_concordance_scatter")


# ---------------------------------------------------------------------------
# FIGURE 3 — tau / specificity summary  (tau bars + flag summary table)
# ---------------------------------------------------------------------------
def fig_tau_summary(res, figdir):
    gene = res["gene"]
    atlases, taus, colors = [], [], []
    if res.get("gtex"): atlases.append("GTEx"); taus.append(res["gtex"]["tau_log2"]); colors.append(BLUE)
    if res.get("hpa"):  atlases.append("HPA consensus"); taus.append(res["hpa"]["tau_log2"]); colors.append(GOLD)
    if not atlases:
        return None

    fig = plt.figure(figsize=(12, 5.5))
    gs = fig.add_gridspec(1, 2, width_ratios=[1, 1.35], wspace=0.3)

    # Panel A: tau bars
    axA = fig.add_subplot(gs[0])
    bars = axA.bar(atlases, taus, color=colors, edgecolor="white", width=0.55, zorder=3)
    for bar, t in zip(bars, taus):
        axA.text(bar.get_x() + bar.get_width() / 2, t + 0.025, f"{t:.3f}",
                 ha="center", fontsize=12, fontweight="bold")
    axA.axhline(0.85, color=RED, ls="--", lw=1, alpha=0.7)
    axA.text(0.5, 0.62, "~0.85 = highly tissue-specific threshold",
             transform=axA.transData, fontsize=8, color=RED, va="center", ha="center")
    axA.set_ylim(0, 1.12); axA.set_xlim(-0.6, len(atlases) - 0.4)
    axA.set_ylabel("Tau (\u03c4) specificity score", fontsize=11)
    axA.set_title("A. Computed tissue-specificity (\u03c4)", fontsize=11, fontweight="bold", loc="left")
    axA.text(0.5, -0.16, "0 = ubiquitous   \u2192   1 = single-tissue specific",
             transform=axA.transAxes, ha="center", fontsize=8, color=MUTED)
    axA.spines[["top", "right"]].set_visible(False)

    # Panel B: high-baseline flag summary table
    axB = fig.add_subplot(gs[1]); axB.axis("off")
    axB.set_title("B. High-baseline tissues (flagged in either atlas)",
                  fontsize=11, fontweight="bold", loc="left")
    rows = []
    if res.get("gtex"):
        gf = res["gtex"]["flags"]
        for _, r in gf[gf["high_baseline"]].sort_values("median_tpm", ascending=False).head(8).iterrows():
            rows.append(["GTEx", r["tissue_gtex"].replace("_", " "), f'{r["median_tpm"]:.1f}', r["tier"]])
    if res.get("hpa"):
        hf = res["hpa"]["flags"]
        for _, r in hf[hf["high_baseline"]].sort_values("ntpm", ascending=False).head(8).iterrows():
            rows.append(["HPA", r["tissue_hpa"], f'{r["ntpm"]:.1f}', r["tier"]])
    if not rows:
        rows = [["\u2014", "no tissue met high-baseline criteria", "\u2014", "\u2014"]]
    tbl = axB.table(cellText=rows, colLabels=["Atlas", "Tissue", "Value", "Tier"],
                    cellLoc="left", loc="upper left", colWidths=[0.14, 0.5, 0.16, 0.24])
    tbl.auto_set_font_size(False); tbl.set_fontsize(8.5); tbl.scale(1, 1.35)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor(WARM_GRAY)
        if r == 0:
            cell.set_facecolor(GOLD); cell.set_text_props(color="white", fontweight="bold")
        elif r % 2 == 0:
            cell.set_facecolor("#F9F7F3")

    native = res["hpa"].get("spec_category") if res.get("hpa") else None
    sub = f"HPA native call: {native}" if native else ""
    fig.suptitle(f"{gene}: tissue-specificity summary   {('|  ' + sub) if sub else ''}",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    return _save(fig, figdir, "fig3_tau_summary")


# ---------------------------------------------------------------------------
# FIGURE 4 — safety organ heatmap  (organ x {GTEx, HPA}, log-scaled)
# ---------------------------------------------------------------------------
def fig_safety_heatmap(res, figdir):
    sf = res["safety"].copy()
    if sf.empty:
        return None
    gene = res["gene"]
    sf["maxexp"] = sf[["gtex_tpm", "hpa_ntpm"]].max(axis=1)
    sf = sf.sort_values("maxexp", ascending=False).reset_index(drop=True)
    mat = np.log2(sf[["gtex_tpm", "hpa_ntpm"]].values.astype(float) + 1)
    raw = sf[["gtex_tpm", "hpa_ntpm"]].values.astype(float)
    # Data-adaptive vmax so this target's top organ reaches the red end of the scale.
    # (Per-report scaling, not a fixed cross-target scale — the report is single-target.)
    vmax = float(np.nanmax(mat)) if mat.size and np.nanmax(mat) > 0 else 1.0

    # Distinct colors per system; the three vital systems get their own hues
    # (not all black) so the legend entries are distinguishable.
    VITAL_CARD = "#111111"; VITAL_CNS = "#6A3D9A"; VITAL_RESP = "#00A0A0"
    sys_colors = {"Cardiovascular (vital)": VITAL_CARD, "CNS (vital)": VITAL_CNS,
                  "Respiratory (vital)": VITAL_RESP, "Metabolic/hepatic": RED,
                  "Excretory/renal": ORANGE, "Metabolic/endocrine": GREEN,
                  "Metabolic": GREEN, "GI": BLUE, "Endocrine": GREEN,
                  "Musculoskeletal": MUTED, "Immune": MUTED, "Reproductive": PINK,
                  "Integumentary": MUTED, "Other": MUTED}

    fig, ax = plt.subplots(figsize=(6.8, max(4.5, 0.55 * len(sf) + 2.2)))
    im = ax.imshow(mat, aspect="auto", cmap=HEAT_CMAP, vmin=0, vmax=vmax)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["GTEx\n(median TPM)", "HPA\n(nTPM)"], fontsize=10)
    ax.set_yticks(range(len(sf)))
    ax.set_yticklabels(sf["organ"], fontsize=10)
    for tick, sysname in zip(ax.get_yticklabels(), sf["system"]):
        tick.set_color(sys_colors.get(sysname, MUTED))
    for i in range(raw.shape[0]):
        for j in range(raw.shape[1]):
            v = raw[i, j]
            if np.isnan(v):
                txt = "n/a"
            else:
                txt = f"{v:.1f}" if v >= 0.1 else f"{v:.2f}"
            color = "white" if (not np.isnan(mat[i, j]) and mat[i, j] > 0.6 * vmax) else BLACK
            ax.text(j, i, txt, ha="center", va="center", fontsize=8.5, color=color)
    ax.set_title(f"{gene}: on-target expression across safety-relevant organs",
                 fontsize=12, fontweight="bold", loc="left", pad=30)
    # Color encodes log2(x+1) for dynamic range; cell TEXT shows raw values.
    # Placed between the title (pad=30) and the axes top so it never clips the title.
    ax.text(0.0, 1.02, "cell color = log2(x+1) scale   |   numbers = raw values (TPM / nTPM)",
            transform=ax.transAxes, fontsize=7.5, color=MUTED, ha="left", va="bottom")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.10)
    cbar.set_label("color scale: log2(expression + 1)", fontsize=9)
    # system color legend (bottom)
    seen = []
    for s in sf["system"]:
        if s not in seen: seen.append(s)
    handles = [Patch(facecolor=sys_colors.get(s, BLACK), label=s) for s in seen]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.08),
              ncol=2, frameon=False, fontsize=7.5)
    fig.tight_layout()
    return _save(fig, figdir, "fig4_safety_heatmap")


def make_all_figures(res, figdir):
    """Generate every applicable figure; return {name: path} for those produced."""
    out = {}
    for fn, key in [(fig_ranked_bars, "fig1_ranked_tissue_bars"),
                    (fig_concordance, "fig2_concordance_scatter"),
                    (fig_tau_summary, "fig3_tau_summary"),
                    (fig_safety_heatmap, "fig4_safety_heatmap")]:
        try:
            p = fn(res, figdir)
            if p: out[key] = p
        except Exception as e:
            print(f"  [make_figures] {key} failed: {e}")
    return out
