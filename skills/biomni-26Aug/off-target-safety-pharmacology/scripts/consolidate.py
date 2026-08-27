#!/usr/bin/env python3
"""
consolidate.py — Gather all upstream outputs into a single report_data.json for the report step.

Reads whatever exists in <outdir>/data and produces a single JSON with the compound block,
ADMET engine/flags, benchmark tier/metrics, ground-truth counts, top predictions, and the
reference list. Missing pieces degrade gracefully (e.g. no ground truth -> Tier C fields).

Usage:
  python consolidate.py --outdir <outdir> --compound-json <resolve output.json> \
        [--references references/references.json]
Output: <outdir>/data/report_data.json
"""
import argparse, os, sys, json
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_qc import AGREEMENT_ORDER


def _read_csv(p):
    return pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--compound-json", required=True)
    ap.add_argument("--references", default=None)
    args = ap.parse_args()
    D = f"{args.outdir}/data"

    comp = json.load(open(args.compound_json))

    # ADMET
    admet_meta = {}
    if os.path.exists(f"{D}/admet_meta.json"):
        admet_meta = json.load(open(f"{D}/admet_meta.json"))
    flags = admet_meta.get("flags", {})
    admet = {"engine": admet_meta.get("engine", "unknown"),
             "n_endpoints": admet_meta.get("n_endpoints", "?"),
             "has_percentiles": admet_meta.get("has_percentiles", False),
             "herg": flags.get("hERG", {}), "flags": flags}

    # benchmark
    bench = {"tier": "C", "tier_label": "De novo / discovery-only (Tier C) — unvalidated",
             "metrics": {}, "reason": "No benchmark run.", "concordant": []}
    if os.path.exists(f"{D}/benchmark_summary.json"):
        bench = json.load(open(f"{D}/benchmark_summary.json"))

    # ground truth
    gt = {"n_distinct_proteins": 0, "n_active_1um": 0, "n_potent_100nm": 0}
    if os.path.exists(f"{D}/ground_truth_summary.json"):
        gt = json.load(open(f"{D}/ground_truth_summary.json"))

    # panel counts
    panel = _read_csv(f"{D}/prediction_panel.csv")
    _src = panel.get("source", pd.Series(dtype=str))
    n_core = int((_src == "core").sum()) if len(panel) else 0
    n_adaptive = int((_src == "adaptive").sum()) if len(panel) else 0
    n_primary = int((_src == "primary").sum()) if len(panel) else 0

    # top OFF-TARGET predictions (primary target excluded — it is on-target), grouped so
    # dual-engine ("Both") support ranks above single-engine, then by consensus. Each row
    # carries its per-engine values and 4-state agreement so the ranking is never shown alone.
    con = _read_csv(f"{D}/offtarget_consensus.csv")
    top = []
    if len(con):
        if "is_primary" in con.columns:
            con = con[~con["is_primary"].fillna(False).astype(bool)]
        con = con.dropna(subset=["consensus"]).copy()
        agr = con["agreement"] if "agreement" in con.columns else pd.Series("", index=con.index)
        con["_ord"] = agr.map(lambda a: AGREEMENT_ORDER.get(str(a), 9))
        con = con.sort_values(["_ord", "consensus"], ascending=[True, False])
        for _, r in con.head(10).iterrows():
            top.append({"label": str(r.get("label", "")),
                        "target_class": str(r.get("target_class", "")),
                        "source": str(r.get("source", "")),
                        "is_primary": bool(r.get("is_primary", False)),
                        "chembl_pref_name": str(r.get("chembl_pref_name", "")),
                        "max_tanimoto": _f(r.get("max_tanimoto")),
                        "P_sim": _f(r.get("P_sim")),
                        "dp_norm": _f(r.get("dp_norm")),
                        "consensus": _f(r.get("consensus")),
                        "agreement": str(r.get("agreement", ""))})

    # references
    refs = []
    ref_path = args.references or f"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}/references/references.json"
    if os.path.exists(ref_path):
        refs = json.load(open(ref_path))

    rd = {"compound": comp, "admet": admet, "benchmark": bench, "ground_truth": gt,
          "panel": {"n_core": n_core, "n_adaptive": n_adaptive, "n_primary": n_primary},
          "top_predictions": top, "references": refs}
    json.dump(rd, open(f"{D}/report_data.json", "w"), indent=2, default=str)
    print(json.dumps({"status": "ok", "tier": bench["tier"],
                      "engine": admet["engine"], "n_top": len(top),
                      "n_refs": len(refs)}, default=str))


if __name__ == "__main__":
    main()
