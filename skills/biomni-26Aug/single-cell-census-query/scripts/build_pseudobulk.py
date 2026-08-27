#!/usr/bin/env python3
"""
Step 4 — Build donor x cell_type pseudobulk RAW-count matrix from the CZ CELLxGENE Census.

Streams the raw X matrix in donor-batches, summing counts per (donor_id, cell_type, disease) group
via a sparse group-indicator matmul, so the full cells x genes matrix is never held in memory.
This is the memory/throughput-critical step — run on a >= 32 GB machine, ideally as a background job.

Generalized from a validated run. Edit the PARAMETERS block only.

DATASET selection:
  - Set DATASET_ID to a single dataset that contains BOTH groups (PREFERRED — no batch confound;
    use enumerate_labels.py "Dataset containment" to find one).
  - Leave DATASET_ID = None to include ALL datasets containing the case/control groups in the
    chosen tissue(s) (multi-dataset; then add dataset/assay as a covariate in DESeq2 or flag it).

Outputs (to /mnt/shared-workspace/shared by default — large intermediates):
  pseudobulk_counts.csv    genes x pseudobulk-samples (raw summed integer counts)
  pseudobulk_coldata.csv   sample metadata: sample, donor_id, cell_type, disease, n_cells, sex
  pseudobulk_var.csv       feature_id -> feature_name
"""
import os, gc, time
import cellxgene_census
import numpy as np, pandas as pd, scipy.sparse as sp

# ============================== PARAMETERS ==============================
CASE_LABEL     = "DISEASE_OF_INTEREST"     # Census `disease` value, e.g. "chronic rhinitis"
CONTROL_LABEL  = "normal"
TISSUES        = ["tissue_a"]              # used only when DATASET_ID is None
DATASET_ID     = None                       # single shared dataset id (preferred) or None
ORGANISM       = "Homo sapiens"
CENSUS_VERSION = None                       # None -> latest stable; pin for reproducibility
MIN_CELLS_PER_SAMPLE = 10                   # drop pseudobulk samples with fewer cells
DONOR_BATCH    = 8                          # donors per streaming read (memory control)
OUT            = "/mnt/shared-workspace/shared"
MAX_ACC_GB     = 24                         # abort if the dense accumulator would exceed this
# =======================================================================

os.makedirs(OUT, exist_ok=True)
ORG = ORGANISM.lower().replace(" ", "_")
t0 = time.time()


def obs_filter():
    dis = f"disease in ['{CASE_LABEL}', '{CONTROL_LABEL}']"
    if DATASET_ID:
        return f"dataset_id == '{DATASET_ID}' and is_primary_data == True and {dis}"
    tissue_expr = " or ".join([f"tissue_general == '{t}'" for t in TISSUES])
    return f"({tissue_expr}) and is_primary_data == True and {dis}"


