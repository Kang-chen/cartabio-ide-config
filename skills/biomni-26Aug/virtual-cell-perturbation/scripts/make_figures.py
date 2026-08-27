#!/usr/bin/env python
"""
Generate benchmark figures (F1-F6) from the metrics pickle + prediction arrays.
Generalized over dataset and model label. Colorblind-friendly Phylo palette; SVG
text stays editable. Run a media-output check on each PNG after this (see SKILL.md).

Figures:
  F1  dataset + split overview (cells/genes/conditions; split sizes; test regimes)
  F2  provenance (training curve IF --curve state.json given; else provenance table)
  F3  model vs baseline summary bars (higher-is-better + lower-is-better panels)
  F4  per-perturbation metric distributions (pearson_delta, pearson_delta_de, dir20)
  F5  performance by held-out regime (boxplot+strip + grouped bars)
  F6  example delta scatters (best / named / median / worst perturbations)

All figures are written as PNG (150 dpi) + SVG to --figdir.
"""
import argparse, json, os, pickle
from pathlib import Path
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["axes.spines.top"] = False
matplotlib.rcParams["axes.spines.right"] = False

COL_MODEL = "#0279EE"; COL_BASE = "#FF9400"; COL_ACCENT = "#75A025"
COL_PINK = "#FD9BED"; COL_DARK = "#222222"
REGIME_COLORS = {"combo_seen1": "#0279EE", "combo_seen2": "#75A025",
                 "unseen_single": "#FF9400", "combo_seen0": "#C0392B",
                 "single": "#8E44AD", "unknown": "#888888"}


def save(fig, figdir, name):
    for ext in ("png", "svg"):
        fig.savefig(f"{figdir}/{name}.{ext}", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[fig] {name}.png / .svg", flush=True)


def fig1_overview(summary, figdir, dataset):
    sizes = None
    prov = summary.get("split_sizes")
    counts = summary.get("regime_counts", {})
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8))
    # panel A: dataset scale
    a = ax[0]
    labels = ["cells", "genes", "conditions"]
    vals = [summary.get("n_test_cells_total", summary.get("n_cells", np.nan)),
            summary.get("n_genes", np.nan), summary.get("n_conditions", np.nan)]
    # fall back to what we have
    vals = [summary.get("n_cells", summary.get("n_test_cells", np.nan)),
            summary.get("n_genes", np.nan), summary.get("n_conditions", np.nan)]
    a.bar(labels, vals, color=[COL_MODEL, COL_ACCENT, COL_BASE])
    for i, v in enumerate(vals):
        if v == v:
            a.text(i, v, f"{int(v):,}", ha="center", va="bottom", fontsize=9)
    a.set_title(f"{dataset}: dataset scale", fontsize=11)
    a.set_ylabel("count")
    # panel B: split sizes (conditions)
    b = ax[1]
    ss = summary.get("split_sizes", {})
    order = [k for k in ("train", "val", "test") if k in ss]
    if order:
        b.bar(order, [ss[k] for k in order], color=[COL_MODEL, COL_ACCENT, COL_BASE])
        for i, k in enumerate(order):
            b.text(i, ss[k], str(ss[k]), ha="center", va="bottom", fontsize=9)
        b.set_ylabel("conditions")
    else:
        # split_sizes not in metrics (e.g. older artifact) -> annotate instead of blank axis
        b.text(0.5, 0.5, "split sizes\nnot recorded", ha="center", va="center",
               fontsize=10, color="#888", transform=b.transAxes)
        b.set_xticks([]); b.set_yticks([])
    b.set_title("Split (conditions)", fontsize=11)
    # panel C: test regime composition
    c = ax[2]
    ks = list(counts.keys()); vs = [counts[k] for k in ks]
    c.bar(range(len(ks)), vs, color=[REGIME_COLORS.get(k, "#888") for k in ks])
    c.set_xticks(range(len(ks))); c.set_xticklabels(ks, rotation=30, ha="right", fontsize=8)
    for i, v in enumerate(vs):
        c.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    c.set_title("Held-out test regimes", fontsize=11); c.set_ylabel("conditions")
    fig.tight_layout()
    save(fig, figdir, "F1_dataset_overview")


