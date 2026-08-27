#!/usr/bin/env python3
"""
ingest_sumstats.py -- Ingest a summary-statistics file and emit a tidy, canonical table with
                      an explicit, printed column mapping you can confirm.

Auto-detects canonical columns from common aliases (prefers GWAS Catalog *harmonised* `hm_*`
columns when present, since those are already GRCh38 + strand-resolved). Override or confirm any
mapping with --col-map (JSON canonical->original). Derives beta from odds ratio when only OR is
present (beta = ln(OR)); derives se from a 95% CI when se is absent; converts -log10(p) with
--neglog10p.

Canonical output columns (tab-separated):
  snp, varid, chr, pos, effect_allele, other_allele, beta, se, pval, eaf, maf, n

  * snp    = rsID when available, else chr:pos:ref:alt
  * varid  = chr:pos:ref:alt (other_allele=ref, effect_allele=alt), always emitted when possible
  * effect_allele is the allele the beta/OR refers to.

Usage:
  python ingest_sumstats.py --input FILE --out tidy.tsv --report map.json \
      [--type {quant,cc}] [--col-map '{"beta":"EFFECT"}'] [--sep '\t'] [--neglog10p]
"""
import argparse
import json
import math
import sys

import numpy as np
import pandas as pd


def log(*a):
    print("[ingest_sumstats]", *a, file=sys.stderr, flush=True)


# canonical -> ordered list of accepted source names (lowercased). hm_* first = prefer harmonised.
ALIASES = {
    "snp":            ["hm_rsid", "rsid", "rs_id", "snp", "snpid", "variant", "marker", "markername", "id"],
    "varid":          ["hm_variant_id", "variant_id", "varid"],
    "chr":            ["hm_chrom", "chr", "chrom", "chromosome", "#chrom", "#chr"],
    "pos":            ["hm_pos", "pos", "position", "bp", "base_pair_location", "bp_location"],
    "effect_allele":  ["hm_effect_allele", "effect_allele", "ea", "alt", "allele1", "a1", "tested_allele", "assessed_allele"],
    "other_allele":   ["hm_other_allele", "other_allele", "oa", "ref", "allele0", "allele2", "a2", "nea", "non_effect_allele"],
    "beta":           ["hm_beta", "beta", "effect", "effsize", "b", "logor", "log_or"],
    "odds_ratio":     ["hm_odds_ratio", "odds_ratio", "or", "oddsratio"],
    "se":             ["standard_error", "se", "stderr", "std_err", "sebeta", "se_beta"],
    "pval":           ["p_value", "pval", "p", "pvalue", "p-value", "p_bolt_lmm", "frequentist_add_pvalue"],
    "eaf":            ["hm_effect_allele_frequency", "effect_allele_frequency", "eaf", "af", "freq", "a1freq", "maf_effect"],
    "maf":            ["minor_allele_frequency", "maf"],
    "n":              ["n", "n_total", "samplesize", "sample_size", "total_n", "neff"],
    "ci_lower":       ["hm_ci_lower", "ci_lower", "ci_low", "l95", "lower_ci"],
    "ci_upper":       ["hm_ci_upper", "ci_upper", "ci_high", "u95", "upper_ci"],
}


def autodetect(cols):
    low = {c.lower(): c for c in cols}
    mapping = {}
    for canon, names in ALIASES.items():
        for n in names:
            if n in low:
                mapping[canon] = low[n]
                break
    return mapping


