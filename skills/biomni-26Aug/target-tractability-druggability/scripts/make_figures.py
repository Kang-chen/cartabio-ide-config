#!/usr/bin/env python3
"""
make_figures.py — the three DATA figures for a target-druggability report (any target).
These are data-driven plots (matplotlib), NOT the conceptual infographic (that is made separately
with GenerateImage). Phylo brand colors; both PNG and SVG; colorblind-friendly.

Figures:
  fig1_tractability_buckets  — per-modality bucket matrix (met / not met)
  fig2_pocket_druggability   — bar chart of top-pocket druggability score(s) with thresholds
  fig3_modality_scorecard    — grouped bars of the scorecard dimensions per modality

Usage:
    python make_figures.py --ot /path/OT.json --pockets /path/pockets.json \
        --scores /path/scores.json --outdir /mnt/results/figures
"""
import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42
import matplotlib.pyplot as plt
import numpy as np

# Phylo palette
GOLD = "#D4A04A"; BLUE = "#0279EE"; GREEN = "#75A025"; ORANGE = "#FF9400"
PINK = "#FD9BED"; BLACK = "#000000"; WARM_GRAY = "#ECE9E2"; MUTED = "#8A8378"
MET_COLOR = GREEN; UNMET_COLOR = "#E7E2D8"

MODALITY_NAMES = {"SM": "Small molecule", "AB": "Antibody",
                  "PR": "PROTAC / degrader", "OC": "Other clinical"}


def save(fig, outdir, name):
    os.makedirs(outdir, exist_ok=True)
    png = os.path.join(outdir, f"{name}.png")
    svg = os.path.join(outdir, f"{name}.svg")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    fig.savefig(svg, bbox_inches="tight")
    plt.close(fig)
    return png


