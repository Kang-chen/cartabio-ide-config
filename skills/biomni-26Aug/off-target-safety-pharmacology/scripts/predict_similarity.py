#!/usr/bin/env python3
"""
predict_similarity.py — Ligand-based off-target prediction (PRIMARY predictor).

For each panel target, pull its known actives from ChEMBL (pChEMBL >= 6), compute the
max ECFP4 Tanimoto between the QUERY and that target's actives, then map that similarity
to a hit probability with a logistic function. This is the chemical-similarity principle:
a molecule is likely to hit targets whose known ligands it structurally resembles.

LEAVE-QUERY-OUT (critical for honest benchmarking): the query is removed from every
target's active set by BOTH canonical SMILES and InChIKey-14, so a compound with measured
data is never scored against itself.

Calibration (verified reference parameters):  P = 1 / (1 + exp(-k*(Tc - t0))),  k=12, t0=0.35
  -> Tc=0.35 gives P=0.5 (decision boundary); hit if P >= 0.5.

Usage:
  python predict_similarity.py --smiles "<canonical>" --inchikey14 <14> \
        --panel <outdir>/data/prediction_panel.csv --outdir <outdir> [--min-actives 5]

Output:
  <outdir>/data/offtarget_similarity_predictions.csv
     columns: uniprot,label,target_class,chembl_target_id,source,
              n_actives,max_tanimoto,P_sim,sim_hit
"""
import argparse, os, sys, time, json, math
import requests
import pandas as pd
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs
from rdkit import RDLogger
RDLogger.DisableLog("rdApp.*")

CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"
K, T0 = 12.0, 0.35


def _get(url, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=60,
                             headers={"Accept": "application/json"})
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def ecfp4(smiles):
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        return None
    return AllChem.GetMorganFingerprintAsBitVect(m, radius=2, nBits=2048)


def fetch_actives_fps(tid, query_smiles_canon, query_ik14, cap=1000):
    """All fps of a target's actives (pChEMBL>=6), with the query removed."""
    fps = []
    offset = 0
    seen = 0
    while True:
        j = _get(f"{CHEMBL}/activity.json",
                 params={"target_chembl_id": tid, "pchembl_value__gte": 6,
                         "limit": 1000, "offset": offset,
                         "only": "canonical_smiles,molecule_chembl_id"})
        acts = (j or {}).get("activities", [])
        if not acts:
            break
        for a in acts:
            smi = a.get("canonical_smiles")
            if not smi:
                continue
            m = Chem.MolFromSmiles(smi)
            if m is None:
                continue
            # leave-query-out: drop the query by canonical SMILES OR InChIKey-14
            try:
                if Chem.MolToSmiles(m) == query_smiles_canon:
                    continue
                if Chem.MolToInchiKey(m)[:14] == query_ik14:
                    continue
            except Exception:
                pass
            fps.append(AllChem.GetMorganFingerprintAsBitVect(m, 2, nBits=2048))
            seen += 1
        offset += 1000
        page = (j or {}).get("page_meta", {})
        if not page.get("next") or seen >= cap:
            break
    return fps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smiles", required=True, help="query canonical SMILES")
    ap.add_argument("--inchikey14", required=True)
    ap.add_argument("--panel", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--min-actives", type=int, default=5,
                    help="targets with fewer usable actives are reported but not scored")
    args = ap.parse_args()
    os.makedirs(f"{args.outdir}/data", exist_ok=True)

    qfp = ecfp4(args.smiles)
    if qfp is None:
        print(json.dumps({"status": "error", "msg": "bad query SMILES"}))
        sys.exit(1)
    # canonicalize query once for exact-match exclusion
    q_canon = Chem.MolToSmiles(Chem.MolFromSmiles(args.smiles))

    panel = pd.read_csv(args.panel)
    rows = []
    for _, r in panel.iterrows():
        tid = r["chembl_target_id"]
        fps = fetch_actives_fps(tid, q_canon, args.inchikey14) if isinstance(tid, str) else []
        n = len(fps)
        if n == 0:
            max_tc, p, hit = float("nan"), float("nan"), False
        else:
            sims = DataStructs.BulkTanimotoSimilarity(qfp, fps)
            max_tc = max(sims)
            p = 1.0 / (1.0 + math.exp(-K * (max_tc - T0)))
            hit = bool(p >= 0.5) if n >= args.min_actives else False
        rows.append({**r.to_dict(), "n_actives": n, "max_tanimoto": max_tc,
                     "P_sim": p, "sim_hit": hit})
        print(f"  {r['label'][:28]:28s} n={n:4d} maxTc={max_tc if max_tc==max_tc else float('nan'):.3f} "
              f"P={p if p==p else float('nan'):.3f} hit={hit}", file=sys.stderr)

    out = pd.DataFrame(rows).sort_values("P_sim", ascending=False, na_position="last")
    out.to_csv(f"{args.outdir}/data/offtarget_similarity_predictions.csv", index=False)
    nhit = int(out["sim_hit"].sum())
    print(json.dumps({"status": "ok", "n_targets": len(out), "n_hits": nhit,
                      "top": out.head(8)[["label", "max_tanimoto", "P_sim"]]
                      .to_dict("records")}, default=str))


if __name__ == "__main__":
    main()
