#!/usr/bin/env python3
"""
build_allelic_series.py — Integrate ClinVar + CIViC into a tiered allelic series.

Joins the gene's ClinVar catalog and CIViC evidence by four methods, derives a
consistent residue position for every specific allele, assigns tiers, infers a
coarse mechanism class, and writes the master table + a summary_stats.json.

CRITICAL CORRECTNESS RULE (residue position):
    For a SPECIFIC allele, the residue position is DEFINED by the variant's OWN
    protein-change name (first integer after a letter). NEVER copy the position
    from a linked ClinVar record's aa_pos — a linked record may be a different
    variant at a different codon, which silently mis-places alleles on the
    lollipop x-axis and in tables. We derive from name, then assert internal
    consistency (see reconcile step).

Tiers:
    Tier 1 — CIViC Predictive (therapeutic) evidence WITH >=1 therapy
    Tier 2 — other CIViC evidence (Dx/Px/Predisposing/Oncogenic) OR ClinVar P/LP
    Tier 3 — remainder of the ClinVar catalog (aggregate context only)

Join methods recorded per allele:
    clinvar_id+protein_change, clinvar_id, protein_change, categorical,
    civic_only, clinvar_only

Usage:
    python build_allelic_series.py --gene EGFR \
        --outdir /mnt/results/EGFR_allelic_series
    (expects fetch_clinvar.py and fetch_civic.py outputs already in --outdir)
"""
import argparse
import json
import os
import re
import sys

import numpy as np
import pandas as pd

LEVEL_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}

# Categorical CIViC entries (not a single codon) -> approximate region label.
# Extend per gene as needed; mapping is APPROXIMATE and flagged in the report.
CATEGORICAL_PATTERNS = [
    (r"exon\s*19\s*deletion", "exon19del"),
    (r"exon\s*20\s*insertion", "exon20ins"),
    (r"exon\s*\d+", "exon-level"),
    (r"amplification", "amplification"),
    (r"expression", "expression"),
    (r"mutation$", "gene-level"),
]


def pos_from_name(variant):
    """First integer following a letter = start codon. Definitional for alleles."""
    if not isinstance(variant, str) or not variant.strip():
        return None
    m = re.search(r"[A-Za-z](\d+)", variant.strip())
    return int(m.group(1)) if m else None


def is_specific_allele(variant):
    """True if the CIViC/ClinVar variant name denotes a specific protein allele."""
    if not isinstance(variant, str):
        return False
    v = variant.strip()
    # e.g. T790M, L858R, E746_A750del, V769_D770insASV, G12C
    return bool(re.match(r"^[A-Za-z]\d+", v)) and "exon" not in v.lower()


def _level_rank_series(levels):
    """Given a list/Series of level letters, return the best (max) rank + letter."""
    best_rank, best_letter = 0, ""
    for lv in levels:
        lv = (lv or "").strip().upper()[:1]
        if lv in LEVEL_RANK and LEVEL_RANK[lv] > best_rank:
            best_rank, best_letter = LEVEL_RANK[lv], lv
    return best_rank, best_letter


def _as_bool(series):
    """Normalize a column that may be real bool or the strings 'True'/'False'."""
    return series.astype(str).str.strip().str.lower().isin(["true", "1", "yes"])


