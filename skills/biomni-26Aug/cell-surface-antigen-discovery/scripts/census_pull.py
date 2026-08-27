#!/usr/bin/env python3
"""Multi-atlas tumor compartment expression from the CZ CELLxGENE Census.

Integrates WHOLE-CELL single-cell RNA-seq across multiple tumor datasets (atlases)
for a disease, partitions cells into epithelial/malignant, CAF, immune, and
endothelial compartments, and computes per-gene, per-compartment expression with
cross-dataset consensus.

Design choices (whole-cell, multi-atlas):
- Prefers `suspension_type == 'cell'` (whole-cell) over single-nucleus, which
  under-detects membrane / secreted transcripts.
- Aggregates ACROSS datasets and reports a consensus fraction, so single-study
  artifacts are penalized and reproducible targets rewarded.

Heavy dependencies (cellxgene_census, scanpy) are imported inside functions so the
module imports cleanly for offline / static testing.
"""

import json
import os
import re
import time
import warnings
from datetime import date

import numpy as np
import pandas as pd

DEFAULT_CENSUS_VERSION = "2025-11-08"
EPS = 1e-3  # pseudocount for enrichment ratios

# Cell Ontology keyword -> compartment. First match wins (ordered).
#
# ORDER MATTERS + MATCHING MATTERS. The malignant/epithelial compartment is checked
# FIRST, and keywords are matched on WORD BOUNDARIES (\b...\b), not raw substrings.
# Both are required to avoid a subtle, high-impact bug: the immune keyword "t cell"
# is a SUBSTRING of "malignan(t cell)", so naive substring matching with immune-first
# mis-assigns the very common Census label "malignant cell" to the immune compartment,
# silently corrupting tumor-vs-immune specificity. Regression-guarded in __main__.
COMPARTMENT_KEYWORDS = [
    ("epithelial", ["epithelial", "epithelium", "ductal", "acinar", "luminal", "basal cell",
                     "malignant", "neoplastic", "tumor", "tumour", "cancer cell",
                     "adenocarcinoma", "carcinoma", "adm"]),
    ("immune", ["t cell", "b cell", "nk cell", "natural killer", "macrophage",
                 "monocyte", "dendritic", "mast cell", "plasma cell", "lymphocyte",
                 "neutrophil", "myeloid", "leukocyte", "granulocyte", "immune"]),
    ("caf", ["fibroblast", "myofibroblast", "stellate", "mesenchymal stromal"]),
    ("endothelial", ["endothelial", "vascular"]),
]


def _kw_match(text, keyword):
    """Word-boundary match of a (possibly multi-word) keyword inside text.

    Uses \b boundaries so 't cell' matches 'CD8-positive t cell' but NOT the substring
    inside 'malignant cell'. Hyphens/commas in ontology labels count as boundaries.
    """
    return re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None


