#!/usr/bin/env python3
"""
detect_build_liftover.py -- Detect the genome build of a tidy summary-statistics table and,
                            if it is GRCh37/hg19, liftover coordinates to GRCh38.

Build detection strategy (no network required):
  * A small panel of "anchor" SNPs with known, well-separated GRCh37 vs GRCh38 positions is
    matched by rsID (if an rsid/snp column looks like rs#) or by chr:pos proximity. Whichever
    build the observed positions agree with wins. Ties / no anchors -> report 'unknown' and,
    unless --assume-build is given, treat as GRCh38 (no-op) with a warning.
  * GWAS Catalog *harmonised* files are already GRCh38, so this is a no-op check for them.

Liftover (only when detected/assumed build is GRCh37/hg19):
  * Uses pyliftover with the hg19->hg38 chain (downloaded by pyliftover on first use, or
    supply --chain for an offline chain file). Rows that fail to lift are dropped and counted.

Usage:
  python detect_build_liftover.py --input tidy.tsv --out tidy_b38.tsv --report build.json \
      [--assume-build {GRCh37,GRCh38}] [--chain hg19ToHg38.over.chain.gz]

Input tidy table is expected to have (from ingest_sumstats.py): chr/pos columns and,
optionally, varid (chr:pos:ref:alt), effect_allele, other_allele. Column names are detected
leniently. Output preserves all columns, updates pos (and varid if present) to GRCh38.
"""
import argparse
import json
import re
import sys

import pandas as pd


def log(*a):
    print("[detect_build_liftover]", *a, file=sys.stderr, flush=True)


# Anchor SNPs: rsID -> {"chr":.., "GRCh37":pos37, "GRCh38":pos38}. Chosen on different chromosomes
# with a large 37/38 offset so a handful of matches disambiguates the build unambiguously.
ANCHORS = {
    "rs4477212":  {"chr": "1", "GRCh37": 82154,     "GRCh38": 82154},      # near-identical (skip if equal)
    "rs2185539":  {"chr": "1", "GRCh37": 1018704,   "GRCh38": 1082207},
    "rs12564807": {"chr": "1", "GRCh37": 734462,    "GRCh38": 799051},
    "rs3131972":  {"chr": "1", "GRCh37": 752721,    "GRCh38": 817341},
    "rs340874":   {"chr": "1", "GRCh37": 214159256, "GRCh38": 213985913},  # PROX1 anchor (this workflow)
    "rs7903146":  {"chr": "10", "GRCh37": 114758349, "GRCh38": 112998590}, # TCF7L2 (T2D)
    "rs1801282":  {"chr": "3", "GRCh37": 12393125,  "GRCh38": 12351626},   # PPARG
}


def find_col(cols, names):
    low = {c.lower(): c for c in cols}
    for n in names:
        if n in low:
            return low[n]
    return None


def detect_build(df, chrom_col, pos_col, id_col):
    votes = {"GRCh37": 0, "GRCh38": 0}
    checked = 0
    if id_col is not None:
        idx = {str(v): p for v, p in zip(df[id_col].astype(str), df[pos_col])}
        for rs, a in ANCHORS.items():
            if a["GRCh37"] == a["GRCh38"]:
                continue
            if rs in idx:
                try:
                    pos = int(idx[rs])
                except (ValueError, TypeError):
                    continue
                checked += 1
                if pos == a["GRCh37"]:
                    votes["GRCh37"] += 1
                elif pos == a["GRCh38"]:
                    votes["GRCh38"] += 1
    # fallback: match by chr:pos proximity (rare; only if no id hits)
    if checked == 0 and chrom_col is not None:
        pos_by_chr = {}
        for c, p in zip(df[chrom_col].astype(str).str.replace("chr", "", case=False), df[pos_col]):
            try:
                pos_by_chr.setdefault(c, set()).add(int(p))
            except (ValueError, TypeError):
                pass
        for rs, a in ANCHORS.items():
            if a["GRCh37"] == a["GRCh38"]:
                continue
            s = pos_by_chr.get(a["chr"], set())
            if a["GRCh37"] in s:
                votes["GRCh37"] += 1; checked += 1
            if a["GRCh38"] in s:
                votes["GRCh38"] += 1; checked += 1
    if checked == 0:
        return "unknown", votes, checked
    build = "GRCh37" if votes["GRCh37"] > votes["GRCh38"] else ("GRCh38" if votes["GRCh38"] > votes["GRCh37"] else "unknown")
    return build, votes, checked


