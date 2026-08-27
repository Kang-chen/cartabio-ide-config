#!/usr/bin/env python3
"""
fetch_qtl.py -- Fetch QTL summary statistics for ONE gene (or all genes) in ONE region from a
                multi-source catalog, and write a tidy table (GRCh38) + provenance JSON whose
                columns match ingest_sumstats.py so downstream tools treat it like any sumstat.

Sources (--source):
  eqtl_catalogue   (default) eQTL Catalogue uniformly-reprocessed datasets (GRCh38). Region is
                   fetched via the dataset's tabix-indexed FTP sumstats file
                   (.../sumstats/{QTS}/{QTD}/{QTD}.all.tsv.gz); the QTS study id is resolved from
                   the QTD dataset id through the eQTL Catalogue API. --list-datasets prints the
                   dataset table (id, study, tissue, quant, N).
  gtex             GTEx v8 -- routed THROUGH eQTL Catalogue's reprocessed GTEx datasets (find the
                   matching QTD via --list-datasets), so it behaves identically. (Use --raw-gtex-file
                   for a locally downloaded per-tissue file instead; those are hg19 -> lift after.)
  eqtlgen          eQTLGen cis-eQTL (whole blood). Ships Z-scores (hg19); pass --eqtlgen-file (+
                   --eqtlgen-af-file for MAF); beta/se are derived from Z+MAF+N (Zhu et al. 2016)
                   and coordinates are hg19 (lift with detect_build_liftover.py afterwards).

Standardized output columns (match ingest_sumstats.py):
  snp, chr, pos, effect_allele, other_allele, beta, se, pval, eaf, maf, n, z, varid, gene_id, qtl_id
  * effect_allele = ALT (eQTL Catalogue / GTEx), AssessedAllele (eQTLGen).
  * snp = rsid when available, else chr:pos:ref:alt. varid = chr:pos:ref:alt always emitted.
  * Multiallelic / duplicate rows collapsed to one record per (gene, chr:pos:ref:alt).

Usage:
  python fetch_qtl.py --source eqtl_catalogue --dataset-id QTD000554 \
      --gene-id ENSG00000230461 --region 1:213400000-214600000 \
      --out qtl.tsv --report qtl.json
  python fetch_qtl.py --list-datasets           # discover dataset ids / tissues
"""
import argparse
import io
import json
import subprocess
import sys

import numpy as np
import pandas as pd

try:
    import requests
except ImportError:
    requests = None

EQTL_API = "https://www.ebi.ac.uk/eqtl/api/v2"
EQTL_FTP = "https://ftp.ebi.ac.uk/pub/databases/spot/eQTL/sumstats"
UA = {"User-Agent": "fine-mapping-susie/1.0", "Accept": "application/json"}

# Raw tabix .all.tsv.gz column order (eQTL Catalogue sumstats spec; 19 positional fields,
# no header line). Field order verified empirically against the API record + a known anchor
# variant (rs340874/ENSG00000117707: maf=0.465812 pval=0.771828 beta=0.0236232 se=0.0812475).
EQTL_COLS = ["molecular_trait_id", "chromosome", "position", "ref", "alt", "variant",
             "ac", "maf", "pvalue", "beta", "se", "type", "an", "ac_alt", "r2",
             "molecular_trait_object_id", "gene_id", "median_tpm", "rsid"]


def log(*a):
    print("[fetch_qtl]", *a, file=sys.stderr, flush=True)


def api_get(url, params=None, timeout=60):
    if requests is None:
        raise RuntimeError("requests not installed")
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def list_datasets(timeout=60):
    rows, page, size = [], 1, 1000
    while True:
        try:
            js = api_get(f"{EQTL_API}/datasets", params={"size": size, "page": page}, timeout=timeout)
        except Exception as e:
            log(f"dataset listing failed on page {page}: {e}")
            break
        if not js:
            break
        rows.extend(js)
        if len(js) < size:
            break
        page += 1
    df = pd.DataFrame(rows)
    if df.empty:
        log("no datasets returned.")
        return
    keep = [c for c in ["dataset_id", "study_label", "tissue_label", "quant_method", "condition_label", "sample_size"] if c in df.columns]
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(df[keep].to_string(index=False))


def resolve_study(dataset_id, timeout=60):
    js = api_get(f"{EQTL_API}/datasets/{dataset_id}", timeout=timeout)
    return js.get("study_id"), js.get("sample_size"), js


