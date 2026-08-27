"""
developability_scan.py - chemical-degradation liabilities + biophysical descriptors.

Runs the validated CDR-weighted liability motif scan and biophysical profile on
one or more constructs (a construct = {name: {VH, VL}}). Sequence-only; no
structure. Motif rules & severities are the validated set in ab_core.

Outputs, per construct:
  - a per-motif liability table (chain, motif, residues, position, region,
    location CDR/FR, severity, CDR-weighted severity)
  - a rollup (total liabilities, CDR liabilities, weighted burden, N-glyco count)
  - a biophysical row (pI, net charge @ pH 6.0/7.4, GRAVY, aromaticity, counts)
"""
from __future__ import annotations
import json
import argparse
import pandas as pd

from ab_core import (make_chain, scan_liabilities, biophysical,
                     aggregation_scan, DEFAULT_SCHEME)


def scan_construct(name, vh, vl, scheme=DEFAULT_SCHEME):
    """Scan one construct. Returns (motif_df, rollup, bio_df, apr_df).

    rollup carries BOTH the chemical-degradation liability burden AND the named
    AGGRESCAN aggregation metric (agg_score_Fv, n_APR, APR_in_CDR/FR,
    agg_weighted). apr_df is the per-aggregation-prone-region table."""
    per_motif, bio_rows, apr_frames = [], [], []
    total = cdr = weighted = nglyco = 0
    n_apr = apr_cdr = apr_fr = 0
    agg_weighted = 0.0
    agg_scores = []
    for dom, seq in (("VH", vh), ("VL", vl)):
        if not seq:
            continue
        c = make_chain(seq, scheme=scheme)
        df, _ = scan_liabilities(c, f"{name}_{dom}")
        if len(df):
            per_motif.append(df)
            total += len(df)
            cdr += int((df.location == "CDR").sum())
            weighted += float(df.weighted_severity.sum())
            nglyco += int(df.motif_type.str.contains("glyco").sum())
        bio_rows.append(biophysical(seq, f"{name}_{dom}"))
        # named aggregation-propensity assessment (AGGRESCAN a3v)
        apr_df, agg_roll = aggregation_scan(c, f"{name}_{dom}")
        if len(apr_df):
            apr_frames.append(apr_df)
        n_apr += agg_roll["n_APR"]
        apr_cdr += agg_roll["APR_in_CDR"]
        apr_fr += agg_roll["APR_in_FR"]
        agg_weighted += agg_roll["agg_weighted"]
        agg_scores.append(agg_roll["agg_score"])
    rollup = {"construct": name, "total_liabilities": total,
              "CDR_liabilities": cdr,
              "total_weighted_burden": round(weighted, 2),
              "N_glyco_sites": nglyco,
              # ---- named aggregation metric (AGGRESCAN a3v) ----
              "agg_score_Fv": round(float(sum(agg_scores) / len(agg_scores)), 3)
              if agg_scores else 0.0,
              "n_APR": n_apr, "APR_in_CDR": apr_cdr, "APR_in_FR": apr_fr,
              "agg_weighted": round(agg_weighted, 2)}
    motif_df = pd.concat(per_motif, ignore_index=True) if per_motif else pd.DataFrame()
    apr_out = pd.concat(apr_frames, ignore_index=True) if apr_frames else pd.DataFrame()
    return motif_df, rollup, pd.DataFrame(bio_rows), apr_out


def scan_all(constructs: dict, scheme=DEFAULT_SCHEME):
    all_motifs, rollups, bios, aprs = [], [], [], []
    for name, v in constructs.items():
        m, r, b, a = scan_construct(name, v.get("VH"), v.get("VL"), scheme)
        if len(m):
            all_motifs.append(m)
        rollups.append(r)
        bios.append(b)
        if len(a):
            aprs.append(a)
    return (pd.concat(all_motifs, ignore_index=True) if all_motifs else pd.DataFrame(),
            pd.DataFrame(rollups),
            pd.concat(bios, ignore_index=True) if bios else pd.DataFrame(),
            pd.concat(aprs, ignore_index=True) if aprs else pd.DataFrame())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--constructs", required=True,
                    help="JSON file: {name: {VH:..., VL:...}}")
    ap.add_argument("--scheme", default=DEFAULT_SCHEME)
    ap.add_argument("--outdir", default="/mnt/results/tables")
    a = ap.parse_args()
    constructs = json.load(open(a.constructs))
    motifs, rollup, bio, aprs = scan_all(constructs, a.scheme)
    import os
    os.makedirs(a.outdir, exist_ok=True)
    motifs.to_csv(f"{a.outdir}/03_liabilities_per_motif.csv", index=False)
    rollup.to_csv(f"{a.outdir}/03_liabilities_rollup.csv", index=False)
    bio.to_csv(f"{a.outdir}/03_biophysical.csv", index=False)
    if len(aprs):
        aprs.to_csv(f"{a.outdir}/03_aggregation_APRs.csv", index=False)
    print(rollup.to_string(index=False))
