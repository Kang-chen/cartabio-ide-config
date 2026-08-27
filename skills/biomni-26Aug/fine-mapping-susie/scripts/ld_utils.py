#!/usr/bin/env python3
"""
ld_utils.py -- Build a signed-r LD matrix from a PLINK2 genotype subset, with r oriented
               to the EFFECT allele of a harmonized summary-statistics table.

This is the second half of the LD step. `build_ld.sh` produces the region/ancestry genotype
subset (PLINK2 pgen/pvar/psam). This script:
  1. Runs `plink2 --pfile PREFIX --export A` -> additive dosage matrix (rows=samples,
     cols=variants). PLINK's .raw header encodes, per variant, which allele was COUNTED
     (the "_<ALLELE>" suffix on each column name). On some builds PLINK counts REF, on
     others ALT -- so we always read the counted allele from the header, never assume.
  2. Matches each harmonized variant to a panel variant by id (and, when ids differ, by
     chr:pos with allele-pair check), and flips the dosage sign when the counted allele is
     the harmonized OTHER allele, so every column is dosage of the EFFECT allele.
  3. Computes Pearson r across samples between every pair of retained variants -> a square,
     symmetric matrix whose sign is w.r.t. the effect allele (NOT r^2; negative r is kept).
  4. Writes the matrix (TSV, snp-id row/col names), the ordered snp list, and a JSON report.

Usage:
  python ld_utils.py --plink-prefix /workspace/ld_region \
      --harmonized harmonized_for_ld.tsv --id-col snp \
      --out-matrix ld.tsv --out-snps ld_snps.txt --report ld.json \
      [--min-callrate 0.98] [--plink2 plink2]

Harmonized table MUST have columns: <id-col> (default `snp`), varid (chr:pos:ref:alt),
effect_allele, other_allele. r is signed with respect to `effect_allele`.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd


def log(*a):
    print("[ld_utils]", *a, file=sys.stderr, flush=True)


def norm_allele(a):
    return str(a).strip().upper()


def parse_varid(v):
    """chr:pos:ref:alt (also tolerates chr_pos_ref_alt, leading 'chr'). Returns (chrom,pos,ref,alt) or (None,)*4."""
    if v is None:
        return (None, None, None, None)
    s = str(v).strip()
    if s.lower().startswith("chr"):
        s = s[3:]
    parts = s.replace("_", ":").split(":")
    if len(parts) >= 4:
        chrom, pos, ref, alt = parts[0], parts[1], parts[2], parts[3]
        try:
            pos = int(pos)
        except ValueError:
            return (None, None, None, None)
        return (chrom, pos, norm_allele(ref), norm_allele(alt))
    return (None, None, None, None)


def run_export_a(plink2, prefix, workdir):
    """plink2 --pfile PREFIX --export A -> .raw ; returns path to the .raw file."""
    out = os.path.join(workdir, "geno")
    cmd = [plink2, "--pfile", prefix, "--export", "A", "--out", out]
    log("running:", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log("plink2 --export A failed:\n" + r.stdout + "\n" + r.stderr)
        # Some builds name the subset .pgen but need --bfile; try a fallback with bfile.
        cmd2 = [plink2, "--bfile", prefix, "--export", "A", "--out", out]
        log("retry:", " ".join(cmd2))
        r2 = subprocess.run(cmd2, capture_output=True, text=True)
        if r2.returncode != 0:
            raise RuntimeError("plink2 --export A failed (pfile and bfile):\n" + r.stderr + "\n" + r2.stderr)
    raw = out + ".raw"
    if not os.path.exists(raw):
        raise RuntimeError(f"expected {raw} not produced by plink2 --export A")
    return raw


def read_raw(raw_path):
    """
    Read a PLINK2 .raw additive file.
    Fixed leading cols: FID IID PAT MAT SEX PHENOTYPE. Remaining cols are '<VARIANTID>_<COUNTED_ALLELE>'.
    Returns: dosage DataFrame (index=IID, columns=variant id), dict variant_id -> counted_allele.
    """
    df = pd.read_csv(raw_path, sep=r"\s+", engine="python")
    fixed = ["FID", "IID", "PAT", "MAT", "SEX", "PHENOTYPE"]
    fixed = [c for c in fixed if c in df.columns]
    geno_cols = [c for c in df.columns if c not in fixed]
    counted = {}
    rename = {}
    for c in geno_cols:
        # split off the trailing _<ALLELE>; variant id itself may contain ':' but not a trailing _ALLELE token we added
        if "_" in c:
            vid, allele = c.rsplit("_", 1)
        else:
            vid, allele = c, ""
        counted[vid] = norm_allele(allele)
        rename[c] = vid
    dos = df[geno_cols].rename(columns=rename)
    dos.index = df["IID"].values if "IID" in df.columns else np.arange(len(df))
    return dos, counted


def main():
    ap = argparse.ArgumentParser(description="Signed-r LD matrix oriented to the effect allele.")
    ap.add_argument("--plink-prefix", required=True, help="PLINK2 pgen/pvar/psam prefix from build_ld.sh")
    ap.add_argument("--harmonized", required=True, help="harmonized table (snp,varid,effect_allele,other_allele)")
    ap.add_argument("--id-col", default="snp", help="id column shared with the panel variant ids (default snp)")
    ap.add_argument("--out-matrix", required=True)
    ap.add_argument("--out-snps", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--min-callrate", type=float, default=0.98)
    ap.add_argument("--plink2", default="plink2")
    args = ap.parse_args()

    H = pd.read_csv(args.harmonized, sep="\t", dtype=str)
    for req in [args.id_col, "effect_allele", "other_allele"]:
        if req not in H.columns:
            log(f"ERROR: harmonized table missing required column '{req}'")
            sys.exit(2)
    H["effect_allele"] = H["effect_allele"].map(norm_allele)
    H["other_allele"] = H["other_allele"].map(norm_allele)
    has_varid = "varid" in H.columns
    n_in = len(H)

    workdir = tempfile.mkdtemp(prefix="ldutils_")
    raw = run_export_a(args.plink2, args.plink_prefix, workdir)
    dos, counted = read_raw(raw)
    n_samples = dos.shape[0]
    log(f"panel: {n_samples} samples, {dos.shape[1]} variants exported")

    # Build lookups from panel: by exact id, and by chr:pos (from the id if it is chr:pos:ref:alt).
    panel_ids = set(dos.columns)
    panel_by_pos = {}
    for vid in dos.columns:
        c, p, r_, a_ = parse_varid(vid)
        if c is not None:
            panel_by_pos.setdefault((c, p), []).append(vid)

    rep = dict(n_harmonized_in=n_in, matched=0, flipped_to_effect=0, dropped_not_in_panel=0,
               dropped_allele_mismatch=0, dropped_monomorphic=0, dropped_low_callrate=0,
               dropped_dup_harmonized=0, n_ambiguous_panel_pos=0)

    kept_ids = []      # harmonized id order
    kept_vectors = []  # dosage-of-effect-allele vectors aligned to samples
    seen_h = set()

    for _, row in H.iterrows():
        hid = row[args.id_col]
        if hid in seen_h:
            rep["dropped_dup_harmonized"] += 1
            continue
        ea, oa = row["effect_allele"], row["other_allele"]
        vid_match = None

        # 1. exact id match to a panel column
        if hid in panel_ids:
            vid_match = hid
        elif has_varid and row["varid"] in panel_ids:
            vid_match = row["varid"]
        else:
            # 2. by chr:pos from varid, with allele-pair check
            if has_varid:
                c, p, ref, alt = parse_varid(row["varid"])
                if c is not None and (c, p) in panel_by_pos:
                    cands = panel_by_pos[(c, p)]
                    if len(cands) > 1:
                        rep["n_ambiguous_panel_pos"] += 1
                    for cand in cands:
                        cc, cp, cref, calt = parse_varid(cand)
                        if {cref, calt} == {ea, oa}:
                            vid_match = cand
                            break

        if vid_match is None:
            rep["dropped_not_in_panel"] += 1
            continue

        ca = counted.get(vid_match, "")
        # counted allele must be one of {ea, oa} to orient
        if ca not in (ea, oa):
            # try to infer from panel id alleles if header allele uninformative
            _, _, cref, calt = parse_varid(vid_match)
            if ca == "" and {cref, calt} == {ea, oa}:
                # cannot know which was counted; skip to be safe
                rep["dropped_allele_mismatch"] += 1
                continue
            rep["dropped_allele_mismatch"] += 1
            continue

        v = pd.to_numeric(dos[vid_match], errors="coerce").astype(float).values
        callrate = np.mean(np.isfinite(v))
        if callrate < args.min_callrate:
            rep["dropped_low_callrate"] += 1
            continue
        # mean-impute missing
        if not np.all(np.isfinite(v)):
            m = np.nanmean(v)
            v = np.where(np.isfinite(v), v, m)
        if np.nanstd(v) == 0:
            rep["dropped_monomorphic"] += 1
            continue

        # orient: dosage of EFFECT allele. PLINK counts `ca`. If ca==oa, flip (2 - dosage).
        if ca == oa:
            v = 2.0 - v
            rep["flipped_to_effect"] += 1
        kept_ids.append(hid)
        kept_vectors.append(v)
        seen_h.add(hid)
        rep["matched"] += 1

    if len(kept_ids) < 2:
        log(f"ERROR: only {len(kept_ids)} variants matched panel; cannot build LD.")
        rep["n_samples"] = int(n_samples)
        rep["n_variants_out"] = len(kept_ids)
        with open(args.report, "w") as fh:
            json.dump(rep, fh, indent=2)
        sys.exit(3)

    M = np.vstack(kept_vectors)              # variants x samples
    R = np.corrcoef(M)                       # variants x variants, signed
    R = np.clip(R, -1.0, 1.0)
    np.fill_diagonal(R, 1.0)

    rdf = pd.DataFrame(R, index=kept_ids, columns=kept_ids)
    rdf.to_csv(args.out_matrix, sep="\t")
    with open(args.out_snps, "w") as fh:
        fh.write("\n".join(kept_ids) + "\n")

    rep["n_samples"] = int(n_samples)
    rep["n_variants_out"] = len(kept_ids)
    rep["r_range"] = [float(np.min(R)), float(np.max(R))]
    rep["has_negative_r"] = bool(np.any(R < 0))
    rep["sign_convention"] = "r signed w.r.t. effect_allele of the harmonized table"
    with open(args.report, "w") as fh:
        json.dump(rep, fh, indent=2)

    print(f"\u2713 LD matrix built: {len(kept_ids)} variants x {n_samples} samples "
          f"(matched {rep['matched']}, flipped {rep['flipped_to_effect']}, "
          f"neg r present={rep['has_negative_r']})")


if __name__ == "__main__":
    main()
