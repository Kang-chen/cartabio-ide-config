#!/usr/bin/env python
"""
Compute GEARS perturbation metrics from saved prediction arrays, for the target
model AND (for free) the control-mean baseline. Generalized over --dataset.

Reconstructs the test_res dict (pred, truth, pred_de, truth_de, pert_cat) exactly
as scGPT's eval_perturb would, then calls the REAL gears.inference.compute_metrics
and deeper_analysis so numbers follow the scGPT / GEARS paper conventions.

Writes a JSON summary (safe on S3) + a pickle with full per-pert tables for figures.

METRIC NUANCE (documented, do not "fix"): the control-mean baseline predicts zero
delta, so its delta-correlations (pearson_delta, pearson_delta_de) AND top-k DE
direction (frac_correct_direction_{20,50,100,200}) are exactly 0 by construction.
BUT frac_correct_direction_all is ~0.25-0.30 and NONZERO: it counts sign agreement
over ALL genes, where the sign of a near-zero predicted delta coincidentally matches
the sign of a near-zero true delta among the many unchanged genes. Always compare
models on top-k DE direction, never on all-gene direction.
"""
import argparse, json, os, warnings, pickle, shutil
from pathlib import Path
import numpy as np

warnings.filterwarnings("ignore")
import logging
logging.getLogger("scgpt").setLevel(logging.ERROR)

from gears import PertData
from gears.inference import compute_metrics, deeper_analysis

DEEP_KEYS = ["pearson_delta", "pearson_delta_de",
             "frac_correct_direction_20", "frac_correct_direction_50",
             "frac_correct_direction_100", "frac_correct_direction_200",
             "frac_correct_direction_all", "mse_top20_de", "pearson_top20_de"]