def main():
    ap = argparse.ArgumentParser(description="Genome-build detection + hg19->GRCh38 liftover.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--assume-build", choices=["GRCh37", "GRCh38"], default=None,
                    help="force build label, skip detection")
    ap.add_argument("--chain", default=None, help="offline hg19->hg38 chain (else pyliftover downloads)")
    ap.add_argument("--sep", default="\t")
    args = ap.parse_args()

    df = pd.read_csv(args.input, sep=args.sep, dtype=str)
    cols = list(df.columns)
    chrom_col = find_col(cols, ["chr", "chrom", "chromosome", "hm_chrom", "#chrom"])
    pos_col = find_col(cols, ["pos", "position", "bp", "base_pair_location", "hm_pos"])
    id_col = find_col(cols, ["snp", "rsid", "variant_id", "hm_rsid", "id", "marker"])
    varid_col = find_col(cols, ["varid", "hm_variant_id"])

    rep = {"detected_build": None, "assumed": bool(args.assume_build), "votes": {},
           "anchors_checked": 0, "liftover_applied": False, "n_in": len(df),
           "n_lifted": 0, "n_dropped_liftover": 0, "chrom_col": chrom_col, "pos_col": pos_col}

    if pos_col is None:
        log("ERROR: could not find a position column; expected one of pos/position/base_pair_location.")
        sys.exit(2)

    if args.assume_build:
        build = args.assume_build
        rep["detected_build"] = build
    else:
        build, votes, checked = detect_build(df, chrom_col, pos_col, id_col)
        rep["detected_build"], rep["votes"], rep["anchors_checked"] = build, votes, checked
        if build == "unknown":
            log("WARNING: could not confirm build from anchors; assuming GRCh38 (no liftover). "
                "Pass --assume-build to override.")
            build = "GRCh38"

    if build == "GRCh38":
        df.to_csv(args.out, sep="\t", index=False)
        with open(args.report, "w") as fh:
            json.dump(rep, fh, indent=2)
        print(f"\u2713 Build {rep['detected_build']} confirmed, no liftover "
              f"(anchors matched: {rep['anchors_checked']}). Wrote {len(df)} rows.")
        return

    # ---- liftover GRCh37/hg19 -> GRCh38 ----
    try:
        from pyliftover import LiftOver
    except ImportError:
        log("ERROR: pyliftover not installed and build is GRCh37. `uv pip install pyliftover`.")
        sys.exit(2)
    lo = LiftOver(args.chain) if args.chain else LiftOver("hg19", "hg38")

    if chrom_col is None:
        log("ERROR: liftover needs a chromosome column but none was found.")
        sys.exit(2)

    new_pos, keep = [], []
    for c, p in zip(df[chrom_col].astype(str), df[pos_col]):
        cc = c if c.lower().startswith("chr") else "chr" + c
        try:
            p0 = int(p)
        except (ValueError, TypeError):
            keep.append(False); new_pos.append(None); continue
        res = lo.convert_coordinate(cc, p0 - 1)  # pyliftover is 0-based
        if res:
            new_pos.append(res[0][1] + 1)
            keep.append(True)
        else:
            new_pos.append(None)
            keep.append(False)

    df["_newpos"] = new_pos
    df["_keep"] = keep
    n_drop = int((~df["_keep"]).sum())
    df = df[df["_keep"]].copy()
    df[pos_col] = df["_newpos"].astype(int).astype(str)

    # rebuild varid if present (chr:pos:ref:alt)
    if varid_col is not None:
        ea = find_col(cols, ["effect_allele", "hm_effect_allele", "alt"])
        oa = find_col(cols, ["other_allele", "hm_other_allele", "ref"])
        if ea and oa:
            chrclean = df[chrom_col].astype(str).str.replace("chr", "", case=False)
            df[varid_col] = (chrclean + ":" + df[pos_col].astype(str) + ":" +
                             df[oa].astype(str).str.upper() + ":" + df[ea].astype(str).str.upper())
    df = df.drop(columns=["_newpos", "_keep"])
    df.to_csv(args.out, sep="\t", index=False)

    rep["liftover_applied"] = True
    rep["n_lifted"] = len(df)
    rep["n_dropped_liftover"] = n_drop
    with open(args.report, "w") as fh:
        json.dump(rep, fh, indent=2)
    print(f"\u2713 Build {rep['detected_build']} -> liftover hg19->GRCh38 applied: "
          f"{len(df)} lifted, {n_drop} dropped.")


if __name__ == "__main__":
    main()