def fig2_provenance(summary, figdir, curve_path, model_label):
    fig, ax = plt.subplots(1, 2 if curve_path else 1,
                           figsize=(11, 3.8) if curve_path else (6, 3.8))
    axes = np.atleast_1d(ax)
    if curve_path and os.path.exists(curve_path):
        st = json.load(open(curve_path))
        h = st["history"]
        ep = [r["epoch"] for r in h]
        tr = [r["train_loss"] for r in h]; va = [r["val_loss"] for r in h]
        a = axes[0]
        a.plot(ep, tr, "-o", color=COL_MODEL, label="train MSE", ms=4)
        a.plot(ep, va, "-o", color=COL_BASE, label="val MSE", ms=4)
        best_ep = int(np.argmin(va)); a.axvline(ep[best_ep], ls="--", color=COL_ACCENT, lw=1)
        a.text(ep[best_ep], max(va), f"best ep {ep[best_ep]}", color=COL_ACCENT,
               fontsize=8, ha="center", va="bottom")
        a.set_xlabel("epoch"); a.set_ylabel("masked MSE")
        a.set_title(f"Own fine-tune curve ({model_label})", fontsize=11); a.legend(frameon=False, fontsize=8)
        tbl_ax = axes[1]
    else:
        tbl_ax = axes[0]
    # provenance / headline table
    tbl_ax.axis("off")
    cm = summary.get("overall_model_compute_metrics", {})
    om = summary.get("overall_model", {})
    rows = [
        ["dataset", str(summary.get("dataset", "?"))],
        ["split / seed", f"{summary.get('split','?')} / {summary.get('seed','?')}"],
        ["test perts / cells", f"{summary.get('n_test_perts','?')} / {summary.get('n_test_cells','?')}"],
        ["genes", str(summary.get("n_genes", "?"))],
        ["pearson (all genes)", f"{cm.get('pearson', float('nan')):.4f}"],
        ["pearson_de", f"{cm.get('pearson_de', float('nan')):.4f}"],
        ["pearson_delta (mean)", f"{om.get('pearson_delta_mean', float('nan')):.4f}"],
        ["pearson_delta_de (mean)", f"{om.get('pearson_delta_de_mean', float('nan')):.4f}"],
    ]
    t = tbl_ax.table(cellText=rows, colLabels=["field", "value"], loc="center", cellLoc="left")
    t.auto_set_font_size(False); t.set_fontsize(9); t.scale(1, 1.4)
    for (r, cc), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#D4A04A"); cell.set_text_props(color="white", weight="bold")
    tbl_ax.set_title("Provenance & headline metrics", fontsize=11)
    fig.tight_layout()
    save(fig, figdir, "F2_provenance")


def fig3_summary(summary, figdir, model_label):
    om = summary.get("overall_model", {}); ob = summary.get("overall_base", {})
    cm = summary.get("overall_model_compute_metrics", {})
    cb = summary.get("overall_base_compute_metrics", {})
    has_base = bool(ob)
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
    # higher is better
    hi = [("pearson\n(all)", cm.get("pearson"), cb.get("pearson")),
          ("pearson_de", cm.get("pearson_de"), cb.get("pearson_de")),
          ("pearson_\ndelta", om.get("pearson_delta_mean"), ob.get("pearson_delta_mean")),
          ("pearson_\ndelta_de", om.get("pearson_delta_de_mean"), ob.get("pearson_delta_de_mean")),
          ("dir_top20", om.get("frac_correct_direction_20_mean"), ob.get("frac_correct_direction_20_mean"))]
    labels = [x[0] for x in hi]; mv = [x[1] for x in hi]; bv = [x[2] for x in hi]
    x = np.arange(len(labels)); w = 0.38
    ax[0].bar(x - w/2, mv, w, label=model_label, color=COL_MODEL)
    if has_base:
        ax[0].bar(x + w/2, bv, w, label="control-mean baseline", color=COL_BASE)
    ax[0].set_xticks(x); ax[0].set_xticklabels(labels, fontsize=8)
    ax[0].set_ylabel("score (higher better)"); ax[0].set_title("Correlation & direction", fontsize=11)
    ax[0].legend(frameon=False, fontsize=8)
    # lower is better
    lo = [("mse\n(all)", cm.get("mse"), cb.get("mse")),
          ("mse_de", cm.get("mse_de"), cb.get("mse_de"))]
    labels2 = [x[0] for x in lo]; mv2 = [x[1] for x in lo]; bv2 = [x[2] for x in lo]
    x2 = np.arange(len(labels2))
    ax[1].bar(x2 - w/2, mv2, w, label=model_label, color=COL_MODEL)
    if has_base:
        ax[1].bar(x2 + w/2, bv2, w, label="control-mean baseline", color=COL_BASE)
    ax[1].set_xticks(x2); ax[1].set_xticklabels(labels2, fontsize=9)
    ax[1].set_ylabel("MSE (lower better)"); ax[1].set_title("Reconstruction error", fontsize=11)
    ax[1].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    save(fig, figdir, "F3_benchmark_summary")