CM_KEYS = ["mse", "pearson", "mse_de", "pearson_de"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="norman")
    ap.add_argument("--split", default="simulation")
    ap.add_argument("--split_seed", type=int, default=42)
    ap.add_argument("--data_dir", default="/workspace/data")
    ap.add_argument("--preds_dir", default="/mnt/results/execution_trace/preds")
    ap.add_argument("--out", default="/mnt/results/execution_trace/benchmark_metrics.json")
    ap.add_argument("--pkl", default="/mnt/results/execution_trace/benchmark_full.pkl")
    ap.add_argument("--model_label", default=None,
                    help="Name for the predicted model in outputs (default: meta['model'] or 'model').")
    ap.add_argument("--no_baseline", action="store_true",
                    help="Skip the control-mean baseline comparison.")
    args = ap.parse_args()

    P = args.preds_dir
    pred = np.load(f"{P}/test_pred.npy")
    truth = np.load(f"{P}/test_truth.npy")
    pert_cat = np.load(f"{P}/test_pertcat.npy", allow_pickle=True)
    de_idx = np.load(f"{P}/test_de_idx.npy")
    ctrl_mean = np.load(f"{P}/ctrl_mean.npy")
    meta = json.load(open(f"{P}/meta.json"))
    genes = meta["genes"]; n_genes = meta["n_genes"]
    model_label = args.model_label or meta.get("model", "model")
    print(f"[load] {model_label}: pred {pred.shape} truth {truth.shape} "
          f"perts {len(np.unique(pert_cat))} de_idx {de_idx.shape}", flush=True)

    pdta = PertData(args.data_dir)
    pdta.load(data_name=args.dataset)
    pdta.prepare_split(split=args.split, seed=args.split_seed)
    adata = pdta.adata

    def build_res(pred_arr, truth_arr):
        pred_de = np.take_along_axis(pred_arr, de_idx, axis=1)
        truth_de = np.take_along_axis(truth_arr, de_idx, axis=1)
        return {"pert_cat": pert_cat,
                "pred": pred_arr.astype(float), "truth": truth_arr.astype(float),
                "pred_de": pred_de.astype(float), "truth_de": truth_de.astype(float)}

    res_model = build_res(pred, truth)
    print(f"[metrics] compute_metrics {model_label}...", flush=True)
    m_model, mp_model = compute_metrics(res_model)
    print(f"[metrics] deeper_analysis {model_label}...", flush=True)
    d_model = deeper_analysis(adata, res_model)

    do_base = not args.no_baseline
    if do_base:
        pred_base = np.broadcast_to(ctrl_mean.astype(float), pred.shape).copy()
        res_base = build_res(pred_base, truth)
        print("[metrics] compute_metrics baseline...", flush=True)
        m_base, mp_base = compute_metrics(res_base)
        print("[metrics] deeper_analysis baseline...", flush=True)
        d_base = deeper_analysis(adata, res_base)

    subgroup = pdta.subgroup
    pert2regime = {}
    for regime, perts in subgroup.get("test_subgroup", {}).items():
        for p in perts:
            pert2regime[p] = regime

    def collect(mp, dp):
        rows = []
        for pert in np.unique(pert_cat):
            if pert == "ctrl":
                continue
            row = {"pert": str(pert), "regime": pert2regime.get(pert, "unknown")}
            for k in CM_KEYS:
                row[k] = float(mp.get(pert, {}).get(k, np.nan))
            for k in DEEP_KEYS:
                row[k] = float(dp.get(pert, {}).get(k, np.nan))
            rows.append(row)
        return rows

    def overall(rows):
        out = {}
        for k in CM_KEYS + DEEP_KEYS:
            vals = [r[k] for r in rows if not np.isnan(r[k])]
            out[k + "_mean"] = float(np.mean(vals)) if vals else float("nan")
            out[k + "_median"] = float(np.median(vals)) if vals else float("nan")
        return out

    def by_regime(rows):
        out = {}
        for reg in sorted(set(r["regime"] for r in rows)):
            sub = [r for r in rows if r["regime"] == reg]
            out[reg] = {"n": len(sub)}
            for k in ["pearson_delta", "pearson_delta_de", "frac_correct_direction_20",
                      "pearson_de", "mse_de"]:
                vals = [r[k] for r in sub if not np.isnan(r[k])]
                out[reg][k + "_mean"] = float(np.mean(vals)) if vals else float("nan")
        return out

    rows_model = collect(mp_model, d_model)
    summary = {
        "dataset": args.dataset, "split": args.split, "seed": args.split_seed,
        "model_label": model_label,
        "n_test_perts": int(len([p for p in np.unique(pert_cat) if p != "ctrl"])),
        "n_test_cells": int(pred.shape[0]), "n_genes": n_genes,
        "vocab_match": meta.get("vocab_match"),
        "overall_model_compute_metrics": {k: float(v) for k, v in m_model.items()},
        "overall_model": overall(rows_model),
        "by_regime_model": by_regime(rows_model),
        "regime_counts": {k: len(v) for k, v in subgroup.get("test_subgroup", {}).items()},
    }
    full = {"summary": summary, "rows_model": rows_model,
            "metrics_pert_model_cm": mp_model, "deeper_model": d_model,
            "pert2regime": pert2regime, "model_label": model_label}

    if do_base:
        rows_base = collect(mp_base, d_base)
        summary["overall_base_compute_metrics"] = {k: float(v) for k, v in m_base.items()}
        summary["overall_base"] = overall(rows_base)
        summary["by_regime_base"] = by_regime(rows_base)
        full["rows_base"] = rows_base
        full["metrics_pert_base_cm"] = mp_base
        full["deeper_base"] = d_base

    # keep legacy key names too, so downstream figure code that expects *_scgpt works
    summary["overall_scgpt"] = summary["overall_model"]
    summary["overall_scgpt_compute_metrics"] = summary["overall_model_compute_metrics"]
    summary["by_regime_scgpt"] = summary["by_regime_model"]
    full["rows_scgpt"] = full["rows_model"]
    full["deeper_scgpt"] = full["deeper_model"]
    full["metrics_pert_scgpt_cm"] = full["metrics_pert_model_cm"]

    json.dump(summary, open(args.out, "w"), indent=2)
    print(f"[save] summary -> {args.out}", flush=True)
    local_pkl = "/workspace/benchmark_full.pkl"
    with open(local_pkl, "wb") as f:
        pickle.dump(full, f)
    shutil.copy(local_pkl, args.pkl)
    print(f"[save] full -> {args.pkl}", flush=True)

    print(f"\n=== {model_label}" + (" vs control-mean baseline" if do_base else "") +
          " (mean over test perts) ===", flush=True)
    hdr = f"{'metric':30s} {model_label:>10s}" + (f" {'baseline':>10s}" if do_base else "")
    print(hdr, flush=True)
    for k in ["pearson_delta", "pearson_delta_de", "pearson_de", "mse_de",
              "frac_correct_direction_20", "frac_correct_direction_all"]:
        s = summary["overall_model"].get(k + "_mean", float("nan"))
        line = f"{k:30s} {s:10.4f}"
        if do_base:
            line += f" {summary['overall_base'].get(k + '_mean', float('nan')):10.4f}"
        print(line, flush=True)
    print("[done]", flush=True)


if __name__ == "__main__":
    main()
