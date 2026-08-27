#!/usr/bin/env python3
"""
benchmark_reveal.py  --  OPTIONAL reference-present benchmark.

Only runs when a clinical/known reference antibody is available (the muMAb 4D5
-> trastuzumab case). It quantifies how close a BLIND humanized design is to the
real thing:
  (A) whole-domain sequence identity of each design vs the reference (VH & VL),
      via BLOSUM62 global alignment;
  (B) back-mutation concordance: at each framework position the design reverted,
      does the residue match the reference's actual framework residue?
  (C) canonical-recovery score: of the reference's known essential framework
      back-mutations, how many did the blind design recover?

This module is NOT part of the default pipeline. In the default (reference-
absent) mode it is simply skipped, and the report uses germline humanness +
liability + immunogenicity as the quality read-outs instead.

Public API:
  pct_identity(a, b) -> (pct, n_match, n_aligned)
  identity_vs_reference(constructs, ref_vh, ref_vl, design_keys=None)
  backmutation_concordance(design_vh, design_vl, graft_vh, graft_vl,
                           ref_vh, ref_vl, scheme=, canonical=None)
  benchmark(constructs, ref_vh, ref_vl, lead_key, graft_key, scheme=,
            canonical=None, ref_name="reference")

CLI:
  python benchmark_reveal.py --constructs constructs.json \
      --ref-vh <seq> --ref-vl <seq> --lead hu_consensus_bmut \
      --graft hu_consensus_graft [--scheme kabat]
"""
from __future__ import annotations
import argparse, json
import pandas as pd

from ab_core import make_chain, DEFAULT_SCHEME

# Trastuzumab's known essential framework back-mutations (Kabat), from the
# Genentech humanization (Carter 1992 PNAS; patent SEQ IDs). Default canonical
# set for the 4D5 example. For other references, pass your own via --canonical.
TRASTUZUMAB_CANONICAL = ["H71", "H73", "H78", "H93", "L66"]


def pct_identity(a: str, b: str):
    """BLOSUM62 global-alignment % identity over aligned (non-gap) columns."""
    from Bio import pairwise2
    from Bio.Align import substitution_matrices
    blosum62 = substitution_matrices.load("BLOSUM62")
    aln = pairwise2.align.globalds(a, b, blosum62, -10, -0.5,
                                   one_alignment_only=True)[0]
    s1, s2 = aln.seqA, aln.seqB
    match = sum(1 for x, y in zip(s1, s2) if x == y and x != "-")
    aligned = sum(1 for x, y in zip(s1, s2) if x != "-" and y != "-")
    return round(100 * match / aligned, 1), match, aligned


def identity_vs_reference(constructs: dict, ref_vh: str, ref_vl: str,
                          design_keys=None):
    """% identity of each construct's VH/VL vs the reference."""
    keys = design_keys or list(constructs.keys())
    rows = []
    for k in keys:
        v = constructs[k]
        vh_id = pct_identity(v["VH"], ref_vh)[0] if v.get("VH") else None
        vl_id = pct_identity(v["VL"], ref_vl)[0] if v.get("VL") else None
        rows.append({"construct": k, "label": v.get("label", ""),
                     "VH_vs_ref_%": vh_id, "VL_vs_ref_%": vl_id})
    return pd.DataFrame(rows)


def _kmap(seq, scheme):
    return {str(p): a for p, a in make_chain(seq, scheme=scheme)}


def backmutation_concordance(design_vh, design_vl, graft_vh, graft_vl,
                             ref_vh, ref_vl, scheme=DEFAULT_SCHEME,
                             canonical=None):
    """Per-position concordance of the lead design's back-mutations vs the
    reference. Compares graft(human) -> design -> reference at every framework
    position where the design differs from the naive graft."""
    canonical = canonical or TRASTUZUMAB_CANONICAL
    rows = []
    for dom, dseq, gseq, rseq in (("VH", design_vh, graft_vh, ref_vh),
                                  ("VL", design_vl, graft_vl, ref_vl)):
        if not (dseq and gseq and rseq):
            continue
        dmap, gmap, rmap = (_kmap(dseq, scheme), _kmap(gseq, scheme),
                            _kmap(rseq, scheme))

        def _num(lbl):
            import re
            m = re.match(r"[A-Z]?(\d+)", lbl)
            return int(m.group(1)) if m else 0
        # positions where design != graft = the back-mutations that were applied
        for lbl in sorted(gmap, key=_num):
            g = gmap.get(lbl, "-")
            m = dmap.get(lbl, "-")
            r = rmap.get(lbl, "-")
            was_bm = (g != m and m != "-")
            if not was_bm:
                continue
            rows.append({"domain": dom, "position": lbl,
                         "graft_human": g, "design_backmut": m,
                         "reference": r, "concordant": (m == r),
                         "over_correction": (g == r and m != r)})
    df = pd.DataFrame(rows)
    return df