def summarize_civic_by_variant(ev):
    """Collapse CIViC evidence (single-variant rows) to one row per variant."""
    if ev.empty:
        return pd.DataFrame()
    if "is_single_variant_evidence" in ev.columns:
        ev = ev[_as_bool(ev["is_single_variant_evidence"])].copy()
    if ev.empty:
        return pd.DataFrame()

    # Normalize expected columns (names verified from CIViC nightly dump).
    def col(*names, default=""):
        for n in names:
            if n in ev.columns:
                return ev[n].fillna(default)
        return pd.Series([default] * len(ev), index=ev.index)

    ev = ev.assign(
        _level=col("evidence_level").astype(str),
        _type=col("evidence_type").astype(str),
        _dir=col("evidence_direction").astype(str),
        _sig=col("significance", "clinical_significance").astype(str),
        _ther=col("therapies", "drugs").astype(str),
        _dis=col("disease").astype(str),
    )

    rows = []
    for var, g in ev.groupby("variant"):
        if not var:
            continue
        best_rank, best_letter = _level_rank_series(g["_level"])
        pred = g[g["_type"].str.contains("Predictive", case=False, na=False)]
        pbest_rank, pbest_letter = _level_rank_series(pred["_level"])
        # Therapy direction split. CIViC clinical meaning is significance
        # COMBINED WITH evidence_direction: a "Does Not Support" item inverts the
        # significance. So "Does Not Support Resistance" means the drug retains
        # activity (-> sensitivity), and "Does Not Support Sensitivity/Response"
        # means the drug lacks activity (-> resistance). Ignoring direction
        # silently mislabels those items (e.g. it would put imatinib in the
        # sensitivity bucket for D816V). Only Predictive evidence carries a
        # therapy sensitivity/resistance meaning.
        sens, res = set(), set()
        for _, r in g.iterrows():
            if not re.search(r"Predictive", r["_type"], re.I):
                continue
            sig = r["_sig"]
            direction = r["_dir"]
            supports = not re.search(r"Does\s*Not\s*Support", direction, re.I)
            is_sens_sig = bool(re.search(r"Sensitivity|Response", sig, re.I))
            is_res_sig = bool(re.search(r"Resistance", sig, re.I))
            if not (is_sens_sig or is_res_sig):
                continue  # e.g. "N/A", "Reduced Sensitivity" handled below
            thers = [t.strip() for t in re.split(r"[,;]", r["_ther"]) if t.strip()]
            # Resolve effective direction after applying evidence_direction.
            effective_sens = (is_sens_sig and supports) or (is_res_sig and not supports)
            effective_res = (is_res_sig and supports) or (is_sens_sig and not supports)
            if effective_sens:
                sens.update(thers)
            if effective_res:
                res.update(thers)
        rows.append({
            "variant": var,
            "civic_best_level": best_letter,
            "civic_best_predictive_level": pbest_letter,
            "n_evidence": int(len(g)),
            "n_predictive": int(len(pred)),
            "evidence_levels": ";".join(sorted(set(g["_level"]) - {""})),
            "evidence_types": ";".join(sorted(set(g["_type"]) - {""})),
            "sensitivity_therapies": "; ".join(sorted(sens)),
            "resistance_therapies": "; ".join(sorted(res)),
            "diseases": "; ".join(sorted(set(g["_dis"]) - {""})),
        })
    return pd.DataFrame(rows)


def summarize_clinvar_by_protein(cv):
    """Collapse ClinVar catalog to one row per protein_change (specific alleles)."""
    if cv.empty:
        return pd.DataFrame()
    cv = cv[cv["protein_change"].fillna("") != ""].copy()
    rows = []
    for pc, g in cv.groupby("protein_change"):
        germ = "; ".join(sorted(set(g["germline_classification"].fillna("")) - {""}))
        onco = "; ".join(sorted(set(g["oncogenicity_classification"].fillna("")) - {""}))
        rows.append({
            "protein_change": pc,
            "residue_position": pos_from_name(pc),
            "clinvar_significance": germ,
            "clinvar_oncogenicity": onco,
            "clinvar_n": int(len(g)),
            "clinvar_conditions": "; ".join(sorted(set(
                "; ".join(g["conditions"].fillna("")).split("; ")) - {""}))[:500],
            "clinvar_variation_ids": ";".join(sorted(set(g["variation_id"].astype(str)))),
            "molecular_consequence": "; ".join(sorted(set(
                "; ".join(g["molecular_consequence"].fillna("")).split("; ")) - {""})),
        })
    return pd.DataFrame(rows)


def categorical_region(name):
    low = (name or "").lower()
    for pat, label in CATEGORICAL_PATTERNS:
        if re.search(pat, low):
            return label
    return None


def infer_mechanism(row):
    """Coarse mechanism class from combined evidence."""
    def _s(x):
        s = "" if x is None else str(x).strip()
        return "" if s.lower() in ("nan", "none") else s
    sig = f"{_s(row.get('clinvar_significance'))} {_s(row.get('clinvar_oncogenicity'))}"
    if _s(row.get("civic_best_predictive_level")):
        return "Actionable (predictive evidence)"
    if re.search(r"ncogenic", sig, re.I):
        return "Oncogenic (ClinVar)"
    mc = str(row.get("molecular_consequence", "")).lower()
    if any(k in mc for k in ("nonsense", "frameshift", "stop", "splice")):
        return "Predicted loss-of-function"
    if "inframe" in mc or "insertion" in mc or "deletion" in mc:
        return "In-frame indel"
    if re.search(r"athogenic", sig, re.I):
        return "Pathogenic (ClinVar)"
    if "missense" in mc:
        return "Missense (uncertain/other)"
    return "Other annotated"