def tabix_region(url, region, timeout=180):
    """Stream a region from a remote tabix-indexed bgzip file. Returns raw text (no header)."""
    cmd = ["tabix", url, region]
    log("tabix:", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"tabix failed for {region} on {url}:\n{r.stderr[:500]}")
    return r.stdout


def fetch_eqtl_catalogue(dataset_id, region, gene_ids, timeout):
    study_id, n, _ = resolve_study(dataset_id, timeout=timeout)
    if not study_id:
        raise RuntimeError(f"could not resolve study id for {dataset_id}")
    url = f"{EQTL_FTP}/{study_id}/{dataset_id}/{dataset_id}.all.tsv.gz"
    raw = tabix_region(url, region, timeout=timeout)
    if not raw.strip():
        log(f"no rows in {region} for {dataset_id}")
        return pd.DataFrame(), n
    df = pd.read_csv(io.StringIO(raw), sep="\t", header=None, names=EQTL_COLS, dtype=str)
    if gene_ids:
        want = set(gene_ids)
        # match on gene_id or molecular_trait_id (ge datasets: molecular_trait_id == gene_id)
        df = df[df["gene_id"].isin(want) | df["molecular_trait_id"].isin(want)]
    if df.empty:
        return df, n
    out = pd.DataFrame({
        "chr": df["chromosome"].astype(str).str.replace("^chr", "", regex=True),
        "pos": pd.to_numeric(df["position"], errors="coerce").astype("Int64"),
        "effect_allele": df["alt"].str.upper(),
        "other_allele": df["ref"].str.upper(),
        "beta": pd.to_numeric(df["beta"], errors="coerce"),
        "se": pd.to_numeric(df["se"], errors="coerce"),
        "pval": pd.to_numeric(df["pvalue"], errors="coerce"),
        "maf": pd.to_numeric(df["maf"], errors="coerce"),
        "gene_id": df["gene_id"].fillna(df["molecular_trait_id"]),
        "rsid": df["rsid"],
    })
    out["eaf"] = np.nan  # eQTL Catalogue reports maf, not effect-allele freq
    out["n"] = float(n) if n else np.nan
    out["z"] = out["beta"] / out["se"]
    out["varid"] = out["chr"] + ":" + out["pos"].astype(str) + ":" + out["other_allele"] + ":" + out["effect_allele"]
    out["snp"] = out["rsid"].where(out["rsid"].notna() & (out["rsid"].astype(str).str.startswith("rs")), out["varid"])
    out["qtl_id"] = dataset_id
    # collapse duplicate (gene, varid)
    out = out.sort_values("pval").drop_duplicates(subset=["gene_id", "varid"], keep="first")
    cols = ["snp", "chr", "pos", "effect_allele", "other_allele", "beta", "se", "pval",
            "eaf", "maf", "n", "z", "varid", "gene_id", "qtl_id"]
    return out[cols], n


def fetch_eqtlgen(region, gene_ids, eqtlgen_file, af_file, n_default, timeout):
    if not eqtlgen_file:
        raise RuntimeError("eQTLGen requires --eqtlgen-file (Z-score cis-eQTL file).")
    log("reading eQTLGen file (hg19; beta/se derived from Z+MAF+N; lift coords afterwards)")
    chrom, span = region.split(":")
    lo, hi = (int(x) for x in span.split("-"))
    use = pd.read_csv(eqtlgen_file, sep="\t", dtype=str)
    # eQTLGen columns: Pvalue, SNP, SNPChr, SNPPos, AssessedAllele, OtherAllele, Zscore, Gene, ...
    use = use[(use["SNPChr"].astype(str) == str(chrom).replace("chr", "")) &
              (pd.to_numeric(use["SNPPos"], errors="coerce").between(lo, hi))]
    if gene_ids:
        use = use[use["Gene"].isin(set(gene_ids))]
    if use.empty:
        return pd.DataFrame(), n_default
    af = None
    if af_file:
        af = pd.read_csv(af_file, sep="\t", dtype=str).set_index("SNP")["AlleleB_all"].to_dict()
    z = pd.to_numeric(use["Zscore"], errors="coerce")
    maf = use["SNP"].map(lambda s: float(af[s]) if af and s in af else np.nan) if af else pd.Series(np.nan, index=use.index)
    nn = pd.to_numeric(use.get("NrSamples", pd.Series(n_default, index=use.index)), errors="coerce").fillna(n_default)
    # Zhu 2016: se = 1/sqrt(2 p (1-p)(N + z^2)); beta = z * se
    p = maf.clip(lower=1e-6, upper=1 - 1e-6)
    se = 1.0 / np.sqrt(2 * p * (1 - p) * (nn + z ** 2))
    beta = z * se
    out = pd.DataFrame({
        "snp": use["SNP"], "chr": use["SNPChr"].astype(str), "pos": pd.to_numeric(use["SNPPos"], errors="coerce").astype("Int64"),
        "effect_allele": use["AssessedAllele"].str.upper(), "other_allele": use["OtherAllele"].str.upper(),
        "beta": beta, "se": se, "pval": pd.to_numeric(use["Pvalue"], errors="coerce"),
        "eaf": np.nan, "maf": maf, "n": nn, "z": z, "gene_id": use["Gene"], "qtl_id": "eQTLGen",
    })
    out["varid"] = out["chr"] + ":" + out["pos"].astype(str) + ":" + out["other_allele"] + ":" + out["effect_allele"]
    return out[["snp", "chr", "pos", "effect_allele", "other_allele", "beta", "se", "pval",
                "eaf", "maf", "n", "z", "varid", "gene_id", "qtl_id"]], n_default