def fig4_distributions(full, figdir, model_label):
    rows = full["rows_model"]
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8))
    for a, key, title in zip(
            ax,
            ["pearson_delta", "pearson_delta_de", "frac_correct_direction_20"],
            ["Pearson delta", "Pearson delta (DE)", "Direction match (top-20 DE)"]):
        vals = np.array([r[key] for r in rows if r[key] == r[key]])
        a.hist(vals, bins=20, color=COL_MODEL, alpha=0.85, edgecolor="white")
        a.axvline(np.median(vals), color=COL_BASE, ls="--", lw=1.5,
                  label=f"median {np.median(vals):.3f}")
        a.set_title(title, fontsize=11); a.set_xlabel(key); a.set_ylabel("# perturbations")
        a.legend(frameon=False, fontsize=8)
    fig.suptitle(f"{model_label}: per-perturbation metric distributions", fontsize=12, y=1.03)
    fig.tight_layout()
    save(fig, figdir, "F4_metric_distributions")


def fig5_by_regime(full, figdir, model_label):
    rows = full["rows_model"]
    regimes = [r for r in ["combo_seen0", "combo_seen1", "combo_seen2", "unseen_single", "single"]
               if any(x["regime"] == r for x in rows)]
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
    # boxplot + strip of pearson_delta by regime
    data = [[x["pearson_delta"] for x in rows if x["regime"] == r and x["pearson_delta"] == x["pearson_delta"]]
            for r in regimes]
    # matplotlib >=3.9 renamed boxplot's `labels` kwarg to `tick_labels`.
    try:
        bp = ax[0].boxplot(data, tick_labels=regimes, patch_artist=True, widths=0.6, showfliers=False)
    except TypeError:
        bp = ax[0].boxplot(data, labels=regimes, patch_artist=True, widths=0.6, showfliers=False)
    for patch, r in zip(bp["boxes"], regimes):
        patch.set_facecolor(REGIME_COLORS.get(r, "#888")); patch.set_alpha(0.35)
    for i, (r, d) in enumerate(zip(regimes, data)):
        jitter = np.random.default_rng(0).normal(0, 0.06, len(d))
        ax[0].scatter(np.full(len(d), i + 1) + jitter, d, s=14,
                      color=REGIME_COLORS.get(r, "#888"), alpha=0.7, edgecolor="white", lw=0.4)
    ax[0].set_xticklabels(regimes, rotation=25, ha="right", fontsize=8)
    ax[0].set_ylabel("pearson_delta"); ax[0].set_title("Pearson delta by regime", fontsize=11)
    ax[0].axhline(0, color="#aaa", lw=0.8, ls=":")
    # grouped bars: mean pearson_delta / pearson_delta_de / dir20 per regime
    br = summary_by_regime(rows, regimes)
    metrics = ["pearson_delta", "pearson_delta_de", "frac_correct_direction_20"]
    x = np.arange(len(regimes)); w = 0.26
    for j, (mkey, col) in enumerate(zip(metrics, [COL_MODEL, COL_ACCENT, COL_PINK])):
        ax[1].bar(x + (j - 1) * w, [br[r][mkey] for r in regimes], w, label=mkey, color=col)
    ax[1].set_xticks(x); ax[1].set_xticklabels(regimes, rotation=25, ha="right", fontsize=8)
    ax[1].set_ylabel("mean score"); ax[1].set_title("Mean metrics by regime", fontsize=11)
    ax[1].legend(frameon=False, fontsize=7)
    fig.tight_layout()
    save(fig, figdir, "F5_by_regime")


def summary_by_regime(rows, regimes):
    out = {}
    for r in regimes:
        sub = [x for x in rows if x["regime"] == r]
        out[r] = {}
        for k in ["pearson_delta", "pearson_delta_de", "frac_correct_direction_20"]:
            vals = [x[k] for x in sub if x[k] == x[k]]
            out[r][k] = float(np.mean(vals)) if vals else np.nan
    return out