def fig_tractability(ot, outdir):
    tract = ot["tractability"]
    mods = [m for m in ["SM", "AB", "PR", "OC"] if m in tract] + \
           [m for m in tract if m not in ("SM", "AB", "PR", "OC")]
    n = len(mods)
    fig, axes = plt.subplots(1, n, figsize=(3.4 * n, 4.6), squeeze=False)
    axes = axes[0]
    for ax, mod in zip(axes, mods):
        labels = tract[mod]["true"] + tract[mod]["false"]
        vals = [1] * len(tract[mod]["true"]) + [0] * len(tract[mod]["false"])
        y = np.arange(len(labels))[::-1]
        colors = [MET_COLOR if v else UNMET_COLOR for v in vals]
        ax.barh(y, [1] * len(labels), color=colors, edgecolor="white", height=0.72)
        for yi, (lab, v) in zip(y, zip(labels, vals)):
            ax.text(0.5, yi, lab, ha="center", va="center", fontsize=7.5,
                    color=BLACK if v else MUTED,
                    fontweight="bold" if v else "normal")
        ax.set_title(f"{MODALITY_NAMES.get(mod, mod)}\n{tract[mod]['n_true']}/{tract[mod]['n_total']} met",
                     fontsize=10.5, fontweight="bold", color=BLACK)
        ax.set_xlim(0, 1); ax.set_ylim(-0.6, len(labels) - 0.4)
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(facecolor=MET_COLOR, label="Bucket met"),
                        Patch(facecolor=UNMET_COLOR, label="Not met")],
               loc="lower center", ncol=2, frameon=False, fontsize=9,
               bbox_to_anchor=(0.5, -0.03))
    sym = ot["target"]["symbol"]
    fig.suptitle(f"{sym} tractability buckets (Open Targets)", fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    return save(fig, outdir, "fig1_tractability_buckets")


def fig_pockets(pockets, outdir):
    """Bar chart of top-pocket druggability across the structures analyzed."""
    entries = []
    for key in ["apo_or_primary", "holo"]:
        p = pockets.get(key)
        if p and p.get("drug_score") is not None:
            entries.append((p.get("label", key), p["drug_score"], p.get("druggable_class")))
    # allow arbitrary extra entries (e.g. an allosteric pocket added by the caller)
    for k, p in pockets.items():
        if k in ("apo_or_primary", "holo", "apo_to_holo_fold"):
            continue
        if isinstance(p, dict) and p.get("drug_score") is not None:
            entries.append((p.get("label", k), p["drug_score"], p.get("druggable_class")))
    if not entries:
        return None
    labels = [e[0] for e in entries]
    scores = [e[1] for e in entries]
    palette = [ORANGE, BLUE, GREEN, PINK, GOLD]
    colors = [palette[i % len(palette)] for i in range(len(entries))]

    fig, ax = plt.subplots(figsize=(2.0 * len(entries) + 3.0, 5.0))
    x = np.arange(len(entries))
    ax.bar(x, scores, color=colors, edgecolor="white", width=0.62, zorder=3)
    for xi, s in zip(x, scores):
        ax.text(xi, s + 0.02, f"{s:.3f}", ha="center", va="bottom",
                fontsize=10, fontweight="bold", color=BLACK)
    ax.axhline(0.5, ls="--", lw=1.2, color=MUTED, zorder=2)
    ax.axhline(0.2, ls=":", lw=1.2, color=MUTED, zorder=2)
    # place threshold labels to the RIGHT of the plotting area, slightly ABOVE each line,
    # in a high-contrast color so they never collide with bars or the reference lines
    right_edge = len(entries) - 0.5 + 0.55
    ax.text(right_edge + 0.08, 0.5 + 0.035, "druggable (>0.5)", va="bottom", ha="left",
            fontsize=8.5, color=BLACK)
    ax.text(right_edge + 0.08, 0.2 + 0.035, "borderline (0.2)", va="bottom", ha="left",
            fontsize=8.5, color=BLACK)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("fpocket druggability score", fontsize=11, labelpad=10)
    ax.set_ylim(0, 1.12)
    ax.set_xlim(-0.7, right_edge + 2.0)
    ax.set_title("Top-pocket druggability by structure", fontsize=13, fontweight="bold")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", ls=":", alpha=0.35, zorder=0)
    # reserve left/right room explicitly; do NOT call tight_layout (it overrides these margins)
    fig.subplots_adjust(left=0.17, right=0.97, bottom=0.13, top=0.9)
    return save(fig, outdir, "fig2_pocket_druggability")


def _to_numeric(v):
    """Convert a scorecard value to float for plotting; 'NA' -> NaN (bar omitted)."""
    return np.nan if v == "NA" else float(v)


def fig_scorecard(scores, outdir):
    """Grouped bars: dimensions per modality (0-3). NA renders as a text label, not a bar."""
    dims = ["Tractability", "Structural", "Clinical", "Overall"]
    mods = list(scores["modalities"].keys())
    data = np.array([[_to_numeric(scores["modalities"][m][d.lower()]) for d in dims]
                     for m in mods], dtype=float)
    x = np.arange(len(dims))
    w = 0.8 / max(len(mods), 1)
    palette = [GREEN, ORANGE, BLUE, PINK, GOLD]
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    for i, m in enumerate(mods):
        ax.bar(x + i * w - 0.4 + w / 2, data[i], width=w, label=MODALITY_NAMES.get(m, m),
               color=palette[i % len(palette)], edgecolor="white", zorder=3)
    # annotate NA cells with a text label (no bar is drawn for NaN)
    for i, m in enumerate(mods):
        for j, d in enumerate(dims):
            if scores["modalities"][m][d.lower()] == "NA":
                ax.text(x[j] + i * w - 0.4 + w / 2, 0.15, "NA", ha="center", va="bottom",
                        fontsize=8, color=MUTED, fontweight="bold", zorder=4)
    ax.set_xticks(x); ax.set_xticklabels(dims, fontsize=10.5)
    ax.set_yticks([0, 1, 2, 3])
    ax.set_yticklabels(["None", "Low", "Med", "High"], fontsize=10)
    ax.set_ylim(0, 3.4)
    ax.set_ylabel("evidence strength", fontsize=11)
    ax.set_title("Modality viability scorecard", fontsize=13, fontweight="bold")
    ax.legend(frameon=False, fontsize=9, ncol=len(mods), loc="upper center",
              bbox_to_anchor=(0.5, -0.08))
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", ls=":", alpha=0.35, zorder=0)
    fig.tight_layout()
    return save(fig, outdir, "fig3_modality_scorecard")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ot", required=True)
    ap.add_argument("--pockets", default=None)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--outdir", default="/mnt/results/figures")
    args = ap.parse_args()
    ot = json.load(open(args.ot))
    scores = json.load(open(args.scores))
    f1 = fig_tractability(ot, args.outdir)
    print(f"[fig] {f1}")
    if args.pockets and os.path.exists(args.pockets):
        f2 = fig_pockets(json.load(open(args.pockets)), args.outdir)
        print(f"[fig] {f2}")
    f3 = fig_scorecard(scores, args.outdir)
    print(f"[fig] {f3}")


if __name__ == "__main__":
    main()