def main():
    ap = argparse.ArgumentParser(description="Summary-stat ingestion + auto column mapping.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--type", choices=["quant", "cc"], default=None,
                    help="trait type; recorded in report (not required for ingestion)")
    ap.add_argument("--col-map", default=None,
                    help="JSON dict overriding/confirming detected columns (canonical->original). Merged over auto-detection.")
    ap.add_argument("--sep", default="\t")
    ap.add_argument("--neglog10p", action="store_true", help="treat the pval column as -log10(p) and convert")
    args = ap.parse_args()

    df = pd.read_csv(args.input, sep=args.sep, dtype=str, low_memory=False)
    mapping = autodetect(df.columns)
    if args.col_map:
        override = json.loads(args.col_map)
        mapping.update(override)
    log("column mapping (canonical -> source): " + json.dumps(mapping))

    def col(canon):
        src = mapping.get(canon)
        return df[src] if src is not None and src in df.columns else None

    out = pd.DataFrame()
    # ids / coords
    out["snp"] = col("snp")
    out["chr"] = col("chr")
    out["pos"] = col("pos")
    out["effect_allele"] = col("effect_allele")
    out["other_allele"] = col("other_allele")
    if out["effect_allele"] is not None:
        out["effect_allele"] = out["effect_allele"].astype(str).str.upper()
    if out["other_allele"] is not None:
        out["other_allele"] = out["other_allele"].astype(str).str.upper()

    # beta: native beta, else ln(OR)
    beta = col("beta")
    if beta is None:
        orr = col("odds_ratio")
        if orr is not None:
            beta = pd.to_numeric(orr, errors="coerce").map(lambda x: math.log(x) if (x is not None and x > 0) else np.nan)
            log("derived beta = ln(odds_ratio)")
    else:
        beta = pd.to_numeric(beta, errors="coerce")
    out["beta"] = beta

    # se: native, else from 95% CI on OR (ln scale) or on beta
    se = col("se")
    if se is not None:
        se = pd.to_numeric(se, errors="coerce")
    else:
        lo, hi = col("ci_lower"), col("ci_upper")
        if lo is not None and hi is not None:
            lo = pd.to_numeric(lo, errors="coerce"); hi = pd.to_numeric(hi, errors="coerce")
            orr = col("odds_ratio")
            if orr is not None:
                se = (np.log(hi) - np.log(lo)) / (2 * 1.959964)
                log("derived se from 95% CI of odds ratio")
            else:
                se = (hi - lo) / (2 * 1.959964)
                log("derived se from 95% CI of beta")
    out["se"] = se

    # pval
    pv = col("pval")
    if pv is not None:
        pv = pd.to_numeric(pv, errors="coerce")
        if args.neglog10p:
            pv = np.power(10.0, -pv)
            log("converted pval from -log10(p)")
    out["pval"] = pv

    # freqs
    eaf = col("eaf")
    out["eaf"] = pd.to_numeric(eaf, errors="coerce") if eaf is not None else np.nan
    maf = col("maf")
    if maf is not None:
        out["maf"] = pd.to_numeric(maf, errors="coerce")
    else:
        out["maf"] = out["eaf"].map(lambda x: min(x, 1 - x) if pd.notna(x) else np.nan)

    # n
    nn = col("n")
    out["n"] = pd.to_numeric(nn, errors="coerce") if nn is not None else np.nan

    # varid = chr:pos:other:effect  (ref=other, alt=effect)
    vmap = mapping.get("varid")
    if vmap is not None and vmap in df.columns:
        out["varid"] = df[vmap].astype(str).str.replace("^chr", "", regex=True)
    elif out["chr"] is not None and out["pos"] is not None and out["effect_allele"] is not None and out["other_allele"] is not None:
        chrclean = out["chr"].astype(str).str.replace("^chr", "", regex=True, case=False)
        out["varid"] = (chrclean + ":" + out["pos"].astype(str) + ":" +
                        out["other_allele"].astype(str) + ":" + out["effect_allele"].astype(str))
    else:
        out["varid"] = np.nan

    # snp fallback to varid when rsid missing
    if out["snp"] is None:
        out["snp"] = out["varid"]
    else:
        out["snp"] = out["snp"].where(out["snp"].notna() & (out["snp"].astype(str) != "") & (out["snp"].astype(str).str.lower() != "nan"),
                                      out["varid"])

    ordered = ["snp", "varid", "chr", "pos", "effect_allele", "other_allele", "beta", "se", "pval", "eaf", "maf", "n"]
    out = out[[c for c in ordered if c in out.columns]]
    out.to_csv(args.out, sep="\t", index=False)

    rep = {
        "n_rows": int(len(out)),
        "trait_type": args.type,
        "column_mapping": mapping,
        "derived": {
            "beta_from_or": bool(mapping.get("beta") is None and mapping.get("odds_ratio") is not None),
            "se_from_ci": bool(mapping.get("se") is None and mapping.get("ci_lower") is not None),
            "pval_from_neglog10": bool(args.neglog10p),
        },
        "missing_canonical": [c for c in ["snp", "chr", "pos", "effect_allele", "other_allele", "beta", "se", "pval"]
                              if c not in mapping],
    }
    with open(args.report, "w") as fh:
        json.dump(rep, fh, indent=2)
    print(f"\u2713 Ingestion complete: {len(out)} rows. "
          f"Mapping: {json.dumps({k: mapping[k] for k in mapping})}")
    if rep["missing_canonical"]:
        log("WARNING: unmapped canonical columns: " + ", ".join(rep["missing_canonical"]) +
            " -- pass --col-map to set them.")


if __name__ == "__main__":
    main()
