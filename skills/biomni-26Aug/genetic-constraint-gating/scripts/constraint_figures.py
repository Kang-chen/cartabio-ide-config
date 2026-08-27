"""
constraint_figures.py — constraint + druggability figures (colorblind-safe, PNG + editable SVG).

  fig1: genes ranked by v2.1.1 LOEUF (lollipop) with the intolerance cutoff line.
  fig2: pLI vs LOEUF scatter with threshold quadrants (labels de-overlapped).
  fig3: v2.1.1 -> v4.1 LOEUF shift (dumbbell).
  fig4: drug-target reading — LOEUF vs knockout-tolerance tier / systemic on-target risk.

All operate on the DataFrame from constraint_analysis.analyze_genes(), using only
rows with a resolved LOEUF_v2. x-axes are tightened to the data range so tightly
clustered constrained gene sets remain readable, and legend entries with no
corresponding points are dropped. Always run a media-output-check on each PNG afterward.
"""

import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42

C_FLAG = "#0072B2"     # blue  = LoF-intolerant (Okabe-Ito)
C_NOFLAG = "#E69F00"   # orange = not flagged
C_THRESH = "#D55E00"   # vermillion threshold line
C_V2 = "#999999"
C_V4 = "#0072B2"
LOEUF_CUT = 0.35
PLI_CUT = 0.90

# KO-tolerance tier -> colour (matches constraint_druggability.tier_color)
TIER_HEX = {
    "Very low (near-essential)": "#7A0177",
    "Low (LoF-intolerant)":      "#D55E00",
    "Intermediate":              "#E69F00",
    "Tolerant":                  "#009E73",
    "Not determined":            "#999999",
}
TIER_RANK = {"Very low (near-essential)": 0, "Low (LoF-intolerant)": 1,
             "Intermediate": 2, "Tolerant": 3, "Not determined": 4}


def _plottable(df):
    d = df[df["LOEUF_v2"].notna()].copy()
    return d.sort_values("LOEUF_v2").reset_index(drop=True)


def _xmax(vals, pad=0.12, floor=0.55):
    m = float(np.nanmax(vals))
    return max(floor, m * (1 + pad) + 0.05)


def fig_ranked_loeuf(df, out_prefix):
    d = _plottable(df)
    if d.empty:
        return None
    any_noflag = (d["LoF_intolerant"] == "No").any()
    ypos = np.arange(len(d))[::-1]
    colors = [C_FLAG if f == "Yes" else C_NOFLAG for f in d["LoF_intolerant"]]
    fig, ax = plt.subplots(figsize=(8.6, max(3.0, 0.72 * len(d) + 1.7)))
    for y, low, up, col in zip(ypos, d["LOEUF_lower_v2"], d["LOEUF_v2"], colors):
        if low is not None and not (isinstance(low, float) and np.isnan(low)):
            ax.plot([low, up], [y, y], color=col, lw=1.6, alpha=0.5, zorder=1)
    ax.scatter(d["LOEUF_v2"], ypos, s=110, color=colors, zorder=3, edgecolor="white", linewidth=0.9)
    ax.scatter(d["oe_lof_v2"], ypos, s=34, facecolors="none", edgecolors=colors, zorder=2, alpha=0.75)
    for y, up in zip(ypos, d["LOEUF_v2"]):
        ax.annotate(f"{up:.3f}", (up, y), textcoords="offset points", xytext=(9, 0),
                    va="center", fontsize=8.6, color="#333333")
    ax.axvline(LOEUF_CUT, color=C_THRESH, ls="--", lw=1.7, zorder=0)
    ax.text(LOEUF_CUT + 0.008, ypos.min() - 0.35, f"LOEUF = {LOEUF_CUT}\n(intolerance cutoff)",
            color=C_THRESH, fontsize=8.6, va="bottom")
    ax.set_yticks(ypos); ax.set_yticklabels(d["gene"], fontsize=11)
    ax.set_ylim(ypos.min() - 0.7, ypos.max() + 0.7)
    ax.set_xlabel("LOEUF (LoF o/e upper 90% CI) — lower = more constrained", fontsize=10.5)
    ax.set_title("gnomAD v2.1.1 LoF constraint, genes ranked by LOEUF", fontsize=12, weight="bold")
    ax.set_xlim(-0.02, _xmax(d["LOEUF_v2"], floor=0.55))
    ax.grid(axis="x", ls=":", alpha=0.4)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    leg = [Line2D([0], [0], marker="o", color="w", markerfacecolor=C_FLAG, markersize=10, label="LoF-intolerant")]
    if any_noflag:
        leg.append(Line2D([0], [0], marker="o", color="w", markerfacecolor=C_NOFLAG, markersize=10, label="Not flagged"))
    leg.append(Line2D([0], [0], marker="o", color="w", markerfacecolor="none", markeredgecolor="grey",
                      markersize=7, label="point o/e"))
    ax.legend(handles=leg, loc="lower right", fontsize=8.8, frameon=True)
    plt.tight_layout()
    plt.savefig(f"{out_prefix}.png", dpi=200, bbox_inches="tight")
    plt.savefig(f"{out_prefix}.svg", bbox_inches="tight")
    plt.close()
    return f"{out_prefix}.png"