# ---------------------------------------------------------------------------
# LIQUID-TUMOR (AML) COMPARTMENT MODE  [added for the AML run]
# ---------------------------------------------------------------------------
# The default keyword map is built for SOLID tumors: malignant cells land in the
# "epithelial" target compartment, and myeloid/monocyte/dendritic labels are treated
# as tumor-infiltrating IMMUNE cells. In a myeloid LIQUID tumor (AML / APL bone marrow)
# this is exactly wrong: the leukemic blasts ARE the myeloid / monocytic / promyelocyte /
# progenitor populations, and the correct on-target-vs-off-tumor comparator is the
# residual NORMAL lymphoid compartment (T / B / NK / plasma cells).
#
# When AML mode is active, malignant myeloid blasts are mapped to "epithelial" (the
# generic target compartment the scorer reads) and normal lymphoid cells to "immune"
# (the comparator), so the downstream specificity / consensus / scoring / harness logic
# is used UNCHANGED. Mapping is by explicit Cell Ontology label (verified against the
# Census AML+APL cell_type inventory, census 2025-11-08) — not substring matching —
# because "monocyte"/"myeloid" legitimately appear in the solid-tumor immune keywords.
_AML_MALIGNANT_MYELOID = {
    "classical monocyte", "non-classical monocyte", "intermediate monocyte", "monocyte",
    "mhc-ii-positive classical monocyte",
    "early promyelocyte", "late promyelocyte", "promyelocyte", "myelocyte", "metamyelocyte",
    "hematopoietic multipotent progenitor cell", "hematopoietic stem cell",
    "hematopoietic oligopotent progenitor cell", "hematopoietic precursor cell",
    "hematopoietic cell", "progenitor cell",
    "granulocyte monocyte progenitor cell", "common myeloid progenitor",
    "megakaryocyte-erythroid progenitor cell", "megakaryocyte progenitor cell",
    "basophil mast progenitor cell", "common dendritic progenitor",
    "conventional dendritic cell", "plasmacytoid dendritic cell", "myeloid dendritic cell",
    "dendritic cell", "cycling myeloid cell", "myeloid cell", "myeloid leukocyte",
    "erythroid progenitor cell", "erythroid lineage cell", "erythroid progenitor cell, mammalian",
    "neutrophil progenitor cell", "granulocyte", "mature neutrophil", "neutrophil",
}
_AML_NORMAL_LYMPHOID_KEYWORDS = [
    "t cell", "b cell", "nk cell", "natural killer", "lymphocyte", "plasma cell",
    "thymocyte", "lymphoid", "innate lymphoid", "nkt",
]
_AML_STROMA_KEYWORDS = ["mesenchymal", "fibroblast", "stromal", "endosteal", "osteoblast"]

_AML_MODE = {"on": False}


def set_aml_mode(on=True):
    """Enable/disable liquid-tumor (AML) compartment mapping. See notes above."""
    _AML_MODE["on"] = bool(on)
    return _AML_MODE["on"]


def _assign_compartment_aml(cell_type):
    ct = str(cell_type).lower().strip()
    if ct in _AML_MALIGNANT_MYELOID:
        return "epithelial"          # malignant myeloid blast = target compartment
    if any(_kw_match(ct, k) for k in _AML_NORMAL_LYMPHOID_KEYWORDS):
        return "immune"              # residual normal lymphocytes = comparator
    if any(_kw_match(ct, k) for k in _AML_STROMA_KEYWORDS):
        return "caf"                 # marrow stroma
    if _kw_match(ct, "endothelial") or _kw_match(ct, "vascular"):
        return "endothelial"
    return "other"


def assign_compartment(cell_type):
    """Map a Census cell_type ontology label to a compartment. Tumor/malignant cells are
    grouped with epithelial (the target compartment for surface antigens).

    Epithelial/malignant is evaluated before immune and matching is word-boundary based,
    so 'malignant cell' -> 'epithelial' (NOT 'immune'). If AML mode is enabled
    (`set_aml_mode(True)`), the liquid-tumor mapping is used instead (myeloid blasts ->
    epithelial/target, normal lymphoid -> immune/comparator)."""
    if _AML_MODE["on"]:
        return _assign_compartment_aml(cell_type)
    ct = str(cell_type).lower()
    for compartment, keywords in COMPARTMENT_KEYWORDS:
        if any(_kw_match(ct, k) for k in keywords):
            return compartment
    return "other"


def _require_census():
    try:
        import cellxgene_census  # noqa: F401
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: cellxgene-census. Install with "
            "`pip install -U cellxgene-census` (Python 3.10-3.12)."
        ) from exc
    import cellxgene_census
    return cellxgene_census


def _disease_clause(disease_label):
    """Census obs disease clause from a single label or a list of labels.

    Census splits lung cancer across granularity levels (e.g. 'lung adenocarcinoma'
    vs the umbrella 'non-small cell lung carcinoma'), so a list lets a caller union
    related labels: ['lung adenocarcinoma', 'non-small cell lung carcinoma'].
    """
    if isinstance(disease_label, (list, tuple, set)):
        vals = ", ".join("'" + str(d) + "'" for d in disease_label)
        return f"disease in [{vals}]"
    return f"disease == '{disease_label}'"


