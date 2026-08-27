#!/usr/bin/env python3
"""
make_figures.py  --  Phylo-palette figures for the antibody assessment report.

Generalized to ANY construct set (dynamic ordering / colouring, not hard-coded
to the 6-construct 4D5 example) and MODE-AWARE:
  * immunogenicity panels degrade to a labelled "predictor unavailable" placeholder
    when the MHC-II axis could not be computed (never invents bars);
  * the benchmark figure (identity vs reference + back-mutation concordance) is
    only drawn in reference-present mode.

Figures produced (into <outdir>, PNG + SVG @200 dpi):
  fig1_developability   liability burden (weighted) + N-glyco, all constructs
  fig2_immunogenicity   Fv MHC-II epitope load + promiscuous FR/CDR split
  fig3_tradeoff         humanness vs immunogenicity frontier
  fig4_scorecard        normalized MASTER heatmap (green = better)
  fig5_benchmark        (reference-present only) identity + concordance

Public API:
  make_all(master, dev_summ, fv_immuno, immuno_status, humanness,
           outdir, order=None, ref_key=None, benchmark=None)

Colours follow the Phylo palette. Fonts: Liberation Sans; SVG text kept editable.
"""
from __future__ import annotations
import os
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---- Phylo palette ----
PHYLO_GOLD = "#D4A04A"; PHYLO_BLUE = "#0279EE"; PHYLO_GREEN = "#75A025"
PHYLO_ORANGE = "#FF9400"; PHYLO_PINK = "#FD9BED"; PHYLO_BLACK = "#111111"
PHYLO_WARM_GRAY = "#ECE9E2"; MUTED = "#8A8378"

# palette for graft/backmut/parent/reference kinds (cycled for extra constructs)
_KIND_COLOR = {"parent": PHYLO_ORANGE, "graft": PHYLO_BLUE,
               "backmut": PHYLO_GREEN, "reference": PHYLO_BLACK}
_CYCLE = [PHYLO_BLUE, PHYLO_GREEN, PHYLO_GOLD, PHYLO_PINK, "#9EC3F0",
          "#B5D98A", MUTED]


def _infer_kind(name: str):
    n = name.lower()
    if "graft" in n:
        return "graft"
    if "bmut" in n or "backmut" in n:
        return "backmut"
    if any(r in n for r in ("tras", "reference", "_ref", "clinical")):
        return "reference"
    return "parent"


def _colors_for(order, ref_key=None):
    cols = {}
    ci = 0
    for c in order:
        if ref_key and c == ref_key:
            cols[c] = PHYLO_BLACK
            continue
        kind = _infer_kind(c)
        if kind in _KIND_COLOR and kind != "graft" and kind != "backmut":
            cols[c] = _KIND_COLOR[kind]
        else:
            cols[c] = _CYCLE[ci % len(_CYCLE)]
            ci += 1
    return cols


def _short(name):
    return (name.replace("hu_", "").replace("_", "\n")
            if len(name) > 10 else name)


def _savefig(fig, name, outdir):
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for ext in ("png", "svg"):
        p = os.path.join(outdir, f"{name}.{ext}")
        fig.savefig(p, dpi=200, bbox_inches="tight", facecolor="white")
        paths.append(p)
    plt.close(fig)
    return paths


