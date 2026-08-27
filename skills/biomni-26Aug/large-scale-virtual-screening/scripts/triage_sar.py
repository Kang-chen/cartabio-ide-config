#!/usr/bin/env python3
"""
triage_sar.py -- rank hits, triage top-N, and summarize preliminary SAR.

Works for BOTH branches:
  * always: top-N table, physchem descriptors, Butina scaffold clustering of top
    hits, generic (Bemis-Murcko) scaffolds, property-vs-affinity Spearman trends.
  * labeled only: precision@N of the top-N against activity_label, plus best-in-
    cluster among known actives.

Descriptors (RDKit): MW, cLogP, TPSA, HBD, HBA, RotB, AromaticRings, Fsp3,
HeavyAtoms, QED, murcko_scaffold.

Butina clustering: Morgan FP (r=2, nBits=2048), distance = 1 - Tanimoto,
Butina.ClusterData(..., cutoff, isDistData=True); clusters sorted by size desc.

Property-affinity Spearman flags a scoring-function SIZE/LIPOPHILICITY bias when
e.g. MW/cLogP/AromaticRings correlate with better (more negative) affinity -- an
important caveat for interpreting Vina hit lists.

Inputs:
  --scores all_scores_merged.csv
Outputs (into --outdir/tables and --outdir):
  tables/top_hits.csv            top-N with descriptors (+label if labeled)
  tables/scaffold_clusters.csv   cluster_id,size,members,best_mol_id,best_affinity
  molecular_descriptors.csv      descriptors for all scored compounds
  property_score_correlations.json  Spearman rho/p per property (+n)
  triage_summary.json            precision@N (labeled), counts, cluster stats
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import sys

try:
    from common import SEED, label_from_row
except ImportError:  # pragma: no cover
    try:
        from scripts.common import SEED, label_from_row
    except ImportError:
        SEED = 42

        def label_from_row(r):
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


def _descriptors(smiles):
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Crippen, QED, rdMolDescriptors
    from rdkit.Chem.Scaffolds import MurckoScaffold
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        return None
    try:
        scaf = MurckoScaffold.MurckoScaffoldSmiles(mol=m)
    except Exception:
        scaf = ""
    return {
        "MW": round(Descriptors.MolWt(m), 2),
        "cLogP": round(Crippen.MolLogP(m), 3),
        "TPSA": round(rdMolDescriptors.CalcTPSA(m), 2),
        "HBD": rdMolDescriptors.CalcNumHBD(m),
        "HBA": rdMolDescriptors.CalcNumHBA(m),
        "RotB": rdMolDescriptors.CalcNumRotatableBonds(m),
        "AromaticRings": rdMolDescriptors.CalcNumAromaticRings(m),
        "Fsp3": round(rdMolDescriptors.CalcFractionCSP3(m), 3),
        "HeavyAtoms": m.GetNumHeavyAtoms(),
        "QED": round(QED.qed(m), 3),
        "murcko_scaffold": scaf,
    }


def _load_scored(scores_csv):
    rows = []
    with open(scores_csv, newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("status") != "ok" or not r.get("vina_affinity"):
                continue
            try:
                r["_aff"] = float(r["vina_affinity"])
            except ValueError:
                continue
            r["_label"] = label_from_row(r)  # 1 / 0 / None, resolved once
            rows.append(r)
    rows.sort(key=lambda r: r["_aff"])  # best (most negative) first
    return rows


def _butina(smis, cutoff):
    from rdkit import Chem
    from rdkit.Chem import AllChem, DataStructs
    from rdkit.ML.Cluster import Butina
    fps = []
    keep = []
    for i, smi in enumerate(smis):
        m = Chem.MolFromSmiles(smi)
        if m is None:
            continue
        fps.append(AllChem.GetMorganFingerprintAsBitVect(m, 2, nBits=2048))
        keep.append(i)
    n = len(fps)
    dists = []
    for i in range(1, n):
        sims = DataStructs.BulkTanimotoSimilarity(fps[i], fps[:i])
        dists.extend(1 - s for s in sims)
    clusters = Butina.ClusterData(dists, n, cutoff, isDistData=True)
    clusters = sorted(clusters, key=len, reverse=True)
    # map back to original indices
    return [[keep[i] for i in c] for c in clusters]


def run(args) -> int:
    rows = _load_scored(args.scores)
    if not rows:
        print("[ERROR] no successfully scored compounds", file=sys.stderr); return 2
    labeled = any(r["_label"] in (0, 1) for r in rows)

    def _lab_str(r):
        return "" if r["_label"] is None else str(r["_label"])

    # descriptors for all scored
    tables = os.path.join(args.outdir, "tables")
    os.makedirs(tables, exist_ok=True)
    desc_fields = ["MW", "cLogP", "TPSA", "HBD", "HBA", "RotB", "AromaticRings",
                   "Fsp3", "HeavyAtoms", "QED", "murcko_scaffold"]
    with open(os.path.join(args.outdir, "molecular_descriptors.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mol_id", "smiles", "source", "activity_label", "vina_affinity"] + desc_fields)
        for r in rows:
            d = _descriptors(r["smiles"]) or {k: "" for k in desc_fields}
            w.writerow([r["mol_id"], r["smiles"], r.get("source", ""),
                        _lab_str(r), r["_aff"]] + [d[k] for k in desc_fields])

    # top-N table
    topN = rows[: args.top_n]
    with open(os.path.join(tables, "top_hits.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "mol_id", "smiles", "source", "activity_label",
                    "vina_affinity"] + desc_fields)
        for i, r in enumerate(topN, 1):
            d = _descriptors(r["smiles"]) or {k: "" for k in desc_fields}
            w.writerow([i, r["mol_id"], r["smiles"], r.get("source", ""),
                        _lab_str(r), r["_aff"]] + [d[k] for k in desc_fields])

    summary = {"n_scored": len(rows), "labeled": labeled, "top_n": args.top_n}

    # precision@N (labeled)
    if labeled:
        def prec_at(n):
            sub = rows[:n]
            hits = sum(1 for r in sub if r["_label"] == 1)
            return round(100.0 * hits / max(1, len(sub)), 1), hits
        n_act = sum(1 for r in rows if r["_label"] == 1)
        summary["baseline_active_rate_pct"] = round(100.0 * n_act / len(rows), 2)
        summary["precision_at"] = {str(n): {"pct": prec_at(n)[0], "hits": prec_at(n)[1]}
                                   for n in args.precision_ns}

    # scaffold clustering of top hits (or actives if labeled)
    if labeled:
        cluster_rows = [r for r in rows if r["_label"] == 1]
    else:
        cluster_rows = rows[: args.cluster_top]
    clusters = _butina([r["smiles"] for r in cluster_rows], args.tanimoto_cutoff)
    with open(os.path.join(tables, "scaffold_clusters.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cluster_id", "size", "best_mol_id", "best_affinity", "members"])
        for cid, idxs in enumerate(clusters):
            members = [cluster_rows[i] for i in idxs]
            best = min(members, key=lambda r: r["_aff"])
            w.writerow([cid, len(idxs), best["mol_id"], round(best["_aff"], 3),
                        ";".join(m["mol_id"] for m in members)])
    summary["n_clusters"] = len(clusters)
    summary["n_singletons"] = sum(1 for c in clusters if len(c) == 1)
    summary["largest_clusters"] = [len(c) for c in clusters[:5]]
    summary["clustered_set"] = "actives" if labeled else f"top_{args.cluster_top}"

    # property-affinity Spearman (on the clustered set if labeled=actives, else all)
    from scipy.stats import spearmanr
    corr_rows = cluster_rows if labeled else rows
    props = ["MW", "cLogP", "TPSA", "HBD", "HBA", "RotB", "AromaticRings", "Fsp3",
             "HeavyAtoms", "QED"]
    affs = [r["_aff"] for r in corr_rows]
    corr = {}
    for p in props:
        vals = []
        ok_aff = []
        for r, a in zip(corr_rows, affs):
            d = _descriptors(r["smiles"])
            if d and d.get(p) != "":
                vals.append(d[p]); ok_aff.append(a)
        if len(vals) >= 5:
            rho, pv = spearmanr(vals, ok_aff)
            corr[p] = {"rho": round(float(rho), 4), "p": float(pv), "n": len(vals)}
    with open(os.path.join(args.outdir, "property_score_correlations.json"), "w") as fh:
        json.dump({"note": "rho<0 => property correlates with MORE NEGATIVE (better) "
                           "affinity; strong MW/cLogP/AromaticRings trends flag a "
                           "Vina size/lipophilicity bias.",
                   "set": summary["clustered_set"], "correlations": corr}, fh, indent=2)

    with open(os.path.join(args.outdir, "triage_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)

    print(f"[OK] top {args.top_n} hits, {len(clusters)} clusters "
          f"({summary['n_singletons']} singletons), correlations for {len(corr)} properties")
    if labeled:
        print(f"[OK] precision@N: {summary.get('precision_at')} "
              f"(baseline {summary.get('baseline_active_rate_pct')}%)")
    return 0


def self_check() -> int:
    ok = True
    d = _descriptors("c1ccccc1")  # benzene
    if not d or d["AromaticRings"] != 1 or d["HeavyAtoms"] != 6:
        print(f"[FAIL] descriptors -> {d}", file=sys.stderr); ok = False
    cl = _butina(["c1ccccc1", "c1ccccc1C", "CCCCCCCC", "CCCCCCCCC"], 0.4)
    if not cl or len(cl) < 1:
        print(f"[FAIL] butina -> {cl}", file=sys.stderr); ok = False
    print(f"triage_sar.py self-check: {'PASS' if ok else 'FAIL'} "
          f"(benzene arom-rings=1, butina made {len(cl)} clusters)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Hit triage + preliminary SAR")
    ap.add_argument("--scores", help="all_scores_merged.csv")
    ap.add_argument("--outdir", default="/mnt/results/sbvs_run")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--cluster-top", type=int, default=200, help="unlabeled: cluster top-K")
    ap.add_argument("--precision-ns", type=int, nargs="+", default=[10, 20, 50, 100])
    ap.add_argument("--tanimoto-cutoff", type=float, default=0.4)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    if not args.scores:
        ap.error("--scores is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