def benchmark(constructs: dict, ref_vh: str, ref_vl: str,
              lead_key: str, graft_key: str, scheme: str = DEFAULT_SCHEME,
              canonical=None, ref_name: str = "reference"):
    """Full reference-present benchmark. Returns dict of DataFrames + scores."""
    canonical = canonical or TRASTUZUMAB_CANONICAL
    ident = identity_vs_reference(constructs, ref_vh, ref_vl)

    lead = constructs[lead_key]
    graft = constructs[graft_key]
    concord = backmutation_concordance(
        lead.get("VH"), lead.get("VL"), graft.get("VH"), graft.get("VL"),
        ref_vh, ref_vl, scheme=scheme, canonical=canonical)

    n_bm = int(len(concord))
    n_conc = int(concord["concordant"].sum()) if n_bm else 0
    overcorr = (concord[concord["over_correction"]]["position"].tolist()
                if n_bm else [])
    recovered = ([p for p in canonical
                  if p in set(concord[concord["concordant"]]["position"])]
                 if n_bm else [])

    scores = {"reference": ref_name, "lead": lead_key,
              "n_backmutations": n_bm, "n_concordant": n_conc,
              "pct_concordant": round(100 * n_conc / n_bm, 1) if n_bm else None,
              "canonical_set": canonical,
              "canonical_recovered": recovered,
              "canonical_recovery": f"{len(recovered)}/{len(canonical)}",
              "over_corrections": overcorr}

    # lead identity headline
    lead_id = ident[ident["construct"] == lead_key]
    if len(lead_id):
        scores["lead_VH_identity_%"] = float(lead_id.iloc[0]["VH_vs_ref_%"])
        scores["lead_VL_identity_%"] = float(lead_id.iloc[0]["VL_vs_ref_%"])

    return {"identity": ident, "concordance": concord, "scores": scores}


def main():
    ap = argparse.ArgumentParser(description="Reference-present humanization benchmark")
    ap.add_argument("--constructs", required=True,
                    help="JSON {name:{VH,VL,label}} or humanize() output")
    ap.add_argument("--ref-vh", required=True)
    ap.add_argument("--ref-vl", required=True)
    ap.add_argument("--ref-name", default="reference")
    ap.add_argument("--lead", required=True, help="lead back-mutated construct key")
    ap.add_argument("--graft", required=True, help="matching naive-graft key")
    ap.add_argument("--scheme", default=DEFAULT_SCHEME)
    ap.add_argument("--canonical", help="comma-separated known back-mut positions "
                    "(e.g. H71,H73,H78,H93,L66)")
    ap.add_argument("--json", help="write scores + tables JSON here")
    args = ap.parse_args()

    with open(args.constructs) as f:
        raw = json.load(f)
    constructs = raw.get("constructs", raw)
    constructs = {k: {"VH": v.get("VH"), "VL": v.get("VL"),
                      "label": v.get("label", "")} for k, v in constructs.items()}
    canonical = args.canonical.split(",") if args.canonical else None

    res = benchmark(constructs, args.ref_vh, args.ref_vl, args.lead, args.graft,
                    scheme=args.scheme, canonical=canonical, ref_name=args.ref_name)

    print(f"=== (A) Identity vs {args.ref_name} ===")
    print(res["identity"].to_string(index=False))
    print(f"\n=== (B) Back-mutation concordance ({args.lead}) ===")
    print(res["concordance"].to_string(index=False))
    print("\n=== (C) Scores ===")
    for k, v in res["scores"].items():
        print(f"  {k}: {v}")

    if args.json:
        out = {"scores": res["scores"],
               "identity": res["identity"].to_dict("records"),
               "concordance": res["concordance"].to_dict("records")}
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