def discover_datasets(disease_label, census_version=DEFAULT_CENSUS_VERSION,
                            organism="Homo sapiens", min_cells=500, whole_cell_only=True):
    """Discover datasets for a disease and report suspension type + compartments.

    Returns a DataFrame: dataset_id, n_cells, suspension_type, is_whole_cell.
    """
    cellxgene_census = _require_census()
    obs_filter = f"{_disease_clause(disease_label)} and is_primary_data == True"
    cols = ["dataset_id", "suspension_type", "assay", "cell_type"]
    with cellxgene_census.open_soma(census_version=census_version) as census:
        exp = census["census_data"]["homo_sapiens" if "sapiens" in organism.lower() else organism]
        obs = exp.obs.read(value_filter=obs_filter, column_names=cols).concat().to_pandas()

    if obs.empty:
        warnings.warn(
            f"No cells for disease == '{disease_label}'. Run a broader `discover` "
            "(see czi-cellxgene-census) and copy the exact label; disease may be "
            "'||'-composite in newer Census schemas."
        )
        return pd.DataFrame(columns=["dataset_id", "n_cells", "suspension_type", "is_whole_cell"])

    g = (obs.groupby(["dataset_id", "suspension_type"], observed=True)
            .size().reset_index(name="n_cells"))
    g["is_whole_cell"] = g["suspension_type"].astype(str).str.lower().eq("cell")
    g = g[g["n_cells"] >= min_cells].sort_values("n_cells", ascending=False)
    if whole_cell_only:
        kept = g[g["is_whole_cell"]]
        n_sn = int(g.loc[~g["is_whole_cell"], "n_cells"].sum())
        if n_sn:
            print(f"  De-weighting {n_sn} single-nucleus cells (whole_cell_only=True)")
        g = kept
    print(f"✓ Discovered {g['dataset_id'].nunique()} dataset(s) for '{disease_label}' "
          f"({int(g['n_cells'].sum())} cells)")
    return g.reset_index(drop=True)


def _normalize(adata):
    """CP10k + log1p on raw counts (deterministic across Census versions)."""
    import scanpy as sc
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    return adata


def _compartment_stats(adata, genes_in_order):
    """Per-gene, per-compartment mean (normalized) + pct expressing (from raw>0)."""
    comp = adata.obs["compartment"].astype(str).values
    X = adata.X
    is_sparse = hasattr(X, "tocsc")
    Xc = X.tocsc() if is_sparse else np.asarray(X)
    rows = []
    for j, gene in enumerate(genes_in_order):
        col = Xc[:, j]
        vals = np.asarray(col.toarray() if hasattr(col, "toarray") else col).ravel()
        for compartment in ["epithelial", "caf", "immune", "endothelial"]:
            mask = comp == compartment
            n = int(mask.sum())
            if n == 0:
                continue
            v = vals[mask]
            n_expr = int((v > 0).sum())
            rows.append({
                "gene_symbol": gene,
                "compartment": compartment,
                "n_cells": n,
                "n_expressing": n_expr,
                "pct_expressing": round(100.0 * n_expr / n, 4),
                "mean_expression": round(float(v.mean()), 6),
            })
    return rows