def main():
    # 1) obs (cell metadata) — small, fast
    with cellxgene_census.open_soma(census_version=CENSUS_VERSION) as census:
        obs = (census["census_data"][ORG].obs.read(
            value_filter=obs_filter(),
            column_names=["soma_joinid", "cell_type", "disease", "donor_id", "sex"],
        ).concat().to_pandas())
        var = (census["census_data"][ORG]["ms"]["RNA"].var.read(
            column_names=["soma_joinid", "feature_id", "feature_name"]).concat().to_pandas())

    for c in ["cell_type", "donor_id", "disease"]:
        obs[c] = obs[c].astype(str)
    print(f"[{time.time()-t0:.0f}s] obs rows: {len(obs)}, donors: {obs.donor_id.nunique()}, "
          f"cell types: {obs.cell_type.nunique()}, genes: {len(var)}", flush=True)
    if len(obs) == 0:
        raise SystemExit("No cells matched the filter — verify labels with enumerate_labels.py first.")

    # group key = donor | cell_type | disease
    obs["grp"] = obs["donor_id"] + "||" + obs["cell_type"] + "||" + obs["disease"]
    grp_levels = obs["grp"].unique().tolist()
    grp_index = {g: i for i, g in enumerate(grp_levels)}
    n_grp, n_genes = len(grp_levels), len(var)

    acc_gb = n_grp * n_genes * 8 / 1e9
    print(f"[{time.time()-t0:.0f}s] {n_grp} pseudobulk groups; dense accumulator ~{acc_gb:.1f} GB", flush=True)
    if acc_gb > MAX_ACC_GB:
        raise SystemExit(f"Accumulator {acc_gb:.1f} GB exceeds MAX_ACC_GB={MAX_ACC_GB}. "
                         "Provision a larger machine or restrict genes/cell types.")

    acc = np.zeros((n_grp, n_genes), dtype=np.float64)
    ncells = np.zeros(n_grp, dtype=np.int64)

    # 2) stream raw X in donor batches
    donors = sorted(obs["donor_id"].unique())
    with cellxgene_census.open_soma(census_version=CENSUS_VERSION) as census:
        for bstart in range(0, len(donors), DONOR_BATCH):
            bdon = donors[bstart:bstart + DONOR_BATCH]
            sub = obs[obs.donor_id.isin(bdon)]
            ad = cellxgene_census.get_anndata(
                census, organism=ORGANISM, measurement_name="RNA", X_name="raw",
                obs_coords=sub["soma_joinid"].to_numpy(),
                obs_column_names=["soma_joinid", "cell_type", "disease", "donor_id"],
            )
            gvec = (ad.obs["donor_id"].astype(str) + "||" + ad.obs["cell_type"].astype(str)
                    + "||" + ad.obs["disease"].astype(str)).map(grp_index).to_numpy()
            X = ad.X.tocsr() if sp.issparse(ad.X) else sp.csr_matrix(ad.X)
            rows = np.arange(X.shape[0])
            S = sp.csr_matrix((np.ones(X.shape[0]), (gvec, rows)), shape=(n_grp, X.shape[0]))
            acc += np.asarray((S @ X).todense())
            np.add.at(ncells, gvec, 1)
            done = bstart + len(bdon)
            rate = (time.time() - t0) / done
            print(f"[{time.time()-t0:.0f}s] donors {bstart+1}-{done}/{len(donors)} "
                  f"({X.shape[0]} cells); est. total ~{rate*len(donors)/60:.0f} min", flush=True)
            del ad, X, S; gc.collect()

    # 3) assemble outputs; keep samples with enough cells
    keep = ncells >= MIN_CELLS_PER_SAMPLE
    print(f"[{time.time()-t0:.0f}s] samples {n_grp}, kept (>= {MIN_CELLS_PER_SAMPLE} cells): {keep.sum()}", flush=True)
    kept = [grp_levels[i] for i in range(n_grp) if keep[i]]
    counts = pd.DataFrame(acc[keep].T.astype(np.int64),
                          index=var["feature_id"].values, columns=kept)
    coldata = pd.DataFrame({
        "sample": kept,
        "donor_id": [g.split("||")[0] for g in kept],
        "cell_type": [g.split("||")[1] for g in kept],
        "disease":  [g.split("||")[2] for g in kept],
        "n_cells":  ncells[keep],
    })
    sex_map = obs.drop_duplicates("donor_id").set_index("donor_id")["sex"].astype(str).to_dict()
    coldata["sex"] = coldata["donor_id"].map(sex_map)

    counts.to_csv(f"{OUT}/pseudobulk_counts.csv")
    coldata.to_csv(f"{OUT}/pseudobulk_coldata.csv", index=False)
    var.to_csv(f"{OUT}/pseudobulk_var.csv", index=False)
    print(f"[{time.time()-t0:.0f}s] DONE. counts {counts.shape}, coldata {coldata.shape}", flush=True)
    print("disease x n_samples:\n", coldata.groupby("disease").size().to_string())
    print("donors per group:\n",
          coldata.drop_duplicates("donor_id").groupby("disease").size().to_string())


if __name__ == "__main__":
    main()
