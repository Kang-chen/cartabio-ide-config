#!/usr/bin/env python3
"""
fetch_civic.py — Download/cache CIViC nightly bulk TSVs and extract clinical
evidence for one gene.

CIViC (Clinical Interpretation of Variants in Cancer) publishes three nightly
TSVs that together give variant -> molecular profile -> clinical evidence:
  - nightly-VariantSummaries.tsv          (variant metadata; has clinvar_ids)
  - nightly-MolecularProfileSummaries.tsv (profiles; single- and multi-variant)
  - nightly-ClinicalEvidenceSummaries.tsv (evidence items; the actionability)

We keep SINGLE-variant molecular profiles as the primary per-allele evidence set
(complex/co-occurring profiles are tabulated separately and not attributed to a
single allele, to avoid over-crediting an allele with combination evidence).

Output:
  <gene>_civic_variants.csv          gene's CIViC variants (+ clinvar_ids bridge)
  <gene>_civic_evidence_long.csv     one row per evidence item for the gene

Usage:
    python fetch_civic.py --gene EGFR --outdir /mnt/results/EGFR_allelic_series
    # --cache-dir defaults to /workspace/civic_cache (TSVs are ~tens of MB)

Endpoints/columns verified against a live EGFR run (see references/data_sources.md).
"""
import argparse
import os
import sys
import time

import pandas as pd
import requests

CIVIC_BASE = "https://civicdb.org/downloads/nightly"
FILES = {
    "variants": "nightly-VariantSummaries.tsv",
    "profiles": "nightly-MolecularProfileSummaries.tsv",
    "evidence": "nightly-ClinicalEvidenceSummaries.tsv",
}

# CIViC evidence level letter -> rank (A strongest). Used downstream for tiering.
LEVEL_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}


def download_tsvs(cache_dir, max_age_hours=24):
    """Download the three nightly TSVs into cache_dir unless a fresh copy exists."""
    os.makedirs(cache_dir, exist_ok=True)
    paths = {}
    for key, fname in FILES.items():
        dest = os.path.join(cache_dir, fname)
        fresh = (
            os.path.exists(dest)
            and (time.time() - os.path.getmtime(dest)) < max_age_hours * 3600
            and os.path.getsize(dest) > 1000
        )
        if fresh:
            print(f"[civic] using cached {fname} ({os.path.getsize(dest)//1024} KB)")
        else:
            url = f"{CIVIC_BASE}/{fname}"
            print(f"[civic] downloading {url}")
            r = requests.get(url, timeout=180)
            r.raise_for_status()
            with open(dest, "wb") as fh:
                fh.write(r.content)
            print(f"[civic] saved {fname} ({len(r.content)//1024} KB)")
        paths[key] = dest
    return paths


def _read_tsv(path):
    return pd.read_csv(path, sep="\t", dtype=str).fillna("")


def extract_gene(gene, paths, outdir):
    os.makedirs(outdir, exist_ok=True)
    variants = _read_tsv(paths["variants"])
    profiles = _read_tsv(paths["profiles"])
    evidence = _read_tsv(paths["evidence"])

    # --- gene's variants -------------------------------------------------
    gcol = "gene" if "gene" in variants.columns else (
        "entrez_gene" if "entrez_gene" in variants.columns else None
    )
    if gcol is None:
        raise RuntimeError(f"Could not find a gene column in VariantSummaries; "
                           f"columns = {list(variants.columns)[:15]}")
    gvar = variants[variants[gcol].str.upper() == gene.upper()].copy()
    print(f"[civic] {len(gvar)} CIViC variants for {gene}")

    # variant_id column name is 'variant_id'
    vid_col = "variant_id" if "variant_id" in gvar.columns else "id"
    gvar_ids = set(gvar[vid_col].astype(str))

    # --- molecular profiles that involve the gene's variants -------------
    # Profiles list their variant ids; single-variant profiles are the primary set.
    prof_vcol = None
    for cand in ("variant_ids", "variant_id", "variants"):
        if cand in profiles.columns:
            prof_vcol = cand
            break
    mp_id_col = "molecular_profile_id" if "molecular_profile_id" in profiles.columns else (
        "id" if "id" in profiles.columns else None
    )

    def profile_variant_list(cell):
        # Cells may be comma/space/semicolon separated ids.
        return [t for t in __import__("re").split(r"[,\s;]+", str(cell)) if t]

    single_profiles = {}   # mp_id -> variant_id (single-variant profiles only)
    gene_profiles = set()  # all mp_ids touching this gene
    if prof_vcol and mp_id_col:
        for _, row in profiles.iterrows():
            vids = profile_variant_list(row[prof_vcol])
            if not vids:
                continue
            if any(v in gvar_ids for v in vids):
                gene_profiles.add(str(row[mp_id_col]))
                if len(vids) == 1 and vids[0] in gvar_ids:
                    single_profiles[str(row[mp_id_col])] = vids[0]

    # --- evidence for those profiles -------------------------------------
    ev_mp_col = None
    for cand in ("molecular_profile_id", "molecular_profile", "mp_id"):
        if cand in evidence.columns:
            ev_mp_col = cand
            break
    if ev_mp_col == "molecular_profile":
        # Some dumps carry the profile NAME; fall back to id if present.
        if "molecular_profile_id" in evidence.columns:
            ev_mp_col = "molecular_profile_id"

    gene_ev = evidence[evidence[ev_mp_col].astype(str).isin(gene_profiles)].copy()

    # Map single-variant profiles back to their variant id and merge names.
    gene_ev["single_variant_id"] = gene_ev[ev_mp_col].astype(str).map(single_profiles)
    id2name = dict(zip(gvar[vid_col].astype(str), gvar.get("variant", gvar.get("name", ""))))
    gene_ev["variant"] = gene_ev["single_variant_id"].map(id2name)
    # Rows without a single-variant mapping are complex-profile evidence.
    gene_ev["is_single_variant_evidence"] = gene_ev["single_variant_id"].notna()

    print(f"[civic] {len(gene_ev)} evidence items across {len(gene_profiles)} profiles "
          f"({gene_ev['is_single_variant_evidence'].sum()} single-variant)")

    # --- write outputs ---------------------------------------------------
    gvar_out = os.path.join(outdir, f"{gene}_civic_variants.csv")
    gvar.to_csv(gvar_out, index=False)
    ev_out = os.path.join(outdir, f"{gene}_civic_evidence_long.csv")
    gene_ev.to_csv(ev_out, index=False)
    print(f"[civic] wrote {gvar_out} and {ev_out}")
    return gvar, gene_ev


def fetch(gene, outdir, cache_dir):
    paths = download_tsvs(cache_dir)
    return extract_gene(gene, paths, outdir)


def main():
    ap = argparse.ArgumentParser(description="Fetch CIViC evidence for a gene.")
    ap.add_argument("--gene", required=True, help="HGNC gene symbol, e.g. EGFR")
    ap.add_argument("--outdir", required=True, help="Output directory")
    ap.add_argument("--cache-dir", default="/workspace/civic_cache",
                    help="Where to cache the nightly TSVs")
    args = ap.parse_args()
    fetch(args.gene.upper(), args.outdir, args.cache_dir)


if __name__ == "__main__":
    sys.exit(main())
