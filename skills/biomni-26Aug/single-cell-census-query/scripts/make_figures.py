#!/usr/bin/env python3
"""
Step 6 — Figures for the single-cell-census-query skill (Phylo palette, editable SVG text).

Generalized from a validated run. Produces:
  fig_expr_<gene>.(png|svg)      per-gene dotplot: mean expr + % expressing across cell types
  fig_volcano_global.(png|svg)   global pseudobulk DE volcano
  fig_panel_forest.(png|svg)     gene-panel log2FC +/- SE across cell types
  fig_deg_counts.(png|svg)       DEGs per cell type barplot
  fig_pseudobulk_boxplots.(png|svg)  logCPM of panel genes, case vs control (per gene)

MANDATORY after running: Read(<png>, mode="media_output_check") on each figure and regenerate
anything blank/clipped/overlapping. This caught real overlap bugs in the source run.

Edit the PARAMETERS block only.
"""
import os, textwrap
import matplotlib
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np, pandas as pd

# ============================== PARAMETERS ==============================
GENE_PANEL   = ["GENE1", "GENE2"]     # e.g. ["IL13", "TSLP"]
PANEL_TAG    = "panel"                 # must match the tags used upstream
CASE_LABEL   = "case"                  # display label for case group
CONTROL_LABEL = "control"              # display label for control group
TISSUES      = ["tissue_a"]           # tissues present in the atlas CSV
MIN_CELLS_DISPLAY = 200                # min cells/cell-type to display in dotplots
TOP_N_CT     = 15                      # top cell types (by % expressing) per dotplot panel
# =======================================================================

FIG = "/mnt/results/figures"; RES = "/mnt/results/data"
os.makedirs(FIG, exist_ok=True)
GOLD, BLUE, ORANGE, GREEN, GREY = "#D4A04A", "#0279EE", "#FF9400", "#75A025", "#8A8378"


def save(fig, name):
    for ext in ("png", "svg"):
        fig.savefig(f"{FIG}/{name}.{ext}", dpi=150, bbox_inches="tight")
    plt.close(fig)


def wrap(labels, width=34):
    return ["\n".join(textwrap.wrap(str(x), width)) for x in labels]


# ---------- Dotplots: mean expression + % expressing across cell types ----------
def dotplots(expr):
    n_t = len(TISSUES)
    for gene in GENE_PANEL:
        mcol, pcol = f"{gene}_mean", f"{gene}_pct_expr"
        if mcol not in expr.columns:
            print(f"skip {gene}: not in atlas CSV"); continue
        sub = expr[expr.n_cells >= MIN_CELLS_DISPLAY]
        vmax = max(np.percentile(sub[mcol], 99) if len(sub) else 1e-8, 1e-8)
        fig, axes = plt.subplots(n_t, 1, figsize=(9.5, 4.6 * n_t), squeeze=False)
        axes = axes.ravel(); scs = []
        for ax, tissue in zip(axes, TISSUES):
            d = expr[(expr.tissue == tissue) & (expr.n_cells >= MIN_CELLS_DISPLAY)].copy()
            d = d.sort_values(pcol, ascending=False).head(TOP_N_CT).iloc[::-1]
            y = np.arange(len(d)); sizes = d[pcol].values * 16 + 18
            sc = ax.scatter(np.ones(len(d)), y, s=sizes, c=d[mcol].values, cmap="Reds",
                            vmin=0, vmax=vmax, edgecolor="black", linewidth=0.4, zorder=3)
            scs.append(sc)
            ax.set_yticks(y); ax.set_yticklabels(wrap(d["cell_type"].values), fontsize=8)
            ax.set_xticks([]); ax.set_xlim(0.6, 1.4)
            ax.set_title(f"{tissue}", fontsize=11, fontweight="bold")
            ax.grid(axis="y", linestyle=":", alpha=0.4, zorder=0); ax.margins(y=0.08)
        cb = fig.colorbar(scs[0], ax=list(axes), fraction=0.04, pad=0.02)
        cb.set_label("mean normalized expression", fontsize=8); cb.ax.tick_params(labelsize=7)
        handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor="grey",
                          markeredgecolor="black", markersize=np.sqrt(p * 16 + 18), label=f"{p}%")
                   for p in [5, 25, 50]]
        fig.legend(handles=handles, title="% cells expressing", loc="lower center", ncol=3,
                   fontsize=8, title_fontsize=8, bbox_to_anchor=(0.5, -0.03), framealpha=0.9)
        fig.suptitle(f"{gene} expression across cell types\n(normal tissue, CZ CELLxGENE Census; "
                     f"top {TOP_N_CT} by % expressing)", fontsize=11.5, fontweight="bold")
        save(fig, f"fig_expr_{gene}")
    print("dotplots done")


