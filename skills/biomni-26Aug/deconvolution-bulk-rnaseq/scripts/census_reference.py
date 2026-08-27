#!/usr/bin/env python3
"""census_reference.py -- Build a single-cell reference from the CELLxGENE Census.

OPTIONAL FALLBACK. The preferred way to build a reference is to pull a curated
scRNA-seq dataset (10x H5, an .h5ad you already have, or a Seurat .rds) and feed
it straight into ``scripts/load_data.R``'s ``load_reference()``. This
self-contained script pulls a harmonized, multi-dataset reference for a tissue
(optionally filtered to disease terms) in one step and writes an .h5ad that
scripts/load_data.R reads via load_reference().

It writes:
  <out_dir>/<name>_reference.h5ad   - raw counts; obs has cell_type + donor_id
  <out_dir>/<name>_signature.csv    - per-cell-type mean expression (genes x type)

The .h5ad uses gene SYMBOLS as var_names (matched against the bulk gene IDs).
Census stores raw counts (non-log) which is what BayesPrism/DWLS/MuSiC expect.

Example:
  python scripts/census_reference.py --tissue-general "blood" --name pbmc
  python scripts/census_reference.py --tissue-general "skin of body" \
      --disease psoriasis "psoriasis vulgaris" normal --name psoriasis_skin
"""
from __future__ import annotations

import argparse
import os
import sys


def build_census_reference(
    out_dir: str = "data",
    tissue_general: str = "blood",
    disease_terms=None,
    organism: str = "Homo sapiens",
    census_version: str = "stable",
    max_cells_per_type: int = 2000,
    min_genes: int = 200,
    name: str = "reference",
):
    """Pull cells for a tissue (optionally restricted to ``disease_terms``) from
    the Census, QC, subsample per cell type, and write the reference .h5ad +
    a per-cell-type signature CSV. Returns (h5ad_path, signature_csv_path)."""
    import cellxgene_census  # noqa: F401
    import numpy as np
    import pandas as pd
    import scanpy as sc

    os.makedirs(out_dir, exist_ok=True)

    obs_filter = f"tissue_general == '{tissue_general}' and is_primary_data == True"
    if disease_terms:
        disease_list = "[" + ", ".join(f"'{d}'" for d in disease_terms) + "]"
        obs_filter += f" and disease in {disease_list}"
    want_cols = ["cell_type", "disease", "donor_id", "assay", "tissue", "dataset_id"]

    print(f"[census] opening census '{census_version}'", flush=True)
    print(f"[census] filter: {obs_filter}", flush=True)
    census = cellxgene_census.open_soma(census_version=census_version)
    try:
        # cellxgene_census.get_anndata column kwarg has shifted across versions;
        # try the three known spellings, then fall back to pulling all obs cols.
        adata = None
        for kwargs in (
            {"obs_column_names": want_cols},
            {"column_names": {"obs": want_cols}},
            {},
        ):
            try:
                adata = cellxgene_census.get_anndata(
                    census,
                    organism=organism,
                    measurement_name="RNA",
                    X_name="raw",  # raw counts -> methods want non-log counts
                    obs_value_filter=obs_filter,
                    **kwargs,
                )
                break
            except TypeError:
                continue
        if adata is None:
            raise RuntimeError("cellxgene_census.get_anndata failed for all kwarg variants")
    finally:
        census.close()

    print(f"[census] pulled {adata.n_obs} cells x {adata.n_vars} genes", flush=True)
    if adata.n_obs == 0:
        raise RuntimeError("No cells matched the filter -- check tissue/disease terms.")

    # Use gene symbols (feature_name) as var_names so they match bulk gene IDs.
    if "feature_name" in adata.var.columns:
        adata.var["ensembl_id"] = adata.var_names
        adata.var_names = adata.var["feature_name"].astype(str)
        adata.var_names_make_unique()

    # Light QC (Census is pre-QC'd; this drops near-empty cells).
    sc.pp.filter_cells(adata, min_genes=min_genes)

    # Balanced, tractable reference: subsample per cell type.
    if max_cells_per_type and adata.n_obs > 0:
        rng = np.random.default_rng(0)
        keep = []
        for _ct, idx in adata.obs.groupby("cell_type", observed=True).indices.items():
            idx = np.asarray(idx)
            if idx.size > max_cells_per_type:
                idx = rng.choice(idx, size=max_cells_per_type, replace=False)
            keep.extend(idx.tolist())
        adata = adata[sorted(keep)].copy()
        print(f"[census] subsampled to {adata.n_obs} cells "
              f"(<= {max_cells_per_type}/cell type)", flush=True)

    # Per-cell-type mean-expression signature (genes x cell_type), for QC.
    X = adata.X
    dense = X.toarray() if hasattr(X, "toarray") else np.asarray(X)
    df = pd.DataFrame(dense, columns=adata.var_names)
    df["cell_type"] = adata.obs["cell_type"].values
    signature = df.groupby("cell_type").mean().T

    sig_path = os.path.join(out_dir, f"{name}_signature.csv")
    signature.to_csv(sig_path)
    h5ad_path = os.path.join(out_dir, f"{name}_reference.h5ad")
    adata.write_h5ad(h5ad_path)

    print(f"[census] wrote {h5ad_path}", flush=True)
    print(f"[census] wrote {sig_path}", flush=True)
    print("[census] cell-type counts:\n"
          + adata.obs["cell_type"].value_counts().to_string(), flush=True)
    return h5ad_path, sig_path


def build_parser():
    ap = argparse.ArgumentParser(
        description="Build a CELLxGENE Census single-cell reference (.h5ad) for deconvolution.")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--name", default="reference")
    ap.add_argument("--tissue-general", default="blood",
                    help="Census tissue_general value, e.g. 'blood', 'skin of body', 'lung'")
    ap.add_argument("--disease", nargs="*", default=None,
                    help="Optional disease terms to restrict to (space-separated). "
                         "Omit for all diseases.")
    ap.add_argument("--organism", default="Homo sapiens")
    ap.add_argument("--census-version", default="stable")
    ap.add_argument("--max-cells-per-type", type=int, default=2000)
    ap.add_argument("--min-genes", type=int, default=200)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    build_census_reference(
        out_dir=args.out_dir,
        tissue_general=args.tissue_general,
        disease_terms=args.disease,
        organism=args.organism,
        census_version=args.census_version,
        max_cells_per_type=args.max_cells_per_type,
        min_genes=args.min_genes,
        name=args.name,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
