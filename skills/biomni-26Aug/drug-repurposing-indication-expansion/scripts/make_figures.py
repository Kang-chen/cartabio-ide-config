"""Generate the four core data-driven report figures (disease-agnostic).

All figures are driven purely by the analysis outputs (annotated frame, disease sets,
control results), so they generalise to any disease with no code change. Saved as both
.png (for PDF embedding) and .svg (editable) into <outdir>/figures/.

Figures:
  fig1_score_distribution  -- histogram of S_reversal across all perturbations, with the
                              reversal (positive) region shaded; annotates n significant.
  fig2_top20_approved      -- horizontal bar of the top-N approved reversers by consensus
                              rank, colored by significance, MOA annotated.
  fig3_signature_overview  -- (A) up/down gene counts of the disease signature;
                              (B) a few representative marker genes (agent-supplied).
  fig4_moa_and_validation  -- (A) top MOA themes among approved reversers;
                              (B) control validation: named controls' scores, colored by
                              expected direction (the internal-validity panel).

Public API:
  make_all(annotated_df, disease_up, disease_dn, control_df, outdir,
           disease_label, top_n=20, marker_genes=None, moa_theme_df=None) -> dict(paths)
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
import matplotlib.pyplot as plt

from report_style import OKABE_ITO

GREEN = OKABE_ITO["green"]
RED = OKABE_ITO["red"]
BLUE = OKABE_ITO["blue"]
ORANGE = OKABE_ITO["orange"]
GREY = OKABE_ITO["grey"]


def _save(fig, outdir, name):
    os.makedirs(outdir, exist_ok=True)
    png = os.path.join(outdir, f"{name}.png")
    svg = os.path.join(outdir, f"{name}.svg")
    fig.savefig(png, dpi=200, bbox_inches="tight")
    fig.savefig(svg, bbox_inches="tight")
    plt.close(fig)
    return png


def fig_score_distribution(annotated_df, outdir, fdr_thresh=0.05):
    s = annotated_df["S_reversal"].values
    n_sig = int(((annotated_df["S_reversal"] > 0) & (annotated_df["fdr_reversal"] < fdr_thresh)).sum())
    fig, ax = plt.subplots(figsize=(7, 4))
    lo, hi = np.nanmin(s), np.nanmax(s)
    bins = np.linspace(lo, hi, 46)
    ax.hist(s, bins=bins, color=GREY, edgecolor="white", linewidth=0.4)
    ax.axvspan(0, hi, color=GREEN, alpha=0.10)
    ax.axvline(0, color="black", lw=1)
    ax.set_xlabel("Reversal connectivity score ($S_{reversal}$)")
    ax.set_ylabel("Number of perturbations")
    ax.text(0.98, 0.95, f"{n_sig} significant reversers\n(FDR < {fdr_thresh})",
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=GREEN))
    ax.annotate("reversal", xy=(hi * 0.6, ax.get_ylim()[1] * 0.5), color=GREEN, fontsize=10, ha="center")
    ax.annotate("mimicry", xy=(lo * 0.6, ax.get_ylim()[1] * 0.5), color=RED, fontsize=10, ha="center")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    return _save(fig, outdir, "fig1_score_distribution")


def fig_top_approved(annotated_df, outdir, top_n=20, fdr_thresh=0.05, disease_label=None):
    # Select AND display the top_n approved reversers by the single canonical ranking. The bar
    # order equals the Table-1 / literature-slate order (canonical_rank), so no view disagrees
    # about "the top candidate". Canonical #1-of-the-approved-subset is drawn at the TOP.
    rc = "canonical_rank" if "canonical_rank" in annotated_df.columns else "consensus_rank"
    df = annotated_df[(annotated_df["approved"]) & (annotated_df["S_reversal"] > 0)].copy()
    df = df.sort_values(rc).head(top_n)
    # barh draws bottom-to-top, so reverse to put the best canonical rank at the top
    df = df.iloc[::-1].reset_index(drop=True)
    colors = [GREEN if f < fdr_thresh else GREY for f in df["fdr_reversal"]]
    has_ns = bool((df["fdr_reversal"] >= fdr_thresh).any())
    fig, ax = plt.subplots(figsize=(7.2, max(4, 0.32 * len(df))))
    ax.barh(range(len(df)), df["S_reversal"], color=colors, edgecolor="white")
    ax.set_yticks(range(len(df)))
    labels = []
    for _, r in df.iterrows():
        moa = str(r["moa"]).split("|")[0] if pd.notna(r["moa"]) else ""
        rnk = f"#{int(r[rc])}  " if rc in r and pd.notna(r[rc]) else ""
        labels.append(f"{rnk}{r['drug']}" + (f"  ·  {moa}" if moa and moa != "nan" else ""))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Reversal connectivity score ($S_{reversal}$)  ·  ordered by canonical rank")
    from matplotlib.patches import Patch
    handles = [Patch(color=GREEN, label=f"FDR < {fdr_thresh}")]
    if has_ns:
        handles.append(Patch(color=GREY, label="n.s."))
    ax.legend(handles=handles, loc="lower right", fontsize=8, frameon=False)
    _title = f"Top approved repurposing candidates{(' — ' + disease_label) if disease_label else ''}"
    ax.set_title(_title, fontsize=11, loc="left")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    return _save(fig, outdir, "fig2_top20_approved")


def fig_signature_overview(disease_up, disease_dn, outdir, disease_label, marker_genes=None):
    fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.4), gridspec_kw={"width_ratios": [1, 1.5]})
    ax = axes[0]
    ax.bar(["Up", "Down"], [len(disease_up), len(disease_dn)], color=[RED, BLUE], edgecolor="white")
    for i, v in enumerate([len(disease_up), len(disease_dn)]):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_ylabel("Genes in signature")
    ax.set_title("A. Disease signature size", fontsize=10, loc="left")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)

    ax2 = axes[1]
    ax2.axis("off")
    ax2.set_title(f"B. Representative {disease_label} markers", fontsize=10, loc="left")
    if marker_genes:
        up_m = marker_genes.get("up", [])[:6]
        dn_m = marker_genes.get("dn", [])[:6]
        txt = ""
        if up_m:
            txt += "Up: " + ", ".join(up_m) + "\n"
        if dn_m:
            txt += "Down: " + ", ".join(dn_m)
        ax2.text(0.02, 0.75, txt, fontsize=9, family="monospace", va="top",
                 transform=ax2.transAxes)
    else:
        ax2.text(0.02, 0.6, f"Signature: {len(disease_up)} up / {len(disease_dn)} down genes\n"
                            "A drug is predicted therapeutic if its\nperturbation reverses this pattern.",
                 fontsize=9, va="top", transform=ax2.transAxes)
    return _save(fig, outdir, "fig3_signature_overview")


def fig_moa_and_validation(annotated_df, control_df, outdir, moa_theme_df=None, fdr_thresh=0.05):
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.8), gridspec_kw={"width_ratios": [1.2, 1]})

    # A: MOA themes among approved reversers (count-based; honest, not necessarily FDR-sig)
    ax = axes[0]
    df = annotated_df[(annotated_df["approved"]) & (annotated_df["S_reversal"] > 0) & annotated_df["moa"].notna()]
    from collections import Counter
    c = Counter()
    for m in df["moa"]:
        for t in str(m).split("|"):
            t = t.strip()
            if t and t != "nan":
                c[t] += 1
    top = c.most_common(8)[::-1]
    if top:
        ax.barh(range(len(top)), [v for _, v in top], color=BLUE, edgecolor="white")
        ax.set_yticks(range(len(top)))
        ax.set_yticklabels([k for k, _ in top], fontsize=8)
        ax.set_xlabel("Approved reversers with MOA")
    ax.set_title("A. Mechanistic themes (nominal)", fontsize=10, loc="left")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)

    # B: control validation
    ax2 = axes[1]
    if control_df is not None and len(control_df):
        cd = control_df[control_df["present"]].copy()
        cd = cd.sort_values("S_reversal")
        colors = [GREEN if e == "reverser" else RED for e in cd["expected"]]
        ax2.barh(range(len(cd)), cd["S_reversal"], color=colors, edgecolor="white")
        ax2.set_yticks(range(len(cd)))
        ax2.set_yticklabels(cd["control"], fontsize=8)
        ax2.axvline(0, color="black", lw=1)
        ax2.set_xlabel("$S_{reversal}$")
        from matplotlib.patches import Patch
        _handles = [Patch(color=GREEN, label="expected reverser")]
        if (cd["expected"] == "mimic").any():
            _handles.append(Patch(color=RED, label="expected mimic"))
        ax2.legend(handles=_handles, loc="lower right", fontsize=7, frameon=False)
    else:
        ax2.axis("off")
        ax2.text(0.5, 0.5, "No controls specified", ha="center", va="center", transform=ax2.transAxes)
    ax2.set_title("B. Control validation", fontsize=10, loc="left")
    for sp in ("top", "right"):
        ax2.spines[sp].set_visible(False)
    return _save(fig, outdir, "fig4_moa_and_validation")


def make_all(annotated_df, disease_up, disease_dn, control_df, outdir,
             disease_label, top_n=20, marker_genes=None, moa_theme_df=None, fdr_thresh=0.05):
    figdir = os.path.join(outdir, "figures")
    paths = {}
    paths["fig1"] = fig_score_distribution(annotated_df, figdir, fdr_thresh)
    paths["fig2"] = fig_top_approved(annotated_df, figdir, top_n, fdr_thresh, disease_label)
    paths["fig3"] = fig_signature_overview(disease_up, disease_dn, figdir, disease_label, marker_genes)
    paths["fig4"] = fig_moa_and_validation(annotated_df, control_df, figdir, moa_theme_df, fdr_thresh)
    return paths
