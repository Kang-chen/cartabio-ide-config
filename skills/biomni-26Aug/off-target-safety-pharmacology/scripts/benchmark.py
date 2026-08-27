#!/usr/bin/env python3
"""
benchmark.py — Consensus + TIERED validation of off-target predictions.

Combines the two predictors into a consensus and, crucially, decides HOW MUCH the result
can be trusted based on how much measured ground truth exists for the query. This tiering
is the mechanism that stops de novo predictions from being dressed up as validated.

CONSENSUS & AGREEMENT
  P_sim               : logistic-calibrated similarity probability (primary predictor)
  dp_norm             : min-max normalized DeepPurpose affinity across the panel
  consensus           = (P_sim + dp_norm) / 2   [kept for continuity only — the two inputs are
                        NOT on a comparable scale, so consensus can rank disagreement beside agreement]
  agreement           : Both / Similarity only / DTI only / Neither  (the report ranks/groups on this)

SIMILARITY-HIT SPLIT (item: adaptive-expansion circularity)
  n_sim_hits_core     : similarity hits among the fixed core panel  -> the INDEPENDENT-evidence count
  n_sim_hits_adaptive : similarity hits among adaptively-added targets (scored ~1.0 by construction)

PRIMARY vs OFF-TARGET (item: primary target is NOT an off-target)
  The intended primary target and its orthologs (is_primary) are excluded from every off-target
  count/metric and reported separately as an on-target sanity check. When the primary is unresolved,
  a labeled proxy sensitivity (excluding the most-potent measured target) is reported too.

TIERS (data-sufficiency gate, evaluated against OFF-TARGET panel targets that have measured pChEMBL)
  Tier A  (validated):        >= 15 panel targets measured  AND  >= 5 positives (<=1 uM)
                              -> compute ROC-AUC + average precision; report as validation.
  Tier B  (partial context):  some measured overlap but below the Tier-A gate
                              -> report overlap/agreement descriptively; NOT called validation.
  Tier C  (de novo):          little/no measured overlap
                              -> predictions labeled "discovery-only, unvalidated".

Usage:
  python benchmark.py --sim <sim.csv> --dp <dp.csv> --truth <known_targets_collapsed.csv> \
        --outdir <outdir> [--tierA-min-measured 15] [--tierA-min-pos 5]

Outputs:
  <outdir>/data/offtarget_consensus.csv
  <outdir>/data/benchmark_prediction_vs_measured.csv
  <outdir>/data/benchmark_summary.json  (tier, metrics or reason, concordant list)
"""
import argparse, os, sys, json, warnings
import pandas as pd
import numpy as np
warnings.filterwarnings("ignore", category=FutureWarning)
try:
    pd.set_option("future.no_silent_downcasting", True)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_qc import agreement_state, normalize_prefname


def load_primary(path):
    """Load the intended primary target set from a resolve_compound JSON (if any)."""
    prim_u, prim_p, resolved = set(), set(), False
    if path and os.path.exists(path):
        c = json.load(open(path))
        prim_u = set(c.get("primary_uniprots", []) or [])
        prim_p = {normalize_prefname(p) for p in c.get("primary_pref_names", []) or []}
        resolved = bool(c.get("primary_target_resolved", False))
    return prim_u, prim_p, resolved


