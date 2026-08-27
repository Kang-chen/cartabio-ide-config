#!/usr/bin/env python3
"""
make_finemap_figures.py -- publication-quality regional fine-mapping figure for ONE trait.

Produces a stacked multi-panel regional plot (LocusZoom-style), saved as BOTH .png and .svg with
editable SVG text:

  Panel A  GWAS -log10(p) vs position, each variant colored by r^2 to the lead (credible-set) variant
  Panel B  SuSiE posterior inclusion probability (PIP), credible-set members highlighted
  Panel C  gene track (optional; from a BED/GTF-lite TSV or a single --gene-window)
  Panel D  ENCODE cCRE track (optional; from annotate_variants.py *_encode_ccre.csv)

Inputs are the direct outputs of run_susie_finemap.R and annotate_variants.py, so the figure always
matches the analysis it illustrates.

Usage:
  python make_finemap_figures.py \
      --pip all_variants_pip.csv \
      --ld ld.tsv --ld-snps ld_snps.txt \
      --credible-set credible_set.csv \
      --out-prefix figures/finemap_regional \
      [--ccre annotation_encode_ccre.csv] \
      [--genes genes.tsv] [--title "T2D  chr1:213.4-214.6 Mb"] \
      [--pad 150000]
"""
import argparse, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# r^2 bins -> LocusZoom colors
R2_BINS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.01]
R2_COLORS = ["#5A5A5A", "#4DA6E0", "#5DC863", "#F4A63E", "#D7191C"]
R2_LABELS = ["<0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", ">0.8"]


