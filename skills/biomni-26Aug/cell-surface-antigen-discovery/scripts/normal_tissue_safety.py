#!/usr/bin/env python3
"""Normal-tissue therapeutic index (the primary safety axis for antibody modalities).

A surface antigen is only useful for an ADC / CAR-T / bispecific if it is LOW or
absent in vital normal tissues — on-target/off-tumor expression in heart, lung,
liver, kidney, brain, marrow, or GI tract drives dose-limiting toxicity. This module
turns a normal-tissue baseline (Human Protein Atlas RNA consensus nTPM + IHC protein
levels) into a 0-1 safety score.

INPUT: target_baseline_expression_long.csv with columns
    gene_symbol, tissue, organs, rna_value, rna_level, protein_level
This file is produced by scripts/hpa_baseline.py (build_hpa_baseline_long). Open Targets
Platform v4 removed Target.expressions, so the baseline is sourced directly from HPA;
the safety math below (vital-organ keywords, nTPM thresholds, IHC->ordinal mapping, and
the conservative min(protein, RNA)) is unchanged and source-agnostic.

This is the safety axis an essentiality-gated pipeline omits — and the reason
housekeeping hits (ATP1A1, CDH1, LDLR) are undruggable: they are broadly expressed
in normal tissue.
"""

import os

import numpy as np
import pandas as pd

# Vital / dose-limiting normal tissues (keyword match against HPA tissue + organ labels).
VITAL_ORGAN_KEYWORDS = [
    "heart", "cardiac", "myocard", "lung", "bronch", "liver", "hepato",
    "kidney", "renal", "brain", "cerebr", "cortex", "cerebell", "hippocamp",
    "nerve", "spinal", "bone marrow", "marrow", "hematopoiet", "blood",
    "pancrea", "stomach", "gastric", "intestin", "duoden", "jejun", "ileum",
    "colon", "rectum", "esophag", "adrenal",
]

# HPA protein/RNA level string -> ordinal (0 = not detected, 3 = high).
_LEVEL_ORD = {"not detected": 0, "low": 1, "medium": 2, "high": 3,
              "not representative": np.nan}


def _is_vital(tissue, organs):
    text = f"{str(tissue).lower()} {str(organs).lower()}"
    return any(k in text for k in VITAL_ORGAN_KEYWORDS)


def _level_to_ord(level):
    if level is None or (isinstance(level, float) and np.isnan(level)):
        return np.nan
    if isinstance(level, (int, float)):
        return float(level)  # already-numeric level (0-3)
    return _LEVEL_ORD.get(str(level).strip().lower(), np.nan)


def _protein_safety(max_ord):
    if np.isnan(max_ord):
        return np.nan
    return {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.1}.get(int(round(max_ord)), np.nan)


def _rna_safety(max_ntpm):
    if np.isnan(max_ntpm):
        return np.nan
    if max_ntpm < 1:
        return 1.0
    if max_ntpm < 10:
        return 0.7
    if max_ntpm < 50:
        return 0.4
    return 0.1


def compute_therapeutic_index(annotation_df, output_dir="results", baseline_long_path=None):
    """Compute per-gene normal-tissue safety from OT baseline expression.

    Writes therapeutic_index.csv; returns the DataFrame
    (gene_symbol, vital_protein_max, vital_rna_max, top_normal_tissues,
     safety_score, vital_organ_flag).
    """
    os.makedirs(output_dir, exist_ok=True)
    if baseline_long_path is None:
        baseline_long_path = os.path.join(output_dir, "target_baseline_expression_long.csv")

    genes = list(dict.fromkeys(annotation_df["gene_symbol"].astype(str)))
    if not os.path.exists(baseline_long_path):
        print(f"  ! Baseline expression not found ({baseline_long_path}); "
              "safety_score = NaN (will be reweighted in scoring).")
        df = pd.DataFrame({"gene_symbol": genes})
        for c in ["vital_protein_max", "vital_rna_max", "top_normal_tissues",
                  "safety_score", "vital_organ_flag"]:
            df[c] = np.nan if c != "top_normal_tissues" else None
        df.to_csv(os.path.join(output_dir, "therapeutic_index.csv"), index=False)
        return df

    base = pd.read_csv(baseline_long_path)
    base["vital"] = base.apply(lambda r: _is_vital(r.get("tissue"), r.get("organs")), axis=1)
    base["protein_ord"] = base["protein_level"].map(_level_to_ord)
    base["rna_value"] = pd.to_numeric(base["rna_value"], errors="coerce")

    rows = []
    for gene in genes:
        sub = base[base["gene_symbol"] == gene]
        vital = sub[sub["vital"]]
        vp = vital["protein_ord"].max(skipna=True) if len(vital) else np.nan
        vr = vital["rna_value"].max(skipna=True) if len(vital) else np.nan
        vp = float(vp) if pd.notna(vp) else np.nan
        vr = float(vr) if pd.notna(vr) else np.nan
        s_prot, s_rna = _protein_safety(vp), _rna_safety(vr)
        # Conservative: if both signals exist, take the LOWER safety.
        cands = [s for s in (s_prot, s_rna) if not (isinstance(s, float) and np.isnan(s))]
        safety = float(min(cands)) if cands else np.nan
        top = (sub.sort_values("rna_value", ascending=False)["tissue"].dropna().head(3).tolist())
        rows.append({
            "gene_symbol": gene,
            "vital_protein_max": vp,
            "vital_rna_max": round(vr, 3) if not np.isnan(vr) else np.nan,
            "top_normal_tissues": "; ".join(map(str, top)) if top else None,
            "safety_score": round(safety, 4) if not (isinstance(safety, float) and np.isnan(safety)) else np.nan,
            "vital_organ_flag": (safety < 0.4) if not (isinstance(safety, float) and np.isnan(safety)) else None,
        })
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(output_dir, "therapeutic_index.csv"), index=False)
    n_safe = int((df["safety_score"] >= 0.6).sum())
    n_flag = int((df["vital_organ_flag"] == True).sum())  # noqa: E712
    print(f"✓ Therapeutic index: {n_safe} gene(s) with favorable normal-tissue safety "
          f"(>=0.6), {n_flag} flagged for vital-organ expression.")
    return df


if __name__ == "__main__":
    demo = pd.DataFrame({"gene_symbol": ["MSLN", "CDH1"]})
    print(compute_therapeutic_index(demo, output_dir="/tmp/cstd_demo"))