def fig_pli_vs_loeuf(df, out_prefix):
    d = _plottable(df)
    d = d[d["pLI_v2"].notna()]
    if d.empty:
        return None
    any_noflag = (d["LoF_intolerant"] == "No").any()
    fig, ax = plt.subplots(figsize=(8.2, 6.2))
    colors = [C_FLAG if f == "Yes" else C_NOFLAG for f in d["LoF_intolerant"]]
    ax.scatter(d["LOEUF_v2"], d["pLI_v2"], s=150, c=colors, edgecolor="white", linewidth=1.1, zorder=3)
    ax.axvline(LOEUF_CUT, color=C_THRESH, ls="--", lw=1.6, zorder=1)
    ax.axhline(PLI_CUT, color=C_THRESH, ls="--", lw=1.6, zorder=1)
    ax.axvspan(-0.1, LOEUF_CUT, color=C_FLAG, alpha=0.05, zorder=0)
    ax.axhspan(PLI_CUT, 1.06, color=C_FLAG, alpha=0.05, zorder=0)
    xmax = _xmax(d["LOEUF_v2"], floor=0.5)
    order = d.sort_values("LOEUF_v2").reset_index(drop=True)
    for i, r in order.iterrows():
        if r["pLI_v2"] >= 0.999:
            ytxt = 0.965 - 0.052 * i
            ax.annotate(r["gene"], (r["LOEUF_v2"], r["pLI_v2"]),
                        xytext=(r["LOEUF_v2"] + xmax * 0.16, ytxt), fontsize=9.2, va="center",
                        arrowprops=dict(arrowstyle="-", color="#9a9a9a", lw=0.8, shrinkA=0, shrinkB=4))
        else:
            ax.annotate(r["gene"], (r["LOEUF_v2"], r["pLI_v2"]), textcoords="offset points",
                        xytext=(8, -12), fontsize=9.2)
    ax.text(LOEUF_CUT - 0.006, 0.02, "LOEUF < 0.35", color=C_THRESH, fontsize=8.8, ha="right")
    ax.text(xmax * 0.99, PLI_CUT + 0.008, "pLI \u2265 0.90", color=C_THRESH, fontsize=8.8, ha="right", va="bottom")
    ax.set_xlabel("LOEUF (gnomAD v2.1.1) — lower = more constrained", fontsize=10.5)
    ax.set_ylabel("pLI (gnomAD v2.1.1) — higher = more constrained", fontsize=10.5)
    ax.set_title("pLI vs LOEUF: agreement of the two constraint metrics", fontsize=12, weight="bold")
    ax.set_xlim(-0.02, xmax)
    ax.set_ylim(-0.03, 1.07)
    ax.grid(ls=":", alpha=0.4)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    leg = [Line2D([0], [0], marker="o", color="w", markerfacecolor=C_FLAG, markersize=11, label="LoF-intolerant")]
    if any_noflag:
        leg.append(Line2D([0], [0], marker="o", color="w", markerfacecolor=C_NOFLAG, markersize=11, label="Not flagged"))
    leg.append(Line2D([0], [0], color=C_THRESH, ls="--", label="Standard thresholds"))
    ax.legend(handles=leg, loc="lower right", fontsize=8.8, frameon=True)
    plt.tight_layout()
    plt.savefig(f"{out_prefix}.png", dpi=200, bbox_inches="tight")
    plt.savefig(f"{out_prefix}.svg", bbox_inches="tight")
    plt.close()
    return f"{out_prefix}.png"