def r2_color(r2):
    if r2 is None or (isinstance(r2, float) and np.isnan(r2)):
        return "#BBBBBB"
    for i in range(len(R2_BINS) - 1):
        if R2_BINS[i] <= r2 < R2_BINS[i + 1]:
            return R2_COLORS[i]
    return R2_COLORS[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pip", required=True, help="all_variants_pip.csv from run_susie_finemap.R")
    ap.add_argument("--ld", required=True)
    ap.add_argument("--ld-snps", required=True)
    ap.add_argument("--credible-set", required=True)
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--ccre", default=None, help="*_encode_ccre.csv from annotate_variants.py")
    ap.add_argument("--genes", default=None, help="TSV: gene,start,end[,strand]")
    ap.add_argument("--title", default="Regional fine-mapping")
    ap.add_argument("--pad", type=int, default=150000, help="bp padding around credible set for x-range")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out_prefix)) or ".", exist_ok=True)

    df = pd.read_csv(args.pip)
    cs = pd.read_csv(args.credible_set)
    for req in ("pos", "pval", "pip", "snp"):
        if req not in df.columns:
            raise SystemExit(f"ERROR: --pip file missing '{req}' column.")

    # lead = max-PIP variant (the credible-set anchor)
    lead = df.loc[df["pip"].idxmax()]
    lead_snp = str(lead["snp"])
    lead_pos = int(lead["pos"])

    # r^2 to lead from signed-r LD matrix
    ld_snps = [s.strip() for s in open(args.ld_snps)]
    R = pd.read_csv(args.ld, sep="\t", index_col=0)
    df["r2_lead"] = np.nan
    if lead_snp in R.index:
        r_lead = R.loc[lead_snp]
        df["r2_lead"] = df["snp"].map(lambda s: (r_lead[s] ** 2) if s in r_lead.index else np.nan)
    else:
        print(f"[figures] WARNING: lead {lead_snp} not in LD matrix; r^2 coloring unavailable.")

    # x-range around credible set
    cs_pos = cs["pos"] if "pos" in cs.columns and len(cs) else pd.Series([lead_pos])
    x0 = int(cs_pos.min()) - args.pad
    x1 = int(cs_pos.max()) + args.pad
    reg = df[(df["pos"] >= x0) & (df["pos"] <= x1)].copy()
    reg["mlogp"] = -np.log10(reg["pval"].clip(lower=1e-300))
    cs_snps = set(cs["snp"].astype(str)) if "snp" in cs.columns else set()

    # ---- layout: A + B always; C/D only if data present ----
    has_genes = args.genes is not None and os.path.exists(args.genes)
    has_ccre = args.ccre is not None and os.path.exists(args.ccre)
    ratios = [3.0, 2.2]
    panels = ["A", "B"]
    if has_genes:
        ratios.append(0.9); panels.append("C")
    if has_ccre:
        ratios.append(0.7); panels.append("D")
    fig, axes = plt.subplots(len(panels), 1, figsize=(9, sum(ratios) * 0.95),
                             gridspec_kw={"height_ratios": ratios}, sharex=True)
    if len(panels) == 1:
        axes = [axes]
    ax_map = dict(zip(panels, axes))
    xmb = lambda v: v / 1e6

    # Panel A: GWAS -log10 p colored by r^2
    axA = ax_map["A"]
    order = reg.sort_values("r2_lead", na_position="first")
    axA.scatter(order["pos"].map(xmb), order["mlogp"],
                c=[r2_color(v) for v in order["r2_lead"]], s=22, edgecolor="white", linewidth=0.3, zorder=2)
    # lead marker
    axA.scatter([xmb(lead_pos)], [-np.log10(max(lead["pval"], 1e-300))],
                marker="D", s=70, facecolor="#7B2D8E", edgecolor="black", linewidth=0.6, zorder=4)
    ymax = float(reg["mlogp"].max())
    axA.annotate(lead_snp, xy=(xmb(lead_pos), -np.log10(max(lead["pval"], 1e-300))),
                 xytext=(xmb(lead_pos) + (x1 - x0) * 0.02 / 1e6, ymax * 0.96),
                 fontsize=8.5, fontweight="bold",
                 arrowprops=dict(arrowstyle="->", color="#333333", lw=0.7))
    axA.set_ylim(0, ymax * 1.18)
    axA.set_ylabel(r"$-\log_{10}(P)$", fontsize=10)
    axA.set_title(args.title, fontsize=12, fontweight="bold", loc="left")
    leg = [Line2D([0], [0], marker="o", linestyle="", markerfacecolor=c, markeredgecolor="white",
                  markersize=7, label=l) for c, l in zip(R2_COLORS, R2_LABELS)]
    leg.append(Line2D([0], [0], marker="D", linestyle="", markerfacecolor="#7B2D8E",
                      markeredgecolor="black", markersize=8, label="lead"))
    axA.legend(handles=leg, title=r"$r^2$", loc="upper left", fontsize=7.5, title_fontsize=8,
               framealpha=0.9, ncol=2)

    # Panel B: SuSiE PIP
    axB = ax_map["B"]
    noncs = reg[~reg["snp"].astype(str).isin(cs_snps)]
    incs = reg[reg["snp"].astype(str).isin(cs_snps)]
    axB.scatter(noncs["pos"].map(xmb), noncs["pip"], s=18, color="#9C9C9C",
                edgecolor="white", linewidth=0.3, zorder=2)
    axB.scatter(incs["pos"].map(xmb), incs["pip"], s=60, color="#D7191C",
                edgecolor="black", linewidth=0.5, zorder=4, label="95% credible set")
    axB.set_ylim(-0.03, 1.05)
    axB.set_ylabel("SuSiE PIP", fontsize=10)
    if len(incs):
        axB.legend(loc="upper left", fontsize=8, framealpha=0.9)

    # Panel C: gene track
    if has_genes:
        axC = ax_map["C"]
        g = pd.read_csv(args.genes, sep="\t")
        y = 0
        n_drawn = 0
        for _, row in g.iterrows():
            gs, ge = float(row["start"]), float(row["end"])
            if ge < x0 or gs > x1:
                continue
            # clip the drawn bar to the visible window so partially-overlapping genes still show
            gs_c, ge_c = max(gs, x0), min(ge, x1)
            axC.plot([xmb(gs_c), xmb(ge_c)], [y, y], lw=5, solid_capstyle="butt", color="#4A6FA5")
            axC.text(xmb((gs_c + ge_c) / 2), y + 0.25, str(row.get("gene", "")), ha="center",
                     fontsize=7.5, style="italic")
            y -= 1
            n_drawn += 1
        if n_drawn == 0:
            axC.text(0.5, 0.5, "no annotated genes in window", ha="center", va="center",
                     transform=axC.transAxes, fontsize=8, color="#8A8378", style="italic")
            axC.set_ylim(0, 1)
        else:
            axC.set_ylim(y - 0.5, 0.8)
        axC.set_yticks([])
        axC.set_ylabel("genes", fontsize=9)

    # Panel D: ENCODE cCRE track
    if has_ccre:
        axD = ax_map["D"]
        c = pd.read_csv(args.ccre)
        s_col = "start" if "start" in c.columns else None
        e_col = "end" if "end" in c.columns else None
        cls_col = next((x for x in ("element_class", "class") if x in c.columns), None)
        ov_col = "overlaps_credible_variant" if "overlaps_credible_variant" in c.columns else None
        for _, row in c.iterrows():
            if s_col is None:
                break
            cs_, ce_ = float(row[s_col]), float(row[e_col])
            if ce_ < x0 or cs_ > x1:
                continue
            hit = bool(row[ov_col]) if ov_col else False
            axD.axvspan(xmb(cs_), xmb(ce_), ymin=0.25, ymax=0.75,
                        color="#FF9400" if hit else "#B7B7B7",
                        alpha=0.95 if hit else 0.6, lw=0)
            if hit and cls_col:
                axD.annotate(str(row[cls_col]), xy=(xmb((cs_ + ce_) / 2), 0.8),
                             ha="center", va="bottom", fontsize=7.5, fontweight="bold",
                             color="#B5651D",
                             arrowprops=dict(arrowstyle="->", color="#B5651D", lw=0.6),
                             xytext=(xmb((cs_ + ce_) / 2), 1.15))
        axD.set_ylim(0, 1.4)
        axD.set_yticks([])
        axD.set_ylabel("cCRE", fontsize=9)

    # lead vline across all panels
    for ax in axes:
        ax.axvline(xmb(lead_pos), color="#7B2D8E", lw=0.7, ls="--", alpha=0.5, zorder=1)
        ax.grid(axis="y", alpha=0.15)

    axes[-1].set_xlabel(f"chr{str(reg['varid'].iloc[0]).split(':')[0].replace('chr','') if 'varid' in reg.columns and len(reg) else ''} position (Mb)",
                        fontsize=10)
    axes[-1].ticklabel_format(axis="x", useOffset=False, style="plain")

    fig.tight_layout(h_pad=0.6)
    png, svg = f"{args.out_prefix}.png", f"{args.out_prefix}.svg"
    fig.savefig(png, dpi=200, bbox_inches="tight")
    fig.savefig(svg, bbox_inches="tight")
    plt.close(fig)
    print(f"[figures] wrote {png} and {svg}")
    print(f"[figures] panels: {'+'.join(panels)}  lead={lead_snp}  region=chr?:{x0:,}-{x1:,}  variants={len(reg)}")


if __name__ == "__main__":
    main()
