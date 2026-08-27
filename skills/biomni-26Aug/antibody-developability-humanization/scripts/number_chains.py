"""
number_chains.py - number VH/VL and delineate CDRs/framework.

The numbering/CDR-definition scheme is a PARAMETER (§8): kabat | imgt |
chothia | martin. It directly changes which residues are treated as CDR vs
framework and therefore which residues get grafted/back-mutated downstream, so
it must be chosen explicitly. Default = kabat (matches Carter 1992 grafting).

Outputs a per-residue annotation table and a per-chain germline/species summary.
"""
from __future__ import annotations
import json
import argparse
import pandas as pd

from ab_core import (make_chain, region_map, region_seq,
                     detect_species_and_germline, DEFAULT_SCHEME)


def annotate_chain(seq: str, chain_name: str, scheme: str = DEFAULT_SCHEME):
    c = make_chain(seq, scheme=scheme)
    rows = [{"chain": chain_name, "position": p, "aa": a, "region": r}
            for p, a, r in region_map(c)]
    cdrs = {r: region_seq(c, r) for r in ["CDR1", "CDR2", "CDR3"]}
    frs = {r: region_seq(c, r) for r in ["FR1", "FR2", "FR3", "FR4"]}
    germ = detect_species_and_germline(seq, scheme=scheme)
    summary = {"chain": chain_name, "scheme": scheme,
               "chain_type": germ.get("chain_type"),
               "species": germ.get("species"),
               "v_gene": germ.get("v_gene"), "j_gene": germ.get("j_gene"),
               **{f"{k}": v for k, v in cdrs.items()}}
    return pd.DataFrame(rows), summary, cdrs, frs


def number_all(vh=None, vl=None, scheme=DEFAULT_SCHEME, out_prefix="01"):
    tables, summaries = [], []
    for dom, seq in (("VH", vh), ("VL", vl)):
        if not seq:
            continue
        df, summ, _, _ = annotate_chain(seq, dom, scheme)
        tables.append(df)
        summaries.append(summ)
    ann = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    summ_df = pd.DataFrame(summaries)
    return ann, summ_df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vh")
    ap.add_argument("--vl")
    ap.add_argument("--scheme", default=DEFAULT_SCHEME,
                    choices=["kabat", "imgt", "chothia", "martin"])
    ap.add_argument("--outdir", default="/mnt/results/tables")
    a = ap.parse_args()
    ann, summ = number_all(a.vh, a.vl, a.scheme)
    import os
    os.makedirs(a.outdir, exist_ok=True)
    ann.to_csv(f"{a.outdir}/01_residue_annotation_{a.scheme}.csv", index=False)
    summ.to_csv(f"{a.outdir}/01_numbering_germline.csv", index=False)
    print(summ.to_string(index=False))
    print(f"\nSaved annotation ({len(ann)} residues) + germline summary.")