def fig6_examples(full, preds_dir, figdir, model_label, named=None):
    pred = np.load(f"{preds_dir}/test_pred.npy")
    truth = np.load(f"{preds_dir}/test_truth.npy")
    pert_cat = np.load(f"{preds_dir}/test_pertcat.npy", allow_pickle=True)
    ctrl_mean = np.load(f"{preds_dir}/ctrl_mean.npy")
    rows = {r["pert"]: r for r in full["rows_model"]}

    # per-pert mean profiles
    uniq = [p for p in np.unique(pert_cat) if p != "ctrl"]
    pm, tm = {}, {}
    for p in uniq:
        m = pert_cat == p
        pm[p] = pred[m].mean(0); tm[p] = truth[m].mean(0)

    scored = [(p, rows.get(p, {}).get("pearson_delta", np.nan)) for p in uniq]
    scored = [(p, s) for p, s in scored if s == s]
    scored.sort(key=lambda x: x[1])
    worst = scored[0][0]; best = scored[-1][0]; med = scored[len(scored) // 2][0]
    # named example: verify present; else fall back to median
    if named and named in pm:
        chosen_named = named
    else:
        if named:
            print(f"[fig6][warn] named example '{named}' not in test set; using median instead", flush=True)
        chosen_named = med
    picks = [("best", best), ("named", chosen_named), ("median", med), ("worst", worst)]

    fig, ax = plt.subplots(2, 2, figsize=(10, 9))
    for a, (tag, p) in zip(ax.ravel(), picks):
        dx = tm[p] - ctrl_mean; dy = pm[p] - ctrl_mean
        r = rows.get(p, {}).get("pearson_delta", np.nan)
        reg = rows.get(p, {}).get("regime", "?")
        a.scatter(dx, dy, s=6, alpha=0.35, color=REGIME_COLORS.get(reg, COL_MODEL), edgecolor="none")
        lim = np.percentile(np.abs(np.concatenate([dx, dy])), 99.5)
        a.plot([-lim, lim], [-lim, lim], ls="--", color="#888", lw=1)
        a.set_xlim(-lim, lim); a.set_ylim(-lim, lim)
        a.set_xlabel("true delta (pert - ctrl)"); a.set_ylabel("predicted delta")
        a.set_title(f"{tag}: {p}\n{reg} | pearson_delta={r:.3f}", fontsize=10)
    fig.suptitle(f"{model_label}: predicted vs true expression change", fontsize=12, y=1.01)
    fig.tight_layout()
    save(fig, figdir, "F6_example_scatters")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl", default="/mnt/results/execution_trace/benchmark_full.pkl")
    ap.add_argument("--metrics", default="/mnt/results/execution_trace/benchmark_metrics.json")
    ap.add_argument("--preds_dir", default="/mnt/results/execution_trace/preds")
    ap.add_argument("--figdir", default="/mnt/results/figures")
    ap.add_argument("--curve", default=None,
                    help="Optional finetune state.json to draw the training curve in F2.")
    ap.add_argument("--dataset", default=None, help="Override dataset label (default: from metrics).")
    ap.add_argument("--model_label", default=None, help="Override model label (default: from metrics).")
    ap.add_argument("--named_example", default=None,
                    help="Perturbation to show in F6 (verified present; else median).")
    ap.add_argument("--only", default=None, help="Comma list to restrict figures, e.g. F3,F4.")
    args = ap.parse_args()

    os.makedirs(args.figdir, exist_ok=True)
    full = pickle.load(open(args.pkl, "rb"))
    summary = json.load(open(args.metrics))
    # Back-compat: older artifacts used *_scgpt keys; alias them to the *_model names.
    for a, b in [("overall_model", "overall_scgpt"),
                 ("overall_model_compute_metrics", "overall_scgpt_compute_metrics"),
                 ("by_regime_model", "by_regime_scgpt")]:
        if a not in summary and b in summary:
            summary[a] = summary[b]
    if "rows_model" not in full and "rows_scgpt" in full:
        full["rows_model"] = full["rows_scgpt"]
    # merge dataset-scale fields (n_cells/n_conditions/split_sizes) if a provenance json exists
    dataset = args.dataset or summary.get("dataset", "dataset")
    model_label = args.model_label or full.get("model_label", summary.get("model_label", "model"))
    prov_path = Path(args.preds_dir).parent / f"split_provenance_{dataset}_seed{summary.get('seed',42)}.json"
    if prov_path.exists():
        prov = json.load(open(prov_path))
        summary.setdefault("n_cells", prov.get("n_cells"))
        summary.setdefault("n_conditions", prov.get("n_conditions"))
        summary.setdefault("split_sizes", prov.get("split_sizes"))

    want = set(args.only.split(",")) if args.only else {"F1", "F2", "F3", "F4", "F5", "F6"}
    if "F1" in want: fig1_overview(summary, args.figdir, dataset)
    if "F2" in want: fig2_provenance(summary, args.figdir, args.curve, model_label)
    if "F3" in want: fig3_summary(summary, args.figdir, model_label)
    if "F4" in want: fig4_distributions(full, args.figdir, model_label)
    if "F5" in want: fig5_by_regime(full, args.figdir, model_label)
    if "F6" in want: fig6_examples(full, args.preds_dir, args.figdir, model_label, args.named_example)
    print("[done] figures ->", args.figdir, flush=True)


if __name__ == "__main__":
    main()
