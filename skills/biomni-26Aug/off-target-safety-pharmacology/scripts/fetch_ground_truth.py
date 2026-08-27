#!/usr/bin/env python3
"""
fetch_ground_truth.py — Pull the QUERY's OWN measured target activities from ChEMBL.

This is the ground-truth layer used (a) to benchmark the predictions when enough data
exists, and (b) to report what is actually known about the compound. It is kept STRICTLY
SEPARATE from the prediction panel: a compound's measured off-targets are never used to
build the panel it is scored against (that would be circular, and impossible for a novel
compound). For a truly novel compound this step simply returns little/nothing, which is
exactly what pushes the benchmark into the lower tiers.

Definitions (verified reference):
  - potency assays: standard_type in {IC50, Ki, Kd, EC50, Potency, AC50} with pChEMBL not null
  - per target: median pChEMBL across assays;  nM = 10^(9 - pChEMBL)
  - active  : median pChEMBL >= 6  (<= 1 uM)
  - potent  : median pChEMBL >= 7  (<= 100 nM)

Usage:
  python fetch_ground_truth.py --chembl-id <CHEMBLxxxx> --outdir <outdir>

Outputs:
  <outdir>/data/known_targets_collapsed.csv
     uniprot,target_pref_name,chembl_target_id,median_pchembl,max_pchembl,
     n_meas,median_nM,active_1uM,potent_100nM
  <outdir>/data/ground_truth_summary.json
     n_records, n_with_pchembl, n_distinct_proteins, n_active_1um, n_potent_100nm
"""
import argparse, os, sys, time, json
import requests
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_qc import normalize_prefname

CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"
POTENCY_TYPES = {"IC50", "Ki", "Kd", "EC50", "Potency", "AC50"}


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


def fetch_all_activities(chembl_id):
    acts = []
    offset = 0
    while True:
        j = _get(f"{CHEMBL}/activity.json",
                 params={"molecule_chembl_id": chembl_id, "limit": 1000,
                         "offset": offset})
        page = (j or {}).get("activities", [])
        if not page:
            break
        acts.extend(page)
        meta = (j or {}).get("page_meta", {})
        if not meta.get("next"):
            break
        offset += 1000
    return acts


def target_uniprot(tid, cache):
    if tid in cache:
        return cache[tid]
    j = _get(f"{CHEMBL}/target/{tid}.json")
    acc, pref, ttype = None, None, None
    if j:
        ttype = j.get("target_type")
        pref = j.get("pref_name")
        for comp in j.get("target_components", []):
            if comp.get("accession"):
                acc = comp["accession"]
                break
    cache[tid] = (acc, pref, ttype)
    return cache[tid]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chembl-id", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--compound-json", default=None,
                    help="resolve_compound output; flags the intended primary target(s) in the "
                         "measured-target table so the benchmark can keep on-target out of the "
                         "off-target positive set")
    args = ap.parse_args()
    os.makedirs(f"{args.outdir}/data", exist_ok=True)

    # intended primary target(s) for is_primary tagging (optional)
    primary_uniprots, primary_prefs = set(), set()
    if args.compound_json and os.path.exists(args.compound_json):
        comp = json.load(open(args.compound_json))
        primary_uniprots = set(comp.get("primary_uniprots", []) or [])
        primary_prefs = {normalize_prefname(p) for p in comp.get("primary_pref_names", []) or []}

    def _mark_primary(df):
        if len(df) == 0:
            df["is_primary"] = pd.Series(dtype=bool)
            return df
        df["is_primary"] = df.apply(
            lambda r: (r["uniprot"] in primary_uniprots)
            or (normalize_prefname(r.get("target_pref_name")) in primary_prefs), axis=1)
        return df

    acts = fetch_all_activities(args.chembl_id)
    n_records = len(acts)
    # keep potency assays with a pChEMBL value
    rows = []
    for a in acts:
        st = a.get("standard_type")
        pv = a.get("pchembl_value")
        tid = a.get("target_chembl_id")
        if st in POTENCY_TYPES and pv not in (None, "") and tid:
            try:
                rows.append({"chembl_target_id": tid, "pchembl": float(pv)})
            except Exception:
                pass
    df = pd.DataFrame(rows)
    n_with_pchembl = len(df)

    if df.empty:
        collapsed = pd.DataFrame(columns=["uniprot", "target_pref_name",
                                          "chembl_target_id", "median_pchembl",
                                          "max_pchembl", "n_meas", "median_nM",
                                          "active_1uM", "potent_100nM"])
        collapsed = _mark_primary(collapsed)
        collapsed.to_csv(f"{args.outdir}/data/known_targets_collapsed.csv", index=False)
        summ = {"n_records": n_records, "n_with_pchembl": 0,
                "n_distinct_proteins": 0, "n_active_1um": 0, "n_potent_100nm": 0}
        json.dump(summ, open(f"{args.outdir}/data/ground_truth_summary.json", "w"),
                  indent=2)
        print(json.dumps(summ))
        return

    # collapse to single-protein targets, resolve UniProt
    cache = {}
    g = df.groupby("chembl_target_id")["pchembl"].agg(["median", "max", "count"])
    out_rows = []
    for tid, r in g.iterrows():
        acc, pref, ttype = target_uniprot(tid, cache)
        if ttype != "SINGLE PROTEIN" or not acc:
            continue
        med = float(r["median"])
        out_rows.append({
            "uniprot": acc, "target_pref_name": pref, "chembl_target_id": tid,
            "median_pchembl": round(med, 3), "max_pchembl": round(float(r["max"]), 3),
            "n_meas": int(r["count"]), "median_nM": round(10 ** (9 - med), 3),
            "active_1uM": bool(med >= 6), "potent_100nM": bool(med >= 7)})
    collapsed = pd.DataFrame(out_rows).sort_values("median_pchembl", ascending=False)
    collapsed = _mark_primary(collapsed)
    collapsed.to_csv(f"{args.outdir}/data/known_targets_collapsed.csv", index=False)

    summ = {"n_records": n_records, "n_with_pchembl": n_with_pchembl,
            "n_distinct_proteins": int(len(collapsed)),
            "n_active_1um": int(collapsed["active_1uM"].sum()),
            "n_potent_100nm": int(collapsed["potent_100nM"].sum())}
    json.dump(summ, open(f"{args.outdir}/data/ground_truth_summary.json", "w"),
              indent=2)
    print(json.dumps(summ))


if __name__ == "__main__":
    main()