# ---------- Volcano: global pseudobulk DE ----------
def volcano(allr):
    try:
        from adjustText import adjust_text
        have_at = True
    except Exception:
        have_at = False
    g = allr[allr.cell_type == "GLOBAL"].dropna(subset=["padj", "log2FoldChange"]).copy()
    if g.empty:
        print("skip volcano: no GLOBAL results"); return
    g["nlp"] = -np.log10(g["padj"].clip(lower=1e-300))
    up = (g.padj < 0.05) & (g.log2FoldChange > 1); dn = (g.padj < 0.05) & (g.log2FoldChange < -1)
    ns = ~(up | dn)
    fig, ax = plt.subplots(figsize=(8, 6.5))
    ax.scatter(g.log2FoldChange[ns], g.nlp[ns], s=6, c="lightgrey", alpha=0.45)
    ax.scatter(g.log2FoldChange[up], g.nlp[up], s=9, c=ORANGE, alpha=0.7)
    ax.scatter(g.log2FoldChange[dn], g.nlp[dn], s=9, c=BLUE, alpha=0.7)
    lab = pd.concat([g.sort_values("padj").head(10),
                     g[g.gene.isin(GENE_PANEL)]]).drop_duplicates("feature_id")
    texts = []
    for _, r in lab.iterrows():
        fw = "bold" if r["gene"] in GENE_PANEL else "normal"
        texts.append(ax.text(r.log2FoldChange, r.nlp, r["gene"],
                             fontsize=9 if fw == "bold" else 7.5, fontweight=fw))
        if r["gene"] in GENE_PANEL:
            ax.scatter([r.log2FoldChange], [r.nlp], s=45, facecolor="none",
                       edgecolor="red", linewidth=1.4, zorder=5)
    if have_at:
        adjust_text(texts, ax=ax, arrowprops=dict(arrowstyle="-", color="grey", lw=0.5),
                    expand_points=(1.5, 1.8), force_text=(0.4, 0.6))
    ax.axhline(-np.log10(0.05), ls="--", c="grey", lw=0.8)
    ax.axvline(1, ls=":", c="grey", lw=0.6); ax.axvline(-1, ls=":", c="grey", lw=0.6)
    ax.set_xlabel(f"log2 fold change ({CASE_LABEL} / {CONTROL_LABEL})")
    ax.set_ylabel("-log10 adjusted p-value")
    ax.set_title(f"Global pseudobulk DE: {CASE_LABEL} vs {CONTROL_LABEL}", fontweight="bold", fontsize=11)
    handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=ORANGE, markersize=7, label=f"Up in {CASE_LABEL}"),
               Line2D([0], [0], marker="o", color="w", markerfacecolor=BLUE, markersize=7, label=f"Down in {CASE_LABEL}"),
               Line2D([0], [0], marker="o", color="w", markerfacecolor="lightgrey", markersize=7, label="NS")]
    ax.legend(handles=handles, fontsize=8, loc="upper right", framealpha=0.95)
    fig.tight_layout(); save(fig, "fig_volcano_global")
    print("volcano done")


# ---------- Forest: gene-panel log2FC +/- SE across cell types ----------
def forest(tgt):
    genes = [g for g in GENE_PANEL if g in set(tgt.gene)]
    if not genes:
        print("skip forest: panel genes absent"); return
    fig, axes = plt.subplots(1, len(genes), figsize=(5.8 * len(genes), 5.6), squeeze=False)
    for ax, gene in zip(axes.ravel(), genes):
        d = tgt[tgt.gene == gene].dropna(subset=["log2FoldChange"]).sort_values("log2FoldChange")
        y = np.arange(len(d))
        colors = [ORANGE if (pd.notnull(p) and p < 0.05 and lfc > 0)
                  else BLUE if (pd.notnull(p) and p < 0.05 and lfc < 0) else GREY
                  for p, lfc in zip(d.padj, d.log2FoldChange)]
        ax.errorbar(d.log2FoldChange, y, xerr=d.lfcSE, fmt="o", ms=6, capsize=2.5,
                    ecolor="grey", elinewidth=1, mfc="white", mec="grey", zorder=2)
        ax.scatter(d.log2FoldChange, y, s=48, c=colors, zorder=3, edgecolor="black", linewidth=0.4)
        ax.axvline(0, ls="--", c="black", lw=0.8)
        ax.set_yticks(y); ax.set_yticklabels(wrap(d.cell_type.values), fontsize=8)
        ax.set_xlabel(f"log2 fold change ({CASE_LABEL} / {CONTROL_LABEL})")
        ax.set_title(gene, fontweight="bold", fontsize=11)
        for yi, (p, lfc, se) in enumerate(zip(d.padj, d.log2FoldChange, d.lfcSE)):
            if pd.notnull(p) and p < 0.05:
                ax.text(lfc + (se if pd.notnull(se) else 0) + 0.2, yi, "*",
                        fontsize=14, va="center", color="red", fontweight="bold")
    handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=ORANGE, markeredgecolor="black", markersize=8, label="Up, padj<0.05"),
               Line2D([0], [0], marker="o", color="w", markerfacecolor=BLUE, markeredgecolor="black", markersize=8, label="Down, padj<0.05"),
               Line2D([0], [0], marker="o", color="w", markerfacecolor=GREY, markeredgecolor="black", markersize=8, label="Not significant")]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=8.5, bbox_to_anchor=(0.5, -0.03))
    fig.suptitle(f"{PANEL_TAG} panel differential expression across cell types "
                 f"({CASE_LABEL} vs {CONTROL_LABEL})", fontweight="bold", fontsize=11.5)
    fig.tight_layout(); save(fig, "fig_panel_forest")
    print("forest done")