def main():
    ap = argparse.ArgumentParser(description="Fetch QTL sumstats for one gene/region (coloc-ready).")
    ap.add_argument("--source", choices=["eqtl_catalogue", "gtex", "eqtlgen"], default="eqtl_catalogue")
    ap.add_argument("--gene-id", default=None, help="Ensembl gene id (ENSG...). Comma-separated ok. Omit for all genes in region.")
    ap.add_argument("--region", default=None, help="GRCh38 region chr:start-end (no 'chr' prefix needed)")
    ap.add_argument("--region-hg19", default=None, help="(eQTLGen) hg19 region chr:start-end")
    ap.add_argument("--dataset-id", default=None, help="eQTL Catalogue dataset id (QTD......)")
    ap.add_argument("--study", default=None, help="(optional) study id override (QTS......)")
    ap.add_argument("--tissue", default=None)
    ap.add_argument("--quant", default=None)
    ap.add_argument("--raw-gtex-file", default=None)
    ap.add_argument("--eqtlgen-file", default=None)
    ap.add_argument("--eqtlgen-af-file", default=None)
    ap.add_argument("--n", type=float, default=None, help="sample size fallback")
    ap.add_argument("--list-datasets", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    if args.list_datasets:
        list_datasets(timeout=args.timeout)
        return

    gene_ids = [g.strip() for g in args.gene_id.split(",")] if args.gene_id else None

    if args.source in ("eqtl_catalogue", "gtex"):
        if not args.dataset_id:
            log("ERROR: --dataset-id (QTD......) required for eqtl_catalogue/gtex. Use --list-datasets.")
            sys.exit(2)
        if not args.region:
            log("ERROR: --region chr:start-end required.")
            sys.exit(2)
        region = args.region.replace("chr", "")
        df, n = fetch_eqtl_catalogue(args.dataset_id, region, gene_ids, args.timeout)
        src_id = args.dataset_id
    else:  # eqtlgen
        region = (args.region_hg19 or args.region)
        if not region:
            log("ERROR: eQTLGen needs --region-hg19 (or --region) chr:start-end.")
            sys.exit(2)
        df, n = fetch_eqtlgen(region.replace("chr", ""), gene_ids, args.eqtlgen_file,
                              args.eqtlgen_af_file, args.n, args.timeout)
        src_id = "eQTLGen"

    if args.out:
        df.to_csv(args.out, sep="\t", index=False)
    rep = {"source": args.source, "dataset_or_id": src_id, "region": args.region,
           "gene_ids": gene_ids, "n_variants": int(len(df)),
           "n_samples": (float(n) if n else None),
           "genes_returned": sorted(df["gene_id"].dropna().unique().tolist()) if not df.empty else []}
    if args.report:
        with open(args.report, "w") as fh:
            json.dump(rep, fh, indent=2)
    print(f"\u2713 QTL fetch complete: {len(df)} variants" + (f" -> {args.out}" if args.out else "")
          + (f"  ({src_id}, N={int(n)})" if n else ""))


if __name__ == "__main__":
    main()