# ---------------------------------------------------------------------------
def fig_developability(dev_summ, order, colors, outdir):
    ms = dev_summ.set_index("construct").loc[order]
    x = np.arange(len(order))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    ax = axes[0]
    ax.bar(x, ms["total_weighted_burden"], color=[colors[c] for c in order],
           edgecolor="white", width=0.65)
    for i, c in enumerate(order):
        ax.text(i, ms.loc[c, "total_weighted_burden"] + 0.3,
                f"{ms.loc[c,'total_weighted_burden']:.0f}", ha="center", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels([_short(c) for c in order], fontsize=8)
    ax.set_ylabel("CDR-weighted liability burden")
    ax.set_title("A. Developability liability burden", fontsize=11, weight="bold")
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    ax.bar(x - 0.2, ms["total_liabilities"], width=0.4, label="Total motifs",
           color=MUTED, edgecolor="white")
    ax.bar(x + 0.2, ms["CDR_liabilities"], width=0.4, label="CDR-resident",
           color=PHYLO_GOLD, edgecolor="white")
    ax.set_xticks(x); ax.set_xticklabels([_short(c) for c in order], fontsize=8)
    ax.set_ylabel("Liability motif count")
    ax.set_title("B. Motif count (total vs CDR)", fontsize=11, weight="bold")
    ax.legend(fontsize=8, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Developability liabilities across constructs",
                 fontsize=12.5, weight="bold", y=1.02)
    fig.tight_layout()
    return _savefig(fig, "fig1_developability", outdir)


def fig_immunogenicity(fv_immuno, immuno_status, order, colors, outdir):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    if immuno_status != "ok" or not len(fv_immuno):
        for ax in axes:
            ax.axis("off")
        axes[0].text(0.5, 0.5,
                     "MHC-II immunogenicity predictor unavailable\n"
                     "(no local NetMHCIIpan and IEDB API not reachable).\n"
                     "This axis is intentionally left blank rather than\n"
                     "reporting fabricated epitope counts.",
                     ha="center", va="center", fontsize=10, color=MUTED,
                     style="italic",
                     bbox=dict(boxstyle="round", fc=PHYLO_WARM_GRAY, ec=MUTED))
        fig.suptitle("T-cell (MHC-II) immunogenicity - UNAVAILABLE",
                     fontsize=12.5, weight="bold", y=1.0)
        return _savefig(fig, "fig2_immunogenicity", outdir)

    fs = fv_immuno.set_index("construct").loc[order]
    x = np.arange(len(order))
    ax = axes[0]
    bars = ax.bar(x, fs["Fv_epitope_load"], color=[colors[c] for c in order],
                  edgecolor="white", width=0.65)
    for b, c in zip(bars, order):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                int(fs.loc[c, "Fv_epitope_load"]), ha="center", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels([_short(c) for c in order], fontsize=8)
    ax.set_ylabel("Fv MHC-II epitope load\n(allele-binding events, rank<=10)")
    ax.set_title("A. Total MHC-II epitope load", fontsize=11, weight="bold")
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    ax.bar(x, fs["promisc_in_CDR"], width=0.65, label="CDR-resident (constrained)",
           color=PHYLO_GOLD, edgecolor="white")
    ax.bar(x, fs["promisc_in_FR"], width=0.65, bottom=fs["promisc_in_CDR"],
           label="Framework-resident (addressable)", color=PHYLO_BLUE,
           edgecolor="white")
    ax.set_xticks(x); ax.set_xticklabels([_short(c) for c in order], fontsize=8)
    ax.set_ylabel("Promiscuous epitopes (>=2 alleles)")
    ax.set_title("B. Promiscuous epitopes: framework vs CDR",
                 fontsize=11, weight="bold")
    ax.legend(fontsize=8, frameon=False, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("T-cell (MHC-II) immunogenicity across constructs",
                 fontsize=12.5, weight="bold", y=1.02)
    fig.tight_layout()
    return _savefig(fig, "fig2_immunogenicity", outdir)


def fig_tradeoff(master, immuno_status, order, colors, outdir, ref_key=None):
    if immuno_status != "ok" or "Fv_epitope_load" not in master.columns:
        return []   # trade-off needs the immunogenicity axis
    mm = master.set_index("construct").loc[order]
    if "mean_FR_humanness_%" in mm.columns:
        xh = mm["mean_FR_humanness_%"]
    else:
        xh = (mm["VH_FR_identity_%"] + mm["VL_FR_identity_%"]) / 2
    yl = mm["Fv_epitope_load"]
    fig, ax = plt.subplots(figsize=(7.4, 5.4))
    # y-limits with headroom so labels never collide with the axes (data
    # values are the epitope loads; add ~12% padding on each side)
    yv = yl.dropna().values.astype(float)
    xv = xh.dropna().values.astype(float)
    if len(yv):
        ypad = max((yv.max() - yv.min()) * 0.16, 3)
        ax.set_ylim(yv.min() - ypad, yv.max() + ypad)
    if len(xv):
        xpad = max((xv.max() - xv.min()) * 0.10, 3)
        ax.set_xlim(xv.min() - xpad, xv.max() + xpad)
    ymid = (yv.min() + yv.max()) / 2 if len(yv) else 0
    for c in order:
        if pd.isna(xh[c]) or pd.isna(yl[c]):
            continue
        ax.scatter(xh[c], yl[c], s=260, color=colors[c], edgecolor="white",
                   linewidth=1.5, zorder=3)
        # place label on the side with more room: below if point is high,
        # above if low (axis is inverted later, so compare raw value to mid)
        dy = -22 if yl[c] >= ymid else 20
        va = "top" if dy < 0 else "bottom"
        ax.annotate(_short(c).replace("\n", " "), (xh[c], yl[c]), fontsize=8,
                    ha="center", va=va, xytext=(0, dy),
                    textcoords="offset points", weight="bold", zorder=4)
    if ref_key and ref_key in mm.index:
        ax.axhline(yl[ref_key], ls=":", color=MUTED, lw=0.8)
        ax.axvline(xh[ref_key], ls=":", color=MUTED, lw=0.8)
    ax.set_xlabel("Framework humanness (mean VH/VL % identity to human germline)")
    ax.set_ylabel("Fv MHC-II epitope load  (fewer = more favorable \u2191)")
    ax.set_title("Humanness - immunogenicity frontier\n"
                 "(naive graft = most human & least immunogenic; "
                 "back-mutation trades both for affinity)",
                 fontsize=10.5, weight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.invert_yaxis()   # fewer epitopes plotted higher (= more favorable)
    # explicit "more favorable" cue in the top-left corner
    ax.annotate("more favorable", xy=(0.015, 0.965), xycoords="axes fraction",
                fontsize=8, style="italic", color=MUTED, ha="left", va="top")
    fig.tight_layout()
    return _savefig(fig, "fig3_tradeoff", outdir)


def fig_scorecard(master, immuno_status, order, outdir):
    sc = master.set_index("construct").loc[order].copy()
    metrics = [("Total liability\nburden", "total_weighted_burden", "lower"),
               ("N-glyco\nsites", "N_glyco_sites", "lower")]
    # named aggregation axis (AGGRESCAN a3v): CDR-weighted APR burden + CDR APRs
    if "agg_weighted" in sc.columns:
        metrics += [("Aggregation\nburden (a3v)", "agg_weighted", "lower")]
    if "APR_in_CDR" in sc.columns:
        metrics += [("CDR aggregation\nregions", "APR_in_CDR", "lower")]
    if immuno_status == "ok" and "Fv_epitope_load" in sc.columns:
        metrics += [("MHC-II epitope\nload", "Fv_epitope_load", "lower"),
                    ("Promiscuous\nepitopes", "Fv_promiscuous", "lower"),
                    ("FR-resident\nepitopes", "promisc_in_FR", "lower")]
    metrics += [("VH framework\nhumanness", "VH_FR_identity_%", "higher"),
                ("VL framework\nhumanness", "VL_FR_identity_%", "higher")]
    metrics = [m for m in metrics if m[1] in sc.columns]

    M = np.zeros((len(order), len(metrics)))
    raw = np.zeros_like(M)
    for j, (_, col, direction) in enumerate(metrics):
        vals = sc[col].values.astype(float)
        raw[:, j] = vals
        finite = vals[np.isfinite(vals)]
        rng = (finite.max() - finite.min()) if len(finite) else 0
        if rng == 0:
            norm = np.ones_like(vals)
        else:
            norm = (vals - finite.min()) / rng
            if direction == "lower":
                norm = 1 - norm
        M[:, j] = norm

    fig, ax = plt.subplots(figsize=(1.35 * len(metrics) + 2, 0.7 * len(order) + 2))
    im = ax.imshow(M, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
    ax.set_xticks(range(len(metrics)))
    ax.set_xticklabels([m[0] for m in metrics], fontsize=8.5)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([_short(c).replace("\n", " ") for c in order], fontsize=9)
    for i in range(len(order)):
        for j in range(len(metrics)):
            v = raw[i, j]
            if not np.isfinite(v):
                txt = "NA"
            else:
                txt = f"{v:.0f}" if v == int(v) else f"{v:.1f}"
            ax.text(j, i, txt, ha="center", va="center", fontsize=8,
                    color="black" if 0.25 < M[i, j] < 0.85 else "white",
                    weight="bold")
    ax.set_title("Construct scorecard: developability, immunogenicity & humanness\n"
                 "(green = more favorable; raw values shown)",
                 fontsize=11, weight="bold")
    fig.tight_layout()
    return _savefig(fig, "fig4_scorecard", outdir)


def fig_aggregation(dev_summ, order, colors, outdir):
    """Named aggregation-propensity figure (AGGRESCAN a3v).

    A. CDR-weighted aggregation burden (sum of APR excess-propensity areas,
       CDR-resident regions up-weighted 1.6x).
    B. Aggregation-prone-region (APR) count, split framework vs CDR.
    """
    if "agg_weighted" not in dev_summ.columns:
        return []
    ms = dev_summ.set_index("construct").loc[order]
    x = np.arange(len(order))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))

    ax = axes[0]
    bars = ax.bar(x, ms["agg_weighted"], color=[colors[c] for c in order],
                  edgecolor="white", width=0.65)
    for b, c in zip(bars, order):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.2,
                f"{ms.loc[c, 'agg_weighted']:.1f}", ha="center", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels([_short(c) for c in order], fontsize=8)
    ax.set_ylabel("CDR-weighted aggregation burden\n(AGGRESCAN a3v APR area)")
    ax.set_title("A. Aggregation burden (a3v)", fontsize=11, weight="bold")
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    ax.bar(x, ms["APR_in_CDR"], width=0.65, label="CDR-resident (constrained)",
           color=PHYLO_GOLD, edgecolor="white")
    ax.bar(x, ms["APR_in_FR"], width=0.65, bottom=ms["APR_in_CDR"],
           label="Framework-resident (addressable)", color=PHYLO_BLUE,
           edgecolor="white")
    ax.set_xticks(x); ax.set_xticklabels([_short(c) for c in order], fontsize=8)
    ax.set_ylabel("Aggregation-prone regions (APRs)")
    ax.set_title("B. APRs: framework vs CDR", fontsize=11, weight="bold")
    ax.legend(fontsize=8, frameon=False, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Aggregation propensity across constructs "
                 "(AGGRESCAN a3v, sequence-based)",
                 fontsize=12.5, weight="bold", y=1.02)
    fig.tight_layout()
    return _savefig(fig, "fig6_aggregation", outdir)


def fig_benchmark(benchmark, outdir):
    """Reference-present only: identity bars + back-mutation concordance grid."""
    if not benchmark:
        return []
    ident = benchmark["identity"]
    concord = benchmark["concordance"]
    # height scales with number of concordance rows so labels stay legible
    nrow = max(len(concord), 1)
    figh = max(5.2, 0.42 * nrow + 2.2)
    fig, axes = plt.subplots(1, 2, figsize=(12.0, figh),
                             gridspec_kw={"width_ratios": [1, 1.25]})

    ax = axes[0]
    d = ident.dropna(subset=["VH_vs_ref_%"])
    x = np.arange(len(d))
    ax.bar(x - 0.2, d["VH_vs_ref_%"], width=0.4, label="VH", color=PHYLO_BLUE,
           edgecolor="white")
    ax.bar(x + 0.2, d["VL_vs_ref_%"], width=0.4, label="VL", color=PHYLO_GOLD,
           edgecolor="white")
    ax.set_xticks(x)
    ax.set_xticklabels([_short(c).replace("\n", " ") for c in d["construct"]],
                       fontsize=8, rotation=25, ha="right")
    ax.set_ylabel("% identity to reference")
    ax.set_ylim(60, 102)
    # truncation note ABOVE the axes (in the title band) so it never overlaps bars
    ax.set_title("A. Blind design similarity to reference\n"
                 "(y-axis truncated at 60%)", fontsize=11, weight="bold")
    ax.legend(fontsize=9, frameon=False, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    cd = concord.reset_index(drop=True)
    # colorblind-safe: blue=concordant, orange=discordant, gray=over-correction
    cmap = {"concordant": PHYLO_BLUE, "over": "#9A9384",
            "discordant": PHYLO_ORANGE}
    colors = []
    for _, r in cd.iterrows():
        if r["concordant"]:
            colors.append(cmap["concordant"])
        elif r["over_correction"]:
            colors.append(cmap["over"])
        else:
            colors.append(cmap["discordant"])
    ax.barh(range(len(cd)), [1] * len(cd), color=colors, edgecolor="white",
            height=0.72)
    for i, (_, r) in enumerate(cd.iterrows()):
        txt = (f"{r['position']}: graft {r['graft_human']}\u2192des "
               f"{r['design_backmut']}  |  ref {r['reference']}")
        fg = "white" if r["concordant"] else PHYLO_BLACK
        ax.text(0.02, i, txt, va="center", fontsize=8.5,
                color=fg, weight="bold")
        # redundant (non-color) encoding so categories don't rely on color alone
        if r["concordant"]:
            sym = "match"
        elif r["over_correction"]:
            sym = "over-corr."
        else:
            sym = "discordant"
        ax.text(0.985, i, sym, va="center", ha="right", fontsize=8,
                color=fg, style="italic")
    ax.set_yticks([]); ax.set_xticks([]); ax.set_xlim(0, 1)
    ax.set_ylim(-0.6, len(cd) - 0.4)
    ax.invert_yaxis()
    ax.set_title("B. Back-mutation concordance vs reference",
                 fontsize=11, weight="bold")
    from matplotlib.patches import Patch
    leg = [Patch(color=cmap["concordant"], label="Concordant with reference"),
           Patch(color=cmap["discordant"], label="Discordant reversion"),
           Patch(color=cmap["over"], label="Over-correction (graft already matched)")]
    # legend BELOW the panel, outside the bars
    ax.legend(handles=leg, fontsize=8, frameon=False, loc="upper center",
              bbox_to_anchor=(0.5, -0.04), ncol=1)
    fig.suptitle("Blind-design validation against held-out reference",
                 fontsize=12.5, weight="bold", y=1.00)
    fig.tight_layout(rect=[0, 0.02, 1, 0.98])
    return _savefig(fig, "fig5_benchmark", outdir)


# ---------------------------------------------------------------------------
def make_all(master, dev_summ, fv_immuno, immuno_status, humanness,
             outdir, order=None, ref_key=None, benchmark=None):
    """Render all figures. Returns {figure_name: [paths]}."""
    if order is None:
        order = list(master["construct"])
    # keep only constructs actually present
    order = [c for c in order if c in set(master["construct"])]
    colors = _colors_for(order, ref_key)

    out = {}
    out["fig1_developability"] = fig_developability(dev_summ, order, colors, outdir)
    out["fig2_immunogenicity"] = fig_immunogenicity(fv_immuno, immuno_status,
                                                    order, colors, outdir)
    t = fig_tradeoff(master, immuno_status, order, colors, outdir, ref_key)
    if t:
        out["fig3_tradeoff"] = t
    out["fig4_scorecard"] = fig_scorecard(master, immuno_status, order, outdir)
    a = fig_aggregation(dev_summ, order, colors, outdir)
    if a:
        out["fig6_aggregation"] = a
    b = fig_benchmark(benchmark, outdir)
    if b:
        out["fig5_benchmark"] = b
    return out


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="Render assessment figures from a reassess() JSON dump")
    ap.add_argument("--master-csv", required=True)
    ap.add_argument("--dev-csv", required=True)
    ap.add_argument("--immuno-csv", default=None)
    ap.add_argument("--humanness-csv", required=True)
    ap.add_argument("--immuno-status", default="ok")
    ap.add_argument("--outdir", default="/mnt/results/figures")
    ap.add_argument("--ref-key", default=None)
    a = ap.parse_args()
    master = pd.read_csv(a.master_csv)
    dev = pd.read_csv(a.dev_csv)
    hum = pd.read_csv(a.humanness_csv)
    fv = pd.read_csv(a.immuno_csv) if a.immuno_csv else pd.DataFrame()
    res = make_all(master, dev, fv, a.immuno_status, hum, a.outdir,
                   ref_key=a.ref_key)
    for k, v in res.items():
        print(f"  {k}: {v}")
