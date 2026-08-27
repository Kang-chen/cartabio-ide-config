#!/usr/bin/env python3
"""
Step 3 — Expression atlas: gene-panel expression across cell types from the CZ CELLxGENE Census.

Pulls an AnnData restricted to GENE_PANEL across TISSUES for the CONTROL_LABEL (normal reference)
using the NORMALIZED layer, then computes per-cell-type mean expression and % of cells expressing.
Restricting `var` to the panel keeps memory tiny even across millions of cells.

Generalized from a validated run (originally IL13/TSLP across lung+nose). Edit PARAMETERS only.

Output:
  /mnt/results/data/<panel_tag>_expression_by_celltype.csv
  (also writes the small AnnData to /workspace for optional reuse)
"""
import os
import cellxgene_census
import numpy as np
import pandas as pd
import scipy.sparse as sp

# ============================== PARAMETERS ==============================
GENE_PANEL     = ["GENE1", "GENE2"]        # e.g. ["IL13", "TSLP"]
TISSUES        = ["tissue_a"]              # e.g. ["lung", "nose"]
REFERENCE_DISEASE = "normal"               # disease label for the atlas reference (usually control)
ORGANISM       = "Homo sapiens"
CENSUS_VERSION = None                       # None -> latest stable; pin for reproducibility
MIN_CELLS_DISPLAY = 200                     # cells/cell-type threshold used later for display only
PANEL_TAG      = "panel"                    # short tag for output filenames
# =======================================================================

RES = "/mnt/results/data"
WORK = "/workspace/analysis"
os.makedirs(RES, exist_ok=True)
os.makedirs(WORK, exist_ok=True)


def main():
    tissue_expr = " or ".join([f"tissue_general == '{t}'" for t in TISSUES])
    gene_list = ", ".join([f"'{g}'" for g in GENE_PANEL])
    with cellxgene_census.open_soma(census_version=CENSUS_VERSION) as census:
        adata = cellxgene_census.get_anndata(
            census,
            organism=ORGANISM,
            measurement_name="RNA",
            X_name="normalized",  # atlas uses NORMALIZED expression
            obs_value_filter=(f"({tissue_expr}) and disease == '{REFERENCE_DISEASE}' "
                              "and is_primary_data == True"),
            var_value_filter=f"feature_name in [{gene_list}]",
            obs_column_names=["cell_type", "tissue_general", "assay", "disease", "donor_id"],
        )
    print("AnnData:", adata.shape)
    print("Resolved genes:\n", adata.var[["feature_name"]].to_string())
    found = set(adata.var["feature_name"])
    missing = [g for g in GENE_PANEL if g not in found]
    if missing:
        print(f"WARNING: genes not found in Census and dropped: {missing}")
    print("tissue_general counts:\n", adata.obs["tissue_general"].value_counts().to_string())
    adata.write(f"{WORK}/{PANEL_TAG}_expr.h5ad")

    # ---- per-cell-type summary: mean expression + % expressing, per tissue ----
    genes = adata.var["feature_name"].tolist()
    name2col = {g: i for i, g in enumerate(genes)}
    X = adata.X.tocsc() if sp.issparse(adata.X) else sp.csc_matrix(adata.X)
    obs = adata.obs.copy()
    obs["cell_type"] = obs["cell_type"].astype(str)

    def per_celltype(tissue):
        idx = np.where(obs["tissue_general"].values == tissue)[0]
        sub_obs = obs.iloc[idx]
        Xt = X[idx, :]
        rows = []
        for ct, gi in sub_obs.groupby("cell_type", observed=True).indices.items():
            rec = {"tissue": tissue, "cell_type": ct, "n_cells": len(gi)}
            for g in genes:
                col = Xt[:, name2col[g]][gi, :]
                vals = col.toarray().ravel() if sp.issparse(col) else np.asarray(col).ravel()
                rec[f"{g}_mean"] = float(vals.mean())
                rec[f"{g}_pct_expr"] = float((vals > 0).mean() * 100)
                rec[f"{g}_mean_in_expr"] = float(vals[vals > 0].mean()) if (vals > 0).any() else 0.0
            rows.append(rec)
        return pd.DataFrame(rows)

    summary = pd.concat([per_celltype(t) for t in TISSUES], ignore_index=True)
    summary = summary.sort_values(["tissue", "n_cells"], ascending=[True, False])
    out = f"{RES}/{PANEL_TAG}_expression_by_celltype.csv"
    summary.to_csv(out, index=False)
    print("Saved:", out, summary.shape)

    # quick top hits per gene
    for g in genes:
        disp = summary[summary.n_cells >= MIN_CELLS_DISPLAY]
        top = disp.sort_values(f"{g}_pct_expr", ascending=False).head(5)
        print(f"\nTop cell types by % expressing {g} (>= {MIN_CELLS_DISPLAY} cells):")
        print(top[["tissue", "cell_type", "n_cells", f"{g}_pct_expr", f"{g}_mean"]].to_string(index=False))


if __name__ == "__main__":
    main()
