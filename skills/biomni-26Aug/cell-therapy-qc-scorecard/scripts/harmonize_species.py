"""
============================================================================
HARMONIZE SPECIES  —  cell-therapy scRNA-seq QC release scorecard
============================================================================

For products aligned to a multi-species (e.g. GRCh38_mm10) reference — common
when a human product is xenografted into mouse — split features by species,
compute a per-cell species fraction, keep the product-species cells, and strip
the species prefix from gene symbols so downstream marker panels match.

No-op for single-species data (multispecies=False): just records that all cells
are the product species.

Species prefixes handled: 'GRCh38_' / 'hg38_' / 'hg19_' / 'GRCh37_' (human),
'mm10_' / 'GRCm38_' / 'mm39_' (mouse). Also handles the common CellRanger
"<genome>_<GENE>" combined-reference symbol style.

Functions
  - harmonize_species(units, cfg) -> (units, species_table_df)

Usage
  from harmonize_species import harmonize_species
  units, species_df = harmonize_species(units, cfg)
"""

import os
import re
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import scanpy as sc

HUMAN_PREFIXES = ("GRCh38_", "GRCh38-", "hg38_", "hg19_", "GRCh37_", "GRCh37-")
MOUSE_PREFIXES = ("mm10_", "mm10-", "GRCm38_", "GRCm38-", "mm39_", "GRCm39_")


def _species_of_var(names, prefixes):
    return np.array([any(str(n).startswith(p) for p in prefixes) for n in names])


def _strip_prefix(name: str) -> str:
    for p in HUMAN_PREFIXES + MOUSE_PREFIXES:
        if str(name).startswith(p):
            return str(name)[len(p):]
    return str(name)


def harmonize_species(units: Dict[str, "sc.AnnData"], cfg: Dict
                      ) -> Tuple[Dict[str, "sc.AnnData"], pd.DataFrame]:
    """Split by species (if multispecies) and keep the product species."""
    keep_prefixes = HUMAN_PREFIXES if cfg["species"] == "human" else MOUSE_PREFIXES
    other_prefixes = MOUSE_PREFIXES if cfg["species"] == "human" else HUMAN_PREFIXES
    rows = []

    for name, a in list(units.items()):
        human_mask = _species_of_var(a.var_names, HUMAN_PREFIXES)
        mouse_mask = _species_of_var(a.var_names, MOUSE_PREFIXES)
        has_both = human_mask.any() and mouse_mask.any()

        if not (cfg.get("multispecies") or has_both):
            # single species: record and continue (strip any stray prefix)
            a.var_names = [_strip_prefix(n) for n in a.var_names]
            a.var_names_make_unique()
            rows.append({"unit": name, "n_cells": a.n_obs,
                         "product_species_frac_median": 1.0,
                         "n_cells_kept": a.n_obs, "mode": "single_species"})
            continue

        # multi-species: per-cell counts per species
        X = a.layers.get("counts", a.X)
        keep_mask = _species_of_var(a.var_names, keep_prefixes)
        other_mask = _species_of_var(a.var_names, other_prefixes)
        counts_keep = np.asarray(X[:, keep_mask].sum(1)).ravel()
        counts_other = np.asarray(X[:, other_mask].sum(1)).ravel()
        total = counts_keep + counts_other
        frac = np.where(total > 0, counts_keep / np.maximum(total, 1), 0.0)
        a.obs["product_species_frac"] = frac

        thr = cfg.get("keep_species_frac", 0.9)
        keep_cells = frac > thr

        # subset to product-species genes and product-species cells
        a_keep = a[keep_cells, keep_mask].copy()
        a_keep.var_names = [_strip_prefix(n) for n in a_keep.var_names]
        a_keep.var_names_make_unique()
        # recompute counts layer on the subset
        a_keep.layers["counts"] = a_keep.X.copy()
        a_keep.uns["n_cells_raw"] = a.uns.get("n_cells_raw", a.n_obs)
        units[name] = a_keep

        rows.append({
            "unit": name,
            "n_cells": int(a.n_obs),
            "product_species_frac_median": float(np.median(frac)),
            "pct_cells_product_species": float(100 * keep_cells.mean()),
            "pct_cross_species_contam": float(100 * (1 - keep_cells.mean())),
            "n_cells_kept": int(keep_cells.sum()),
            "mode": "multi_species_split",
        })
        print(f"  ✓ {name}: multi-species split — kept {keep_cells.sum()}/{a.n_obs} "
              f"{cfg['species']} cells ({100*keep_cells.mean():.1f}%), "
              f"cross-species contam {100*(1-keep_cells.mean()):.2f}%")

    species_df = pd.DataFrame(rows)
    out = os.path.join(cfg["dirs"]["tables"], "01_species_composition.csv")
    species_df.to_csv(out, index=False)
    print(f"✓ species composition -> {out}")
    return units, species_df


if __name__ == "__main__":
    print("harmonize_species.py — import and call harmonize_species(units, cfg).")
