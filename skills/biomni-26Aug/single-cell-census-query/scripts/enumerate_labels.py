#!/usr/bin/env python3
"""
PRE-FLIGHT label verification for the single-cell-census-query skill.

The single most important guardrail: verify that the requested disease/tissue labels actually
exist in the CZ CELLxGENE Census BEFORE running any analysis. In the source analysis, the
requested disease ("asthma") had ZERO primary-data cells, and a documented proxy had to be used.
This script surfaces that situation instead of failing silently downstream.

What it does:
  1. Resolves GENE_PANEL symbols -> Ensembl feature_ids (warns on unresolved symbols).
  2. Enumerates available `disease` labels (with primary-data cell counts) for the target tissue(s).
  3. Confirms CASE_LABEL and CONTROL_LABEL both exist; if CASE_LABEL is absent, prints candidate
     related labels so a proxy can be proposed to the user (require confirmation before proceeding).
  4. Reports per-(cell_type x group) donor counts so testable cell types are visible up front.
  5. Identifies dataset_id(s) that contain BOTH groups (preferred: single shared dataset).

Edit the PARAMETERS block. Nothing here is analysis-specific by default.
"""
import argparse
import cellxgene_census
import pandas as pd

# ============================== PARAMETERS ==============================
GENE_PANEL     = ["GENE1", "GENE2"]        # e.g. ["IL13", "TSLP"]
TISSUES        = ["tissue_a"]              # Census tissue_general, e.g. ["lung", "nose"]
CASE_LABEL     = "DISEASE_OF_INTEREST"     # Census `disease` value, e.g. "chronic rhinitis"
CONTROL_LABEL  = "normal"                  # control `disease` value
ORGANISM       = "Homo sapiens"
CENSUS_VERSION = None                       # None -> latest stable; pin a string for reproducibility
MIN_DONORS_PER_GROUP = 3
# =======================================================================


def resolve_genes(census, panel):
    var = (census["census_data"][ORGANISM.lower().replace(" ", "_")]["ms"]["RNA"].var
           .read(column_names=["soma_joinid", "feature_id", "feature_name"]).concat().to_pandas())
    hit = var[var["feature_name"].isin(panel)][["feature_name", "feature_id", "soma_joinid"]]
    found = set(hit["feature_name"])
    missing = [g for g in panel if g not in found]
    print("=== Gene resolution ===")
    print(hit.to_string(index=False))
    if missing:
        print(f"WARNING: {len(missing)} symbol(s) NOT found in Census var: {missing}")
        print("  -> check for deprecated/alias symbols before proceeding; do not silently drop genes.")
    return hit, missing


def enumerate_diseases(census, tissues):
    org = ORGANISM.lower().replace(" ", "_")
    tissue_expr = " or ".join([f"tissue_general == '{t}'" for t in tissues])
    obs = (census["census_data"][org].obs.read(
        value_filter=f"({tissue_expr}) and is_primary_data == True",
        column_names=["disease", "tissue_general", "dataset_id", "donor_id", "cell_type"],
    ).concat().to_pandas())
    print(f"\n=== Disease labels in tissue(s) {tissues} (primary data) ===")
    dcounts = (obs.groupby("disease")
               .agg(n_cells=("disease", "size"), n_donors=("donor_id", "nunique"))
               .sort_values("n_cells", ascending=False))
    print(dcounts.to_string())
    return obs, dcounts


def check_labels(dcounts, case, control):
    present = set(dcounts.index)
    print("\n=== Label check ===")
    print(f"  CONTROL_LABEL '{control}': {'PRESENT' if control in present else 'ABSENT'}")
    if case in present:
        print(f"  CASE_LABEL '{case}': PRESENT ({int(dcounts.loc[case,'n_cells'])} cells, "
              f"{int(dcounts.loc[case,'n_donors'])} donors)")
    else:
        print(f"  CASE_LABEL '{case}': *** ABSENT ***")
        print("  -> DO NOT silently substitute. Surface this to the user.")
        # crude candidate proxy suggestions: labels sharing a word with the requested disease
        toks = set(case.lower().replace("-", " ").split())
        cand = [d for d in present if toks & set(str(d).lower().replace("-", " ").split())]
        print(f"  Candidate related labels present (possible proxies, needs user confirmation): "
              f"{cand if cand else 'none obvious — consider omics-dataset-retrieval (GEO/etc.)'}")


def donor_counts_by_celltype(obs, case, control, min_donors):
    grp = obs[obs["disease"].isin([case, control])].copy()
    if grp.empty:
        print("\n(No cells for the requested case/control labels — resolve labels first.)")
        return
    tab = (grp.groupby(["cell_type", "disease"])["donor_id"].nunique()
           .unstack(fill_value=0))
    for lab in (control, case):
        if lab not in tab.columns:
            tab[lab] = 0
    tab["testable"] = (tab[control] >= min_donors) & (tab[case] >= min_donors)
    tab = tab.sort_values(case, ascending=False)
    print(f"\n=== Donors per (cell_type x group); testable = >= {min_donors} donors in BOTH ===")
    print(tab.to_string())
    print(f"\nTestable cell types: {int(tab['testable'].sum())} / {len(tab)}")


def shared_datasets(obs, case, control):
    grp = obs[obs["disease"].isin([case, control])]
    if grp.empty:
        return
    per = grp.groupby("dataset_id")["disease"].nunique()
    both = per[per >= 2].index.tolist()
    print("\n=== Dataset containment ===")
    if both:
        print(f"  {len(both)} dataset(s) contain BOTH groups (PREFERRED, no batch confound): {both}")
    else:
        print("  No single dataset contains both groups -> multi-dataset comparison.")
        print("  -> add dataset/assay as a covariate or flag batch confounding as a limitation.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--census-version", default=CENSUS_VERSION)
    args = ap.parse_args()
    with cellxgene_census.open_soma(census_version=args.census_version) as census:
        print(f"Census opened. Requested version: {args.census_version or 'latest stable'}")
        resolve_genes(census, GENE_PANEL)
        obs, dcounts = enumerate_diseases(census, TISSUES)
        check_labels(dcounts, CASE_LABEL, CONTROL_LABEL)
        donor_counts_by_celltype(obs, CASE_LABEL, CONTROL_LABEL, MIN_DONORS_PER_GROUP)
        shared_datasets(obs, CASE_LABEL, CONTROL_LABEL)
    print("\nPre-flight complete. Confirm labels/proxy with the user before running the analysis.")


if __name__ == "__main__":
    main()