def fig_version_shift(df, out_prefix):
    d = _plottable(df)
    d = d[d["LOEUF_v4"].notna()]
    if d.empty:
        return None
    ypos = np.arange(len(d))[::-1]
    fig, ax = plt.subplots(figsize=(8.6, max(3.0, 0.72 * len(d) + 1.7)))
    for y, (_, r) in zip(ypos, d.iterrows()):
        ax.plot([r["LOEUF_v2"], r["LOEUF_v4"]], [y, y], color="grey", lw=1.4, alpha=0.55, zorder=1)
        ax.scatter(r["LOEUF_v2"], y, s=85, color=C_V2, zorder=3, label="v2.1.1" if y == ypos[0] else "")
        ax.scatter(r["LOEUF_v4"], y, s=85, color=C_V4, zorder=3, label="v4.1" if y == ypos[0] else "")
        ax.annotate(f"{r['LOEUF_v2']:.3f}", (r["LOEUF_v2"], y), textcoords="offset points",
                    xytext=(0, 9), ha="center", fontsize=8.0, color=C_V2)
        ax.annotate(f"{r['LOEUF_v4']:.3f}", (r["LOEUF_v4"], y), textcoords="offset points",
                    xytext=(0, -14), ha="center", fontsize=8.0, color=C_V4)
    ax.axvline(LOEUF_CUT, color=C_THRESH, ls="--", lw=1.6)
    ax.text(LOEUF_CUT + 0.006, ypos.min() - 0.35, f"cutoff {LOEUF_CUT}", color=C_THRESH, fontsize=8.6, va="bottom")
    ax.set_yticks(ypos); ax.set_yticklabels(d["gene"], fontsize=11)
    ax.set_ylim(ypos.min() - 0.7, ypos.max() + 0.7)
    ax.set_xlabel("LOEUF (lower = more constrained)", fontsize=10.5)
    ax.set_title("LOEUF shift with cohort size: gnomAD v2.1.1 \u2192 v4.1", fontsize=12, weight="bold")
    ax.set_xlim(-0.02, _xmax(np.concatenate([d["LOEUF_v2"].values, d["LOEUF_v4"].values]), floor=0.55))
    ax.grid(axis="x", ls=":", alpha=0.4)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    ax.legend(loc="lower right", fontsize=9, frameon=True)
    plt.tight_layout()
    plt.savefig(f"{out_prefix}.png", dpi=200, bbox_inches="tight")
    plt.savefig(f"{out_prefix}.svg", bbox_inches="tight")
    plt.close()
    return f"{out_prefix}.png"