def _roc_ap(sub):
    """ROC-AUC (consensus + similarity-only) and average precision on a measured subset.
    Returns None if only one class is present."""
    from sklearn.metrics import roc_auc_score, average_precision_score
    y = sub["measured_active"].astype(int).values
    if len(set(y)) != 2:
        return None
    score = sub["consensus"].fillna(sub["P_sim"]).fillna(0).values
    s2 = sub["P_sim"].fillna(0).values
    return {"roc_auc_consensus": round(float(roc_auc_score(y, score)), 3),
            "average_precision": round(float(average_precision_score(y, score)), 3),
            "roc_auc_similarity": round(float(roc_auc_score(y, s2)), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--dp", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--tierA-min-measured", type=int, default=15)
    ap.add_argument("--tierA-min-pos", type=int, default=5)
    ap.add_argument("--dp-hit-quantile", type=float, default=0.75,
                    help="DeepPurpose hit = affinity in top quantile of panel")
    ap.add_argument("--compound-json", default=None,
                    help="resolve_compound output; supplies the intended primary target so it "
                         "(and orthologs) are excluded from the off-target benchmark")
    args = ap.parse_args()
    os.makedirs(f"{args.outdir}/data", exist_ok=True)

    sim = pd.read_csv(args.sim)
    dp = pd.read_csv(args.dp)
    truth = pd.read_csv(args.truth) if os.path.exists(args.truth) else pd.DataFrame()
    prim_u, prim_p, primary_resolved = load_primary(args.compound_json)

    # ---- consensus ----
    m = sim.merge(dp[["uniprot", "dp_affinity", "dp_pred_nM"]], on="uniprot", how="left")
    if m["dp_affinity"].notna().any():
        amin, amax = m["dp_affinity"].min(), m["dp_affinity"].max()
        rng = (amax - amin) or 1.0
        m["dp_norm"] = (m["dp_affinity"] - amin) / rng
        thr = m["dp_affinity"].quantile(args.dp_hit_quantile)
        m["dp_hit"] = m["dp_affinity"] >= thr
    else:
        m["dp_norm"] = np.nan
        m["dp_hit"] = False
    # NB: consensus averages the logistic-similarity probability with a min-max-normalized
    # DeepPurpose score. Those are NOT on a comparable scale, so consensus is kept only for
    # continuity — the 4-state `agreement` below is what the report ranks/groups on.
    m["consensus"] = m[["P_sim", "dp_norm"]].mean(axis=1)
    m["concordant"] = m["sim_hit"].fillna(False) & m["dp_hit"].fillna(False)
    m["agreement"] = [agreement_state(s, d)
                      for s, d in zip(m["sim_hit"].fillna(False), m["dp_hit"].fillna(False))]

    # robust is_primary on the consensus frame (panel tag OR UniProt/pref ortholog match)
    if "is_primary" not in m.columns:
        m["is_primary"] = False

    def _isprim(r):
        if bool(r.get("is_primary", False)):
            return True
        if r.get("uniprot") in prim_u:
            return True
        key = normalize_prefname(r.get("chembl_pref_name") or r.get("label"))
        return bool(key) and key in prim_p
    m["is_primary"] = m.apply(_isprim, axis=1)

    m = m.sort_values("consensus", ascending=False, na_position="last")
    m.to_csv(f"{args.outdir}/data/offtarget_consensus.csv", index=False)

    # ---- off-target vs on-target split (primary target is NOT an off-target) ----
    off = m[~m["is_primary"]].copy()
    concordant = off.loc[off["concordant"], "label"].tolist()   # off-target only
    agreement_counts = off["agreement"].value_counts().to_dict()
    # similarity-hit count MUST be split; core is the independent-evidence count
    src = off.get("source", pd.Series(index=off.index, dtype=str))
    simhit = off["sim_hit"].fillna(False)
    n_sim_hits_core = int(((src == "core") & simhit).sum())
    n_sim_hits_adaptive = int(((src == "adaptive") & simhit).sum())

    # ---- join to ground truth ----
    tier, metrics, reason = "C", {}, ""
    metrics_including_primary, proxy = {}, {}
    if not truth.empty:
        tmap = (truth.groupby("uniprot")
                .agg(measured_pchembl=("median_pchembl", "max"),
                     measured_active=("active_1uM", "max")).reset_index())
        bench = m.merge(tmap, on="uniprot", how="left")
        bench["measured"] = bench["measured_pchembl"].notna()
        bench["measured_active"] = bench["measured_active"].fillna(False).astype(bool)
        bench.to_csv(f"{args.outdir}/data/benchmark_prediction_vs_measured.csv",
                     index=False)

        # OFF-TARGET measured set drives the tier gate and the headline metrics
        bench_off = bench[~bench["is_primary"]]
        sub_off = bench_off[bench_off["measured"]].copy()
        n_measured = int(bench_off["measured"].sum())
        n_pos = int(bench_off["measured_active"].sum())
        metrics = {"n_measured": n_measured, "n_positives": n_pos}

        if n_measured >= args.tierA_min_measured and n_pos >= args.tierA_min_pos:
            rr = _roc_ap(sub_off)
            if rr:
                tier = "A"
                metrics.update(rr)
            else:
                tier = "B"
                reason = "Only one measured off-target class present; ROC-AUC undefined."
        elif n_measured > 0:
            tier = "B"
            reason = (f"Off-target measured overlap ({n_measured} targets, {n_pos} positives) "
                      f"below Tier-A gate (>= {args.tierA_min_measured} measured AND "
                      f">= {args.tierA_min_pos} positives).")
        else:
            tier = "C"
            reason = "No measured overlap between the off-target panel and the query's ChEMBL data."

        # transparency: naive metrics INCLUDING the primary target (the old, conflated view)
        sub_all = bench[bench["measured"]].copy()
        metrics_including_primary = {"n_measured": int(bench["measured"].sum()),
                                     "n_positives": int(bench["measured_active"].sum())}
        rr_all = _roc_ap(sub_all)
        if rr_all:
            metrics_including_primary.update(rr_all)

        # if the primary target was NOT confidently resolved, report BOTH ways: also exclude
        # the single most-potent measured target as a labeled PROXY for a possible primary.
        if (not primary_resolved) and len(sub_all):
            idx = sub_all["measured_pchembl"].astype(float).idxmax()
            proxy_label = sub_all.loc[idx, "label"]
            proxy_pref = sub_all.loc[idx].get("chembl_pref_name")
            key = normalize_prefname(proxy_pref or proxy_label)
            keep = sub_all.apply(
                lambda r: normalize_prefname(r.get("chembl_pref_name") or r["label"]) != key,
                axis=1)
            sub_proxy = sub_all[keep]
            proxy = {"excluded_proxy_primary": str(proxy_label),
                     "note": ("primary target unresolved from ChEMBL mechanism; this sensitivity "
                              "excludes the single most-potent measured target as a PROXY for a "
                              "possible primary target — a transparency check, not a claim"),
                     "n_measured": int(keep.sum()),
                     "n_positives": int(sub_proxy["measured_active"].sum())}
            rr_proxy = _roc_ap(sub_proxy)
            if rr_proxy:
                proxy.update(rr_proxy)
    else:
        bench = m.copy()
        bench["measured"] = False
        bench["measured_pchembl"] = np.nan
        bench["measured_active"] = False
        tier = "C"
        reason = "No ground-truth activities available for this compound."

    # ---- on-target sanity check (recovering the KNOWN primary target is not off-target perf) ----
    prim = bench[bench["is_primary"]].copy()
    on_target = {"resolved": bool(primary_resolved),
                 "n_primary_rows": int(len(prim)),
                 "n_measured": int(prim["measured"].sum()) if len(prim) else 0,
                 "n_recovered": int((prim["measured"] & prim["sim_hit"].fillna(False)).sum())
                 if len(prim) else 0,
                 "targets": []}
    for _, r in prim.sort_values("P_sim", ascending=False, na_position="last").iterrows():
        on_target["targets"].append({
            "label": str(r["label"]), "uniprot": str(r["uniprot"]),
            "P_sim": (float(r["P_sim"]) if pd.notna(r.get("P_sim")) else None),
            "agreement": str(r.get("agreement", "")),
            "measured": bool(r.get("measured", False)),
            "measured_pchembl": (float(r["measured_pchembl"])
                                 if pd.notna(r.get("measured_pchembl")) else None),
            "recovered": bool(r.get("measured", False) and bool(r.get("sim_hit", False)))})

    tier_label = {"A": "Validated (Tier A)",
                  "B": "Partial measured context (Tier B) — NOT validation",
                  "C": "De novo / discovery-only (Tier C) — unvalidated"}[tier]

    summary = {"tier": tier, "tier_label": tier_label,
               "metrics": metrics,                        # OFF-TARGET (headline)
               "metrics_including_primary": metrics_including_primary,
               "proxy_sensitivity": proxy,                # {} unless primary unresolved
               "primary_resolved": bool(primary_resolved),
               "reason": reason,
               "n_concordant": len(concordant), "concordant": concordant,  # off-target
               "n_sim_hits_core": n_sim_hits_core,
               "n_sim_hits_adaptive": n_sim_hits_adaptive,
               "n_sim_hits_offtarget_total": n_sim_hits_core + n_sim_hits_adaptive,
               "agreement_counts": agreement_counts,      # off-target
               "on_target": on_target}
    json.dump(summary, open(f"{args.outdir}/data/benchmark_summary.json", "w"),
              indent=2, default=str)
    print(json.dumps(summary, default=str))


if __name__ == "__main__":
    main()