def build(gene, outdir):
    cv_path = os.path.join(outdir, f"{gene}_clinvar_full_catalog.csv")
    ev_path = os.path.join(outdir, f"{gene}_civic_evidence_long.csv")
    var_path = os.path.join(outdir, f"{gene}_civic_variants.csv")

    cv = pd.read_csv(cv_path, dtype=str) if os.path.exists(cv_path) else pd.DataFrame()
    ev = pd.read_csv(ev_path, dtype=str) if os.path.exists(ev_path) else pd.DataFrame()
    civ_vars = pd.read_csv(var_path, dtype=str) if os.path.exists(var_path) else pd.DataFrame()

    civic_summary = summarize_civic_by_variant(ev)
    clinvar_summary = summarize_clinvar_by_protein(cv)

    # clinvar_ids bridge: CIViC variant -> ClinVar Variation IDs (== ClinVar uid)
    cv_uids = set(cv["variation_id"].astype(str)) if not cv.empty else set()
    civ_clinvar_map = {}
    if not civ_vars.empty and "clinvar_ids" in civ_vars.columns:
        vcol = "variant" if "variant" in civ_vars.columns else "name"
        for _, r in civ_vars.iterrows():
            ids = [x for x in re.split(r"[,\s;]+", str(r.get("clinvar_ids", ""))) if x]
            civ_clinvar_map[str(r.get(vcol, ""))] = [i for i in ids if i in cv_uids]

    # --- assemble master allele table ------------------------------------
    records = {}

    # 1) CIViC-derived alleles (primary actionability)
    for _, r in civic_summary.iterrows():
        var = r["variant"]
        spec = is_specific_allele(var)
        rec = {
            "variant": var,
            "residue_position": pos_from_name(var) if spec else np.nan,
            "is_specific_allele": bool(spec),
            "source": "CIViC",
            "civic_best_level": r["civic_best_level"],
            "civic_best_predictive_level": r["civic_best_predictive_level"],
            "n_evidence": r["n_evidence"],
            "n_predictive": r["n_predictive"],
            "evidence_levels": r["evidence_levels"],
            "evidence_types": r["evidence_types"],
            "sensitivity_therapies": r["sensitivity_therapies"],
            "resistance_therapies": r["resistance_therapies"],
            "diseases": r["diseases"],
        }
        linked = civ_clinvar_map.get(var, [])
        rec["clinvar_variation_ids"] = ";".join(linked)
        records[var] = rec

    # 2) merge ClinVar per-protein summaries onto matching alleles
    cv_by_pc = {r["protein_change"]: r for _, r in clinvar_summary.iterrows()}
    for var, rec in records.items():
        method = None
        pc = var if var in cv_by_pc else None
        # a) clinvar_id link present?
        has_cid = bool(rec.get("clinvar_variation_ids"))
        if pc and has_cid:
            method = "clinvar_id+protein_change"
        elif has_cid:
            method = "clinvar_id"
        elif pc:
            method = "protein_change"
        elif categorical_region(var):
            method = "categorical"
        else:
            method = "civic_only"
        if pc:
            cvr = cv_by_pc[pc]
            rec["clinvar_significance"] = cvr["clinvar_significance"]
            rec["clinvar_oncogenicity"] = cvr["clinvar_oncogenicity"]
            rec["clinvar_n"] = cvr["clinvar_n"]
            rec["molecular_consequence"] = cvr["molecular_consequence"]
            # Consistency: trust the ALLELE's own codon, not the linked record.
            if rec.get("residue_position") in (None, np.nan) or pd.isna(rec.get("residue_position")):
                rec["residue_position"] = cvr["residue_position"]
        rec["join_method"] = method

    # 3) add ClinVar-only specific alleles not already present (Tier 2/3)
    present = set(records)
    for _, r in clinvar_summary.iterrows():
        pc = r["protein_change"]
        if pc in present or not is_specific_allele(pc):
            continue
        records[pc] = {
            "variant": pc,
            "residue_position": r["residue_position"],
            "is_specific_allele": True,
            "source": "ClinVar",
            "clinvar_significance": r["clinvar_significance"],
            "clinvar_oncogenicity": r["clinvar_oncogenicity"],
            "clinvar_n": r["clinvar_n"],
            "molecular_consequence": r["molecular_consequence"],
            "clinvar_variation_ids": r["clinvar_variation_ids"],
            "join_method": "clinvar_only",
            "n_evidence": 0, "n_predictive": 0,
        }

    master = pd.DataFrame(list(records.values()))
    if master.empty:
        print("[build] WARNING: no alleles assembled.")
        master.to_csv(os.path.join(outdir, f"{gene}_allelic_series_master.csv"), index=False)
        return master

    # --- mechanism + tiers -----------------------------------------------
    master["mechanism"] = master.apply(infer_mechanism, axis=1)

    def _s(x):
        """Safe string: treat NaN / 'nan' / '' as empty (falsy)."""
        if x is None:
            return ""
        s = str(x).strip()
        return "" if s.lower() in ("nan", "none", "") else s

    def tier(row):
        pred = _s(row.get("civic_best_predictive_level"))
        has_ther = bool(_s(row.get("sensitivity_therapies")) or
                        _s(row.get("resistance_therapies")))
        if pred and has_ther:
            return 1
        try:
            nev = int(float(row.get("n_evidence", 0) or 0))
        except (ValueError, TypeError):
            nev = 0
        if nev > 0:
            return 2
        sig = _s(row.get("clinvar_significance"))
        if re.search(r"athogenic", sig, re.I) and "conflicting" not in sig.lower():
            return 2
        return 3

    master["tier"] = master.apply(tier, axis=1)

    # --- residue-position consistency assertion (the critical fix) --------
    spec = master[master["is_specific_allele"] == True].copy()  # noqa: E712
    spec["_pos_from_name"] = spec["variant"].map(pos_from_name)
    bad = spec[
        spec["_pos_from_name"].notna()
        & spec["residue_position"].notna()
        & (spec["_pos_from_name"].astype("Int64") != spec["residue_position"].astype("Int64"))
    ]
    if len(bad):
        print(f"[build] FIXING {len(bad)} position mismatches (using name-derived codon)")
        for idx in bad.index:
            master.loc[master["variant"] == spec.loc[idx, "variant"], "residue_position"] = \
                spec.loc[idx, "_pos_from_name"]
    # Re-verify -> must be zero.
    spec2 = master[master["is_specific_allele"] == True].copy()  # noqa: E712
    spec2["_pn"] = spec2["variant"].map(pos_from_name)
    remaining = int((
        spec2["_pn"].notna() & spec2["residue_position"].notna()
        & (spec2["_pn"].astype("Int64") != spec2["residue_position"].astype("Int64"))
    ).sum())
    print(f"[build] residue-position discrepancies remaining: {remaining} (must be 0)")

    # order columns
    master = master.sort_values(["tier", "residue_position"], na_position="last")
    out_csv = os.path.join(outdir, f"{gene}_allelic_series_master.csv")
    master.to_csv(out_csv, index=False)
    print(f"[build] wrote {len(master)} alleles -> {out_csv}")

    # --- summary stats ----------------------------------------------------
    stats = {
        "gene": gene,
        "clinvar_total": int(len(cv)),
        "civic_variants": int(civ_vars.shape[0]) if not civ_vars.empty else 0,
        "civic_evidence_single": int(_as_bool(ev["is_single_variant_evidence"]).sum())
        if (not ev.empty and "is_single_variant_evidence" in ev.columns) else 0,
        "alleles_total": int(len(master)),
        "specific_alleles": int((master["is_specific_allele"] == True).sum()),  # noqa: E712
        "tier1": int((master["tier"] == 1).sum()),
        "tier2": int((master["tier"] == 2).sum()),
        "tier3": int((master["tier"] == 3).sum()),
        "position_discrepancies": remaining,
        "join_methods": master["join_method"].value_counts().to_dict(),
    }
    with open(os.path.join(outdir, f"{gene}_summary_stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    print(f"[build] stats: {json.dumps(stats)}")
    return master


def main():
    ap = argparse.ArgumentParser(description="Build tiered allelic series from ClinVar+CIViC.")
    ap.add_argument("--gene", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    build(args.gene.upper(), args.outdir)


if __name__ == "__main__":
    sys.exit(main())