def pull_compartment_expression(genes, disease_label, census_version=DEFAULT_CENSUS_VERSION,
                                organism="Homo sapiens", output_dir="results",
                                whole_cell_only=True, enrich_ratio=3.0, min_epi_expr=0.1,
                                max_datasets=8, max_cells_per_dataset=20000):
    """Pull per-compartment expression for `genes` across multiple Census atlases.

    Writes `compartment_expression.csv` (per dataset) and
    `compartment_expression_consensus.csv` (cross-dataset), returns the consensus df.
    """
    cellxgene_census = _require_census()
    os.makedirs(output_dir, exist_ok=True)
    started = time.time()

    ds = discover_datasets(disease_label, census_version, organism,
                                whole_cell_only=whole_cell_only)
    dataset_ids = list(dict.fromkeys(ds["dataset_id"].tolist()))[:max_datasets]
    if not dataset_ids:
        raise RuntimeError(f"No usable datasets for '{disease_label}'.")

    # Cohort ledger (fix: the report must headline the ANALYSED cell count, not the
    # discovery catalogue). `_cat` is the discovered whole-cell count per atlas; a
    # per-atlas row is recorded below with the post-subsampling / post-compartment
    # counts actually used, so discovered vs analysed can be reported side by side.
    _cat = ds.groupby("dataset_id")["n_cells"].sum().to_dict() if len(ds) else {}
    n_cells_discovered_full = int(ds["n_cells"].sum()) if len(ds) else 0
    n_datasets_discovered_full = int(ds["dataset_id"].nunique()) if len(ds) else 0
    ledger = {}  # dataset_id -> ledger row (one per attempted atlas)

    genes = list(dict.fromkeys(str(g).strip() for g in genes))
    var_filter = "feature_name in [" + ", ".join("'" + g.replace("'", "''") + "'" for g in genes) + "]"
    susp = "and suspension_type == 'cell' " if whole_cell_only else ""

    long_rows = []
    org_title = "Homo sapiens" if "sapiens" in organism.lower() else organism
    org_key = "homo_sapiens" if "sapiens" in organism.lower() else organism
    rng = np.random.default_rng(0)  # seeded for reproducible subsampling
    with cellxgene_census.open_soma(census_version=census_version) as census:
        exp = census["census_data"][org_key]
        for ds_id in dataset_ids:
            row = {"dataset_id": ds_id, "n_discovered": int(_cat.get(ds_id, 0)),
                   "n_subsampled": 0, "n_epithelial": 0, "n_caf": 0, "n_immune": 0,
                   "n_endothelial": 0, "n_other": 0, "used": False, "skip_reason": ""}
            ledger[ds_id] = row
            obs_filter = (f"{_disease_clause(disease_label)} and is_primary_data == True "
                          f"{susp}and dataset_id == '{ds_id}'")
            try:
                ids = (exp.obs.read(value_filter=obs_filter, column_names=["soma_joinid"])
                       .concat().to_pandas()["soma_joinid"].to_numpy())
                if len(ids) == 0:
                    row["skip_reason"] = "no_cells"
                    continue
                if max_cells_per_dataset and len(ids) > max_cells_per_dataset:
                    ids = rng.choice(ids, size=max_cells_per_dataset, replace=False)
                    print(f"  subsampled dataset {ds_id[:8]}... to {max_cells_per_dataset} cells")
                row["n_subsampled"] = int(len(ids))
                adata = cellxgene_census.get_anndata(
                    census, organism=org_title, obs_coords=[int(i) for i in ids],
                    var_value_filter=var_filter,
                    obs_column_names=["cell_type", "dataset_id", "suspension_type"],
                )
            except Exception as exc:  # noqa: BLE001
                row["skip_reason"] = "pull_failed"
                print(f"  ! dataset {ds_id[:8]}...: pull failed ({exc}); skipping")
                continue
            if adata.n_obs == 0 or adata.n_vars == 0:
                row["skip_reason"] = "empty_matrix"
                continue
            row["n_subsampled"] = int(adata.n_obs)
            adata.obs["compartment"] = [assign_compartment(c) for c in adata.obs["cell_type"]]
            _comp_counts = adata.obs["compartment"].value_counts().to_dict()
            for _comp in ("epithelial", "caf", "immune", "endothelial", "other"):
                row[f"n_{_comp}"] = int(_comp_counts.get(_comp, 0))
            if row["n_epithelial"] < 20:
                row["skip_reason"] = "lt20_epithelial"
                print(f"  ! dataset {ds_id[:8]}...: <20 epithelial cells; skipping")
                continue
            adata = _normalize(adata)
            genes_in_order = list(adata.var.get("feature_name", adata.var_names))
            for r in _compartment_stats(adata, genes_in_order):
                r["dataset_id"] = ds_id
                long_rows.append(r)
            row["used"] = True
            print(f"  ✓ dataset {ds_id[:8]}...: {adata.n_obs} cells, "
                  f"{row['n_epithelial']} epithelial")

    long_df = pd.DataFrame(long_rows)
    if long_df.empty:
        raise RuntimeError("No compartment expression computed (no epithelial cells found).")
    long_df.to_csv(os.path.join(output_dir, "compartment_expression.csv"), index=False)

    consensus = _build_consensus(long_df, enrich_ratio=enrich_ratio, min_epi_expr=min_epi_expr)
    consensus.to_csv(os.path.join(output_dir, "compartment_expression_consensus.csv"), index=False)
    print(f"✓ Compartment expression: {consensus['gene_symbol'].nunique()} genes across "
          f"{long_df['dataset_id'].nunique()} datasets ({round(time.time()-started,1)}s)")

    # --- Cohort cell-count ledger + summary (fix: report analysed, not catalogue) ---
    ledger_rows = list(ledger.values())
    ledger_df = pd.DataFrame(ledger_rows)
    ledger_df.to_csv(os.path.join(output_dir, "cohort_cell_counts.csv"), index=False)
    used = ledger_df[ledger_df["used"]] if len(ledger_df) else ledger_df
    _cc = ["n_epithelial", "n_caf", "n_immune", "n_endothelial"]
    n_analyzed = int(used[_cc].to_numpy().sum()) if len(used) else 0
    n_subsampled_total = int(used["n_subsampled"].sum()) if len(used) else 0
    n_discovered_analyzed = int(used["n_discovered"].sum()) if len(used) else 0
    n_atlases_analyzed = int(len(used))
    per_comp = {c.replace("n_", ""): (int(used[c].sum()) if len(used) else 0)
                for c in ["n_epithelial", "n_caf", "n_immune", "n_endothelial", "n_other"]}
    cap = int(max_cells_per_dataset) if max_cells_per_dataset else None
    cap_str = f"{cap:,}" if cap else "no cap"
    if n_atlases_analyzed:
        cohort_statement = (
            f"{n_atlases_analyzed} atlas(es) totalling {n_discovered_analyzed:,} whole-cell cells "
            f"discovered for '{disease_label}' (Census {census_version}); {n_analyzed:,} cells "
            f"analysed across the four compartments after per-atlas subsampling to {cap_str} "
            f"(from {n_subsampled_total:,} cells pulled).")
    else:
        cohort_statement = f"No atlases contributed analysable cells for '{disease_label}'."
    funnel_statement = (
        f"epithelial {per_comp['epithelial']:,} · CAF {per_comp['caf']:,} · "
        f"immune {per_comp['immune']:,} · endothelial {per_comp['endothelial']:,} "
        f"(+ {per_comp['other']:,} other, not used in specificity).")
    cohort = {
        "disease_label": disease_label if isinstance(disease_label, str) else list(disease_label),
        "census_version": census_version,
        "subsample_cap_per_dataset": cap,
        "n_datasets_discovered_full": n_datasets_discovered_full,
        "n_cells_discovered_full": n_cells_discovered_full,
        "n_datasets_analyzed": n_atlases_analyzed,
        "n_cells_discovered_analyzed_atlases": n_discovered_analyzed,
        "n_cells_subsampled": n_subsampled_total,
        "n_cells_analyzed": n_analyzed,
        "per_compartment_totals": per_comp,
        "per_atlas": ledger_rows,
        "cohort_statement": cohort_statement,
        "funnel_statement": funnel_statement,
    }
    with open(os.path.join(output_dir, "cohort_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(cohort, fh, indent=2)
    print(f"✓ Cohort: discovered {n_cells_discovered_full:,} whole-cell cells; "
          f"analysed {n_analyzed:,} across {n_atlases_analyzed} atlas(es) "
          f"(subsample cap {cap_str}).")
    return consensus


def _build_consensus(long_df, enrich_ratio=3.0, min_epi_expr=0.1):
    """Aggregate per-dataset compartment means into a cross-dataset consensus."""
    pivot = long_df.pivot_table(index=["gene_symbol", "dataset_id"], columns="compartment",
                                values="mean_expression", aggfunc="mean").reset_index()
    pct = long_df.pivot_table(index=["gene_symbol", "dataset_id"], columns="compartment",
                              values="pct_expressing", aggfunc="mean").reset_index()
    for c in ["epithelial", "caf", "immune", "endothelial"]:
        if c not in pivot.columns:
            pivot[c] = np.nan
        if c not in pct.columns:
            pct[c] = np.nan

    pivot["spec_vs_caf"] = (pivot["epithelial"] + EPS) / (pivot["caf"].fillna(0) + EPS)
    pivot["spec_vs_immune"] = (pivot["epithelial"] + EPS) / (pivot["immune"].fillna(0) + EPS)
    pivot["spec_vs_tme"] = pivot[["spec_vs_caf", "spec_vs_immune"]].min(axis=1)
    pivot["enriched"] = (pivot["spec_vs_tme"] >= enrich_ratio) & (pivot["epithelial"] >= min_epi_expr)

    rows = []
    for gene, sub in pivot.groupby("gene_symbol"):
        psub = pct[pct["gene_symbol"] == gene]
        n_ds = len(sub)
        rows.append({
            "gene_symbol": gene,
            "n_datasets": n_ds,
            "epithelial_mean": round(float(sub["epithelial"].mean(skipna=True)), 6),
            "caf_mean": round(float(sub["caf"].mean(skipna=True)), 6),
            "immune_mean": round(float(sub["immune"].mean(skipna=True)), 6),
            "endothelial_mean": round(float(sub["endothelial"].mean(skipna=True)), 6),
            "epithelial_pct": round(float(psub["epithelial"].mean(skipna=True)), 4),
            "caf_pct": round(float(psub["caf"].mean(skipna=True)), 4),
            "immune_pct": round(float(psub["immune"].mean(skipna=True)), 4),
            "spec_vs_caf": round(float(sub["spec_vs_caf"].median()), 4),
            "spec_vs_immune": round(float(sub["spec_vs_immune"].median()), 4),
            "spec_vs_tme": round(float(sub["spec_vs_tme"].median()), 4),
            "n_datasets_enriched": int(sub["enriched"].sum()),
            "consensus_fraction": round(float(sub["enriched"].mean()), 4),
        })
    return pd.DataFrame(rows).sort_values("spec_vs_tme", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    # Regression guard for the "malignant cell" -> immune substring bug.
    checks = {
        "malignant cell": "epithelial",
        "neoplastic cell": "epithelial",
        "malignant epithelial cell": "epithelial",
        "tumor cell": "epithelial",
        "CD8-positive, alpha-beta T cell": "immune",
        "T cell": "immune",
        "B cell": "immune",
        "fibroblast of lung": "caf",
        "endothelial cell": "endothelial",
    }
    ok = True
    for label, expected in checks.items():
        got = assign_compartment(label)
        flag = "OK " if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  {flag} assign_compartment({label!r}) = {got!r} (expected {expected!r})")
    assert ok, "compartment assignment regression FAILED"
    print("✓ compartment assignment regression checks passed")
    print(f"Run pull_compartment_expression(genes, '<disease>') for a live Census pull "
          f"(default census {DEFAULT_CENSUS_VERSION}).")