# ---------- DEG-count barplot ----------
def deg_counts(sig):
    if sig.empty:
        print("skip deg_counts: no significant results"); return
    c = sig.groupby("cell_type").size().sort_values(ascending=True)
    fig, ax = plt.subplots(figsize=(8, max(3, 0.32 * len(c) + 1)))
    ax.barh(wrap(c.index, 40), c.values, color=GOLD, edgecolor="black", linewidth=0.4)
    ax.set_xlabel("number of DEGs (padj < 0.05)")
    ax.set_title(f"Differentially expressed genes per cell type\n({CASE_LABEL} vs {CONTROL_LABEL})",
                 fontweight="bold", fontsize=11)
    for i, v in enumerate(c.values):
        ax.text(v, i, f" {v}", va="center", fontsize=7.5)
    fig.tight_layout(); save(fig, "fig_deg_counts")
    print("deg_counts done")


# ---------- Pseudobulk boxplots: logCPM of panel genes, case vs control ----------
def boxplots():
    import os.path as osp
    cf = "/mnt/shared-workspace/shared/pseudobulk_counts.csv"
    df = "/mnt/shared-workspace/shared/pseudobulk_coldata.csv"
    vf = "/mnt/shared-workspace/shared/pseudobulk_var.csv"
    if not all(osp.exists(x) for x in (cf, df, vf)):
        print("skip boxplots: pseudobulk files not found"); return
    counts = pd.read_csv(cf, index_col=0); col = pd.read_csv(df); var = pd.read_csv(vf)
    id2name = dict(zip(var.feature_id, var.feature_name))
    name2id = {v: k for k, v in id2name.items()}
    libsize = counts.sum(axis=0).replace(0, np.nan)
    genes = [g for g in GENE_PANEL if g in name2id and name2id[g] in counts.index]
    if not genes:
        print("skip boxplots: panel genes absent from counts"); return
    col = col.set_index("sample")
    fig, axes = plt.subplots(1, len(genes), figsize=(4.4 * len(genes), 4.8), squeeze=False)
    for ax, gene in zip(axes.ravel(), genes):
        cpm = counts.loc[name2id[gene]] / libsize * 1e6
        logcpm = np.log1p(cpm)
        d = pd.DataFrame({"logcpm": logcpm, "disease": col.loc[logcpm.index, "disease"]})
        groups = [d.logcpm[d.disease == CONTROL_LABEL].dropna(),
                  d.logcpm[d.disease == CASE_LABEL].dropna()]
        bp = ax.boxplot(groups, labels=[CONTROL_LABEL, CASE_LABEL], patch_artist=True, widths=0.6,
                        showfliers=False)
        for patch, c in zip(bp["boxes"], [GREY, ORANGE]):
            patch.set_facecolor(c); patch.set_alpha(0.6)
        for i, gvals in enumerate(groups, start=1):
            ax.scatter(np.random.normal(i, 0.06, len(gvals)), gvals, s=6, c="black", alpha=0.35, zorder=3)
        ax.set_title(gene, fontweight="bold", fontsize=11); ax.set_ylabel("log(CPM+1)")
    fig.suptitle(f"Pseudobulk expression of {PANEL_TAG} panel ({CASE_LABEL} vs {CONTROL_LABEL})",
                 fontweight="bold", fontsize=11.5)
    fig.tight_layout(); save(fig, "fig_pseudobulk_boxplots")
    print("boxplots done")


def main():
    expr_path = f"{RES}/{PANEL_TAG}_expression_by_celltype.csv"
    if os.path.exists(expr_path):
        dotplots(pd.read_csv(expr_path))
    allr_path = f"{RES}/pseudobulk_DE_all_results.csv"
    sig_path = f"{RES}/pseudobulk_DE_significant.csv"
    tgt_path = f"{RES}/{PANEL_TAG}_DE_by_celltype.csv"
    if os.path.exists(allr_path):
        allr = pd.read_csv(allr_path); volcano(allr)
    if os.path.exists(tgt_path):
        forest(pd.read_csv(tgt_path))
    if os.path.exists(sig_path):
        deg_counts(pd.read_csv(sig_path))
    boxplots()
    print("All figures written to", FIG)


if __name__ == "__main__":
    main()
