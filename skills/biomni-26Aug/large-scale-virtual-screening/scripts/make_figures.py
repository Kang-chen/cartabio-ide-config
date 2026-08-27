#!/usr/bin/env python3
"""
make_figures.py -- publication-style DATA plots for the screen (PNG + SVG).

DATA plots only (matplotlib/seaborn). The conceptual WORKFLOW INFOGRAPHIC is made
separately by the agent with GenerateImage (per platform guidance) -- not here.

Figures produced (skipped gracefully if inputs are absent):
  fig1_roc_curve            (labeled) ROC from roc_curve.csv + AUC annotation
  fig2_score_distribution   affinity histogram; actives vs decoys if labeled
  fig3_enrichment_factors   (labeled) EF@1/5/10% bar + BEDROC/AUC callout
  fig4_top_structures       grid of top-N hit 2D structures (RDKit)
  fig5_property_vs_affinity  scatter of the strongest property-affinity trend
  fig6_scaffold_clusters    cluster-size bar for the top Butina clusters

Colorblind-friendly palette; Liberation Sans; editable SVG text.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import sys

try:
    from common import label_from_row as _label_of
except Exception:  # standalone fallback if common.py is not importable
    def _label_of(r):
        lab = r.get("activity_label", "")
        if lab not in ("", None):
            try:
                return int(float(lab))
            except (ValueError, TypeError):
                pass
        src = (r.get("source") or "").strip().lower()
        if src in ("active", "actives"):
            return 1
        if src in ("decoy", "decoys", "inactive", "inactives"):
            return 0
        return None


def _setup_mpl():
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
    matplotlib.rcParams["svg.fonttype"] = "none"
    matplotlib.rcParams["figure.dpi"] = 150
    matplotlib.rcParams["axes.spines.top"] = False
    matplotlib.rcParams["axes.spines.right"] = False


# colorblind-safe (Okabe-Ito-ish) + Phylo gold accent
C_ACT = "#0279EE"   # blue - actives
C_DEC = "#8A8378"   # gray - decoys
C_BAR = "#D4A04A"   # gold - bars
C_PT = "#75A025"    # green - points/line


def _save(fig, outdir, name):
    import matplotlib.pyplot as plt
    png = os.path.join(outdir, f"{name}.png")
    svg = os.path.join(outdir, f"{name}.svg")
    fig.savefig(png, bbox_inches="tight")
    try:
        fig.savefig(svg, bbox_inches="tight")
    except Exception:
        svg = None
    plt.close(fig)
    return png, svg


def _read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def fig_roc(outdir, run):
    p = os.path.join(run, "roc_curve.csv")
    mp = os.path.join(run, "enrichment_metrics.json")
    if not os.path.exists(p):
        return None
    import matplotlib.pyplot as plt
    rows = _read_csv(p)
    fpr = [float(r["fpr"]) for r in rows]; tpr = [float(r["tpr"]) for r in rows]
    auc = None
    if os.path.exists(mp):
        auc = json.load(open(mp)).get("roc_auc")
    fig, ax = plt.subplots(figsize=(4.2, 4.2))
    ax.plot(fpr, tpr, color=C_PT, lw=2, label=(f"AUC = {auc}" if auc is not None else "ROC"))
    ax.plot([0, 1], [0, 1], "--", color=C_DEC, lw=1)
    ax.set_xlabel("False positive rate"); ax.set_ylabel("True positive rate")
    ax.set_title("ROC -- actives vs decoys"); ax.legend(loc="lower right", frameon=False)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    return _save(fig, outdir, "fig1_roc_curve")


def fig_scores(outdir, run):
    p = os.path.join(run, "all_scores_merged.csv")
    if not os.path.exists(p):
        return None
    import matplotlib.pyplot as plt
    import numpy as np
    rows = [r for r in _read_csv(p) if r.get("status") == "ok" and r.get("vina_affinity")]
    aff = np.array([float(r["vina_affinity"]) for r in rows])
    labs = [_label_of(r) for r in rows]
    labeled = any(l in (0, 1) for l in labs)
    fig, ax = plt.subplots(figsize=(5, 3.8))
    if labeled:
        a = aff[[l == 1 for l in labs]]
        d = aff[[l == 0 for l in labs]]
        bins = np.linspace(min(aff), max(aff), 40)
        ax.hist(d, bins=bins, density=True, alpha=0.6, color=C_DEC,
                label=f"decoys (med {np.median(d):.2f})")
        ax.hist(a, bins=bins, density=True, alpha=0.7, color=C_ACT,
                label=f"actives (med {np.median(a):.2f})")
        # More negative (stronger) scores sit on the LEFT; put legend upper-left
        # where the actives peak has the least density overlap with text.
        ax.legend(frameon=False, loc="upper left", fontsize=8)
    else:
        ax.hist(aff, bins=40, color=C_BAR, alpha=0.85)
    ax.set_xlabel("Vina affinity (kcal/mol)"); ax.set_ylabel("Density" if labeled else "Count")
    ax.set_title("Docking score distribution", pad=8)
    return _save(fig, outdir, "fig2_score_distribution")


def fig_ef(outdir, run):
    mp = os.path.join(run, "enrichment_metrics.json")
    if not os.path.exists(mp):
        return None
    import matplotlib.pyplot as plt
    m = json.load(open(mp))
    efs = {k: v for k, v in m.items() if k.startswith("EF_") and v is not None}
    if not efs:
        return None
    fig, ax = plt.subplots(figsize=(4.6, 3.8))
    labels = list(efs.keys()); vals = [efs[k] for k in labels]
    bars = ax.bar(labels, vals, color=C_BAR, zorder=2)
    ax.axhline(1.0, ls="--", color=C_DEC, lw=1, label="random (EF=1)", zorder=1)
    ax.set_ylabel("Enrichment factor")
    # Value labels above each bar (small, unambiguous).
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:.2f}", (b.get_x() + b.get_width() / 2, v),
                    textcoords="offset points", xytext=(0, 2), ha="center",
                    va="bottom", fontsize=7.5, color="#2C2A26")
    # Headroom so labels + legend + summary line never collide with bars.
    ax.set_ylim(0, max(vals + [1.0]) * 1.35)
    bed = m.get("bedroc_alpha20")
    # Global metrics live in the (padded) title block, not floating over the axes.
    ax.set_title("Early enrichment\nROC-AUC {}   BEDROC(20) {}".format(m.get("roc_auc"), bed),
                 fontsize=10.5, pad=8)
    ax.legend(frameon=False, loc="upper right", fontsize=8)
    return _save(fig, outdir, "fig3_enrichment_factors")


def fig_structures(outdir, run, top_n):
    p = os.path.join(run, "tables", "top_hits.csv")
    if not os.path.exists(p):
        return None
    from rdkit import Chem
    from rdkit.Chem import Draw
    rows = _read_csv(p)[:top_n]
    mols, legends = [], []
    for r in rows:
        m = Chem.MolFromSmiles(r.get("smiles", ""))
        if m is None:
            continue
        mols.append(m)
        tag = {1: " [active]", 0: " [decoy]"}.get(_label_of(r), "")
        legends.append(f"{r['mol_id']} {r['vina_affinity']}{tag}")
    if not mols:
        return None
    img = Draw.MolsToGridImage(mols, molsPerRow=4, subImgSize=(230, 180), legends=legends)
    png = os.path.join(outdir, "fig4_top_structures.png")
    img.save(png)
    return png, None


def fig_property(outdir, run):
    cp = os.path.join(run, "property_score_correlations.json")
    dp = os.path.join(run, "molecular_descriptors.csv")
    if not (os.path.exists(cp) and os.path.exists(dp)):
        return None
    import matplotlib.pyplot as plt
    corr = json.load(open(cp)).get("correlations", {})
    if not corr:
        return None
    # strongest |rho|
    prop = max(corr, key=lambda k: abs(corr[k]["rho"]))
    rows = [r for r in _read_csv(dp) if r.get(prop) not in ("", None) and r.get("vina_affinity")]
    xs, ys = [], []
    for r in rows:
        try:
            xs.append(float(r[prop])); ys.append(float(r["vina_affinity"]))
        except ValueError:
            continue
    fig, ax = plt.subplots(figsize=(4.4, 3.6))
    ax.scatter(xs, ys, s=10, alpha=0.5, color=C_PT, edgecolors="none")
    ax.set_xlabel(prop); ax.set_ylabel("Vina affinity (kcal/mol)")
    rho = corr[prop]["rho"]
    ax.set_title(f"{prop} vs affinity (Spearman rho = {rho})")
    return _save(fig, outdir, "fig5_property_vs_affinity")


def fig_clusters(outdir, run):
    p = os.path.join(run, "tables", "scaffold_clusters.csv")
    if not os.path.exists(p):
        return None
    import matplotlib.pyplot as plt
    rows = _read_csv(p)
    sizes = sorted([int(r["size"]) for r in rows], reverse=True)[:15]
    if not sizes:
        return None
    fig, ax = plt.subplots(figsize=(5, 3.4))
    ax.bar(range(1, len(sizes) + 1), sizes, color=C_BAR)
    ax.set_xlabel("Cluster (rank by size)"); ax.set_ylabel("Members")
    ax.set_title(f"Top scaffold clusters (of {len(rows)})")
    return _save(fig, outdir, "fig6_scaffold_clusters")


def run(args) -> int:
    _setup_mpl()
    os.makedirs(args.outdir, exist_ok=True)
    made = []
    for fn, label in [
        (lambda: fig_roc(args.outdir, args.run), "fig1_roc"),
        (lambda: fig_scores(args.outdir, args.run), "fig2_scores"),
        (lambda: fig_ef(args.outdir, args.run), "fig3_ef"),
        (lambda: fig_structures(args.outdir, args.run, args.top_n), "fig4_structures"),
        (lambda: fig_property(args.outdir, args.run), "fig5_property"),
        (lambda: fig_clusters(args.outdir, args.run), "fig6_clusters"),
    ]:
        try:
            res = fn()
            if res:
                made.append(res[0]); print(f"[OK] {label} -> {res[0]}")
            else:
                print(f"[skip] {label} (inputs absent)")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] {label} failed: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"[OK] {len(made)} figures in {args.outdir}")
    return 0 if made else 1


def self_check() -> int:
    ok = True
    try:
        _setup_mpl()
        import matplotlib.pyplot as plt  # noqa: F401
        from rdkit.Chem import Draw  # noqa: F401
    except Exception as e:
        print(f"[FAIL] plotting imports: {e}", file=sys.stderr); ok = False
    print(f"make_figures.py self-check: {'PASS' if ok else 'FAIL'} (mpl+rdkit draw import ok)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Generate data-plot figures for the screen")
    ap.add_argument("--run", help="run dir containing metrics/csvs", default="/mnt/results/sbvs_run")
    ap.add_argument("--outdir", default="/mnt/results/sbvs_run/figures")
    ap.add_argument("--top-n", type=int, default=12)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    sys.exit(run(args))


if __name__ == "__main__":
    main()