def fig_druggability(df, out_prefix):
    """Drug-target reading: LOEUF (x) vs KO-tolerance tier (y), coloured by tier.

    Makes the constraint->druggability translation visual: the more constrained a
    gene, the higher up (more 'near-essential') it sits and the higher its systemic
    on-target risk. Requires the druggability columns from add_druggability_columns().
    """
    if "ko_tolerance_tier" not in df.columns:
        return None
    d = _plottable(df)
    if d.empty:
        return None
    # y = tier rank (0 near-essential at top). Spread genes within a tier vertically so
    # labels never collide, widening the per-tier band when a tier is crowded.
    d = d.copy()
    d["_baserank"] = d["ko_tolerance_tier"].map(TIER_RANK).fillna(4).astype(float)
    max_per_tier = int(d.groupby("_baserank").size().max())
    band = min(0.42, 0.12 + 0.09 * max(0, max_per_tier - 1))  # wider band if crowded
    d["_rank"] = d["_baserank"]
    for rk, grp in d.groupby("_baserank"):
        n = len(grp)
        if n > 1:
            offs = np.linspace(-band, band, n)
            d.loc[grp.sort_values("LOEUF_v2").index, "_rank"] = rk + offs
    colors = [TIER_HEX.get(t, "#999999") for t in d["ko_tolerance_tier"]]

    # taller figure when tiers are crowded, so vertical spread has room
    fig_h = max(5.2, 3.4 + 0.55 * max_per_tier)
    fig, ax = plt.subplots(figsize=(9.2, fig_h))
    ax.axvspan(-0.05, 0.35, color="#D55E00", alpha=0.06, zorder=0)
    ax.scatter(d["LOEUF_v2"], d["_rank"], s=180, c=colors, edgecolor="white", linewidth=1.2, zorder=3)
    xmax = _xmax(d["LOEUF_v2"], floor=0.6)
    # gene name to the right of the point; risk sublabel immediately under the name (same x),
    # so neither can land on a neighbouring marker.
    for _, r in d.iterrows():
        xr = r["LOEUF_v2"] + xmax * 0.018
        ax.annotate(r["gene"], (xr, r["_rank"]), va="center", ha="left", fontsize=9.4, weight="bold")
        ax.annotate(f'risk: {r.get("systemic_target_risk","?")}', (xr, r["_rank"]),
                    textcoords="offset points", xytext=(0, -10.5), va="center", ha="left",
                    fontsize=7.3, color="#666666")
    ax.axvline(LOEUF_CUT, color=C_THRESH, ls="--", lw=1.6, zorder=1)
    ax.text(LOEUF_CUT + 0.008, -0.5, f"LOEUF = {LOEUF_CUT}", color=C_THRESH, fontsize=8.6, va="top")
    ax.set_yticks([0, 1, 2, 3])
    ax.set_yticklabels(["Very low\n(near-essential)", "Low\n(LoF-intolerant)", "Intermediate", "Tolerant"], fontsize=9)
    ax.set_ylim(-0.75, 3.75)
    ax.invert_yaxis()  # near-essential at top
    ax.set_xlim(-0.05, xmax)
    ax.set_xlabel("LOEUF (gnomAD, flagging basis) — lower = more constrained", fontsize=10.5)
    ax.set_ylabel("Knockout-tolerance tier", fontsize=10.5)
    ax.set_title("Drug-target reading: constraint \u2192 knockout tolerance & on-target risk",
                 fontsize=12, weight="bold")
    ax.text(0.01, 0.02, "shaded = high systemic on-target risk zone (LOEUF < 0.35)",
            transform=ax.transAxes, fontsize=7.8, color="#B04A00")
    ax.grid(axis="x", ls=":", alpha=0.4)
    for sp in ["top", "right"]:
        ax.spines[sp].set_visible(False)
    plt.tight_layout()
    plt.savefig(f"{out_prefix}.png", dpi=200, bbox_inches="tight")
    plt.savefig(f"{out_prefix}.svg", bbox_inches="tight")
    plt.close()
    return f"{out_prefix}.png"


def make_all_figures(df, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    return {
        "ranked_loeuf": fig_ranked_loeuf(df, os.path.join(out_dir, "fig1_ranked_loeuf")),
        "pli_vs_loeuf": fig_pli_vs_loeuf(df, os.path.join(out_dir, "fig2_pli_vs_loeuf")),
        "version_shift": fig_version_shift(df, os.path.join(out_dir, "fig3_loeuf_v2_vs_v4")),
        "druggability": fig_druggability(df, os.path.join(out_dir, "fig4_druggability_tier")),
    }
