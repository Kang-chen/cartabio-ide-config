#!/usr/bin/env python3
"""
make_figures.py -- Python fallback for the two data-driven figures (if R is unavailable).
Produces fig1 (evidence-matrix heatmap) and fig2 (consensus summary bar) as PNG+SVG and a
fig_manifest.csv. Colorblind-safe, Liberation Sans.

Usage: python make_figures.py --run RUN [--title-prefix "..."]
"""
import argparse, os
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import pandas as pd

VOTE_COLORS = {"INHIBIT": "#0279EE", "ACTIVATE": "#FF9400",
               "INHIBIT (allele-specific)": "#75A025",
               "not_informative": "#ECE9E2", "CONTESTED": "#B0413E"}
TIER_COLORS = {"High": "#0279EE", "High-Moderate": "#75A025",
               "Moderate": "#D4A04A", "Low-Contested": "#B0413E"}


def vote_label(vote, note):
    if vote == "INHIBIT" and "allele-specific" in str(note).lower():
        return "INHIBIT (allele-specific)"
    return vote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--title-prefix", default="")
    args = ap.parse_args()
    figdir = os.path.join(args.run, "figures")
    os.makedirs(figdir, exist_ok=True)
    pfx = args.title_prefix

    mat = pd.read_csv(os.path.join(args.run, "data", "evidence_matrix.csv")).fillna("")
    calls = pd.read_csv(os.path.join(args.run, "data", "consensus_calls.csv")).fillna("")
    manifest = []

    # ---- FIG 1: heatmap ----
    targets = list(dict.fromkeys(mat["target"]))
    axes = list(dict.fromkeys(mat["axis"]))
    fig, ax = plt.subplots(figsize=(9.6, 4.2))
    for yi, axis in enumerate(reversed(axes)):
        for xi, tgt in enumerate(targets):
            cell = mat[(mat["target"] == tgt) & (mat["axis"] == axis)]
            if cell.empty:
                continue
            vote = cell["vote"].iloc[0]
            lab = vote_label(vote, cell["note"].iloc[0])
            ax.add_patch(plt.Rectangle((xi - 0.5, yi - 0.5), 1, 1,
                         facecolor=VOTE_COLORS.get(lab, "#ECE9E2"),
                         edgecolor="white", linewidth=2))
            txt = "n/i" if vote == "not_informative" else vote
            ax.text(xi, yi, txt, ha="center", va="center", fontsize=8,
                    color="#8A8378" if vote == "not_informative" else "white")
    ax.set_xticks(range(len(targets))); ax.set_xticklabels(targets, fontweight="bold")
    ax.set_yticks(range(len(axes))); ax.set_yticklabels(list(reversed(axes)))
    ax.set_xlim(-0.5, len(targets) - 0.5); ax.set_ylim(-0.5, len(axes) - 0.5)
    ax.set_title(f"{pfx}Evidence matrix: per-axis direction of effect", fontweight="bold")
    for s in ax.spines.values():
        s.set_visible(False)
    present = [v for v in VOTE_COLORS if v in set(
        mat.apply(lambda r: vote_label(r["vote"], r["note"]), axis=1))]
    ax.legend(handles=[Patch(facecolor=VOTE_COLORS[v], label=v) for v in present],
              loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False, fontsize=8)
    fig.tight_layout()
    f1 = os.path.join(figdir, "fig1_evidence_matrix.png")
    fig.savefig(f1, dpi=200, bbox_inches="tight", facecolor="white")
    fig.savefig(f1.replace(".png", ".svg"), bbox_inches="tight", facecolor="white")
    plt.close(fig)
    manifest.append({"file": "fig1_evidence_matrix.png", "kind": "evidence_matrix",
                     "caption": "Evidence matrix heatmap. Blue = INHIBIT; green = INHIBIT "
                     "(allele-specific); orange = ACTIVATE; grey = not informative (n/i)."})

    # ---- FIG 2: consensus bar ----
    calls = calls.sort_values("n_agree")
    fig, ax = plt.subplots(figsize=(9.6, 0.7 + 0.6 * len(calls)))
    ypos = range(len(calls))
    colors = [TIER_COLORS.get(c, "#999999") for c in calls["confidence"]]
    ax.barh(list(ypos), calls["n_agree"], color=colors, height=0.66)
    for y, (_, r) in zip(ypos, calls.iterrows()):
        ax.text(r["n_agree"] + 0.05, y, f"{r['consensus']}  ({r['concordance']})",
                va="center", fontsize=9, color="#2C2A26")
    ax.set_yticks(list(ypos)); ax.set_yticklabels(calls["target"], fontweight="bold")
    ax.set_xlabel("# concordant informative axes")
    ax.set_title(f"{pfx}Consensus direction & agreement", fontweight="bold")
    maxn = max(calls["n_agree"]) if len(calls) else 1
    ax.set_xlim(0, maxn * 1.4 + 0.5)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    present_t = [t for t in TIER_COLORS if t in set(calls["confidence"])]
    ax.legend(handles=[Patch(facecolor=TIER_COLORS[t], label=t) for t in present_t],
              loc="lower right", frameon=False, fontsize=8, title="Confidence tier")
    fig.tight_layout()
    f2 = os.path.join(figdir, "fig2_consensus_summary.png")
    fig.savefig(f2, dpi=200, bbox_inches="tight", facecolor="white")
    fig.savefig(f2.replace(".png", ".svg"), bbox_inches="tight", facecolor="white")
    plt.close(fig)
    manifest.append({"file": "fig2_consensus_summary.png", "kind": "consensus_summary",
                     "caption": "Per-target consensus direction; bar length = number of "
                     "informative axes that agree; color = confidence tier."})

    pd.DataFrame(manifest).to_csv(os.path.join(figdir, "fig_manifest.csv"), index=False)
    print(f"Wrote figures + fig_manifest.csv to {figdir}")
    print("REMINDER: run Read mode='media_output_check' on each PNG; regenerate on failure.")


if __name__ == "__main__":
    main()
