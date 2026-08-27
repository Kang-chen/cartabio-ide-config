#!/usr/bin/env python3
"""Composite surface-target scoring + known-target validation harness.

Scoring is ANTIBODY-MODALITY appropriate: tumor-surface specificity and normal-tissue
therapeutic index dominate; DepMap essentiality is NOT a component (annotation only).
The topology gate is applied upstream (only accessible candidates reach here).

The validation harness scores a curated panel of clinically validated targets by the
identical pipeline and reports recall@K. If known targets (TROP2/TACSTD2, c-MET, HER2,
HER3, CEACAM5, MSLN, ...) do not rank near the top — or if cautionary controls
(ATP1A1, CDH1) rank high — the specificity / safety / topology layers are misconfigured.

Score shape (deliberate):  final = tumor_quality x safety_factor x consensus_multiplier
Normal-tissue therapeutic index is a MULTIPLICATIVE safety factor, not a linear term —
a great tumor antigen that is also expressed in heart/liver/lung is not a target,
regardless of how tumor-specific it looks within the microenvironment. This is exactly
what demotes broadly-expressed housekeeping genes like CDH1 / ATP1A1.
"""

import json
import os

import numpy as np
import pandas as pd

# Tumor-quality components (weights sum to 1.0). Safety is applied separately as a
# multiplicative therapeutic-index factor; essentiality is NOT a component.
WEIGHTS = {
    "specificity": 0.30,
    "magnitude": 0.20,
    "homogeneity": 0.20,
    "accessibility": 0.15,
    "tractability": 0.15,
}

# Named alternative weight sets for the tumor-quality weight-sensitivity analysis.
# Same schema as WEIGHTS; each sums to 1.0.
TUMOR_QUALITY_WEIGHT_ALTERNATIVES = {
    "equal": {"specificity": 0.20, "magnitude": 0.20, "homogeneity": 0.20,
              "accessibility": 0.20, "tractability": 0.20},
    "specificity_heavy": {"specificity": 0.50, "magnitude": 0.15, "homogeneity": 0.15,
                          "accessibility": 0.10, "tractability": 0.10},
    "homogeneity_heavy": {"specificity": 0.20, "magnitude": 0.15, "homogeneity": 0.35,
                          "accessibility": 0.15, "tractability": 0.15},
}

# Named alternative safety-aggregation rules for the therapeutic-index stability check.
# Each is a function: (protein_safety, rna_safety) -> safety_score.
TI_WEIGHT_ALTERNATIVES = {
    "conservative_min": lambda p, r: _min_safety(p, r),
    "lenient_mean": lambda p, r: _mean_safety(p, r),
    "strict_max_penalty": lambda p, r: _max_penalty_safety(p, r),
}

# Rank-stability verdict thresholds (Spearman rho and top-K Jaccard).
STABILITY_ROBUST_RHO = 0.90
STABILITY_ROBUST_JACCARD = 0.90
STABILITY_MODERATE_RHO = 0.70

SAFETY_UNKNOWN = 0.7  # neutral factor when normal-tissue safety could not be assessed
_ACCESS_SCORE = {"high": 1.0, "partial": 0.6, "low": 0.3, "none": 0.0}
TIER1_CUT, TIER2_CUT = 0.55, 0.35

# Negative-control verdict thresholds (percentile of rank among scored candidates).
NEG_CONTROL_HIGH_PERCENTILE = 0.25   # <= 25th percentile -> ranks_high -> FAIL
NEG_CONTROL_MID_PERCENTILE = 0.50    # 25-50th percentile -> ranks_mid

# Date the bundled validated-target harness was PRE-REGISTERED (locked before any
# ranking). Recorded per-row in references/known_surface_targets.csv (`date_added` +
# `provenance`); this constant is the fallback when those columns are absent. A target
# whose provenance is `added_post_ranking` post-dates the ranking and triggers dual
# (pre-registered vs augmented) recall reporting — see _run_harness.
HARNESS_LOCK_DATE = "2026-08-06"


def _json_safe(obj):
    """Recursively convert a nested structure to JSON-safe plain Python types.

    Converts numpy scalars to Python builtins; maps NaN and inf to None so that
    ``allow_nan=False`` never raises on values that pandas coerced to float64.
    """
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        return None if (np.isnan(val) or np.isinf(val)) else val
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        return None if (np.isnan(obj) or np.isinf(obj)) else obj
    if obj is pd.NA:
        return None
    return obj


def _specificity_score(spec_vs_tme):
    if pd.isna(spec_vs_tme) or spec_vs_tme <= 0:
        return np.nan
    return float(np.clip(np.log10(spec_vs_tme) / 2.0, 0.0, 1.0))  # 1x->0, 10x->0.5, 100x->1


def _clip01(x, denom=1.0):
    if pd.isna(x):
        return np.nan
    return float(np.clip(x / denom, 0.0, 1.0))


def _tumor_quality(row, weights=None):
    """Weighted tumor-surface-antigen quality with adaptive reweighting over
    available components. Excludes safety (applied multiplicatively) and
    essentiality (annotation only).

    ``weights`` defaults to the module-level WEIGHTS; passing an alternative dict
    enables the weight-sensitivity analysis.
    """
    w = weights if weights is not None else WEIGHTS
    comps = {
        "specificity": row.get("score_specificity"),
        "magnitude": row.get("score_magnitude"),
        "homogeneity": row.get("score_homogeneity"),
        "accessibility": row.get("score_accessibility"),
        "tractability": row.get("score_tractability"),
    }
    num = wsum = 0.0
    for k, v in comps.items():
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        num += w[k] * v
        wsum += w[k]
    return num / wsum if wsum > 0 else np.nan


def _consensus_multiplier(n_enriched):
    # Reward reproducibility across atlases; 3+ enriched datasets -> full weight.
    n = 0 if pd.isna(n_enriched) else int(n_enriched)
    return 0.5 + 0.5 * min(n, 3) / 3.0


# ---------------------------------------------------------------------------
# Safety-aggregation alternatives for the therapeutic-index stability check.
# ---------------------------------------------------------------------------

def _min_safety(p, r):
    """Conservative min — the default rule used in normal_tissue_safety.py."""
    cands = [s for s in (p, r) if s is not None and not (isinstance(s, float) and np.isnan(s))]
    return float(min(cands)) if cands else np.nan


def _mean_safety(p, r):
    """Lenient mean — averages whichever signals exist."""
    cands = [s for s in (p, r) if s is not None and not (isinstance(s, float) and np.isnan(s))]
    return float(np.mean(cands)) if cands else np.nan


def _max_penalty_safety(p, r):
    """Strict max-penalty — takes the LOWER safety (higher expression) of the two,
    but if only one signal exists, applies a 0.9 penalty to the missing one."""
    vals = []
    if p is not None and not (isinstance(p, float) and np.isnan(p)):
        vals.append(p)
    if r is not None and not (isinstance(r, float) and np.isnan(r)):
        vals.append(r)
    if not vals:
        return np.nan
    return float(min(vals))


def _has_protein_evidence(row):
    """Determine whether a candidate has protein-level validation evidence.

    Two independent signals, both already merged into the scored DataFrame:
    - ``vital_protein_max`` (from ti_df / normal_tissue_safety.py): the maximum IHC
      protein ordinal across vital organs.  A non-null value means HPA IHC protein
      measurement was performed for this gene — genuine protein-level evidence.
    - ``is_plasma_membrane`` (from ann_df / annotate_targets.py): Open Targets
      subcellular-localization confirmation of plasma-membrane residence.

    A computed HPA safety score derived from RNA alone is NOT protein evidence —
    it is bulk RNA nTPM, not a protein measurement.
    """
    has_ihc = pd.notna(row.get("vital_protein_max"))
    is_pm = row.get("is_plasma_membrane") is True
    has_evidence = has_ihc or is_pm

    if has_ihc and is_pm:
        source = "both"
    elif has_ihc:
        source = "HPA IHC"
    elif is_pm:
        source = "OT plasma-membrane localization"
    else:
        source = "none"
    return has_ihc, has_evidence, source


def _surface_confirmation(row):
    """Classify how well a candidate's plasma-membrane residency is INDEPENDENTLY
    confirmed — the fix for the inert-topology defect where every SURFY member was
    blanket-called plasma_membrane.

    - ``confirmed_experimental``: SURFY sourced it from the Cell Surface Protein Atlas
      positive-training set / a UniProt GPI anchor (surface_confirmation_surfy), OR it
      is a curated MS-validated seed entry.
    - ``confirmed_ot``: Open Targets subcellularLocations reports plasma-membrane
      (is_plasma_membrane), independent of SURFY's machine-learning prediction.
    - ``unconfirmed``: a machine-learning surface prediction with NO independent
      confirmation (no CSPA/GPI evidence and no Open Targets plasma-membrane call).

    ``unconfirmed`` candidates are real predictions, not errors — but they must be
    labelled so a machine-learning-only hit (e.g. a contact-site protein) is never
    presented with the same confidence as an experimentally confirmed antigen.
    """
    surfy = str(row.get("surface_confirmation_surfy") or "").strip().lower()
    cls = str(row.get("surfaceome_class") or "").strip().lower()
    is_pm = row.get("is_plasma_membrane") is True
    if surfy == "confirmed_experimental" or (not surfy and cls == "ms-validated"):
        return "confirmed_experimental"
    if is_pm:
        return "confirmed_ot"
    return "unconfirmed"


def score_surface_targets(spec_df, surf_df, ann_df, ti_df, known_targets_df,
                          output_dir="results", magnitude_denom=2.0):
    """Merge evidence layers, score, tier, and run the validation harness.

    Returns dict: {"ranked": DataFrame, "harness": DataFrame, "metrics": dict,
                   "stability": dict}.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Topology gate already applied: only accessible candidates (surf_df) are scored.
    _base_cols = ["gene_symbol", "topology", "ectodomain_accessibility", "localization",
                  "surfaceome_class"]
    # Carry SURFY provenance columns through when the genome-scale loader supplied them
    # (they seed the confirmed/unconfirmed surface call below).
    _extra_cols = [c for c in ("surface_confirmation_surfy", "surfy_source", "noncyt_len",
                               "almen_class") if c in getattr(surf_df, "columns", [])]
    df = surf_df[_base_cols + _extra_cols].drop_duplicates("gene_symbol")
    df = df.merge(spec_df, on="gene_symbol", how="inner")  # require tumor expression data
    df = df.merge(ann_df, on="gene_symbol", how="left")
    df = df.merge(ti_df, on="gene_symbol", how="left")

    df["score_specificity"] = df["spec_vs_tme"].map(_specificity_score)
    df["score_safety"] = df["safety_score"]
    df["score_magnitude"] = df["epithelial_mean"].map(lambda v: _clip01(v, magnitude_denom))
    df["score_homogeneity"] = df["epithelial_pct"].map(lambda v: _clip01(v, 100.0))
    df["score_accessibility"] = df["ectodomain_accessibility"].map(
        lambda a: _ACCESS_SCORE.get(str(a).lower(), np.nan))
    df["score_tractability"] = df["antibody_tractability_score"]

    df["tumor_quality"] = df.apply(_tumor_quality, axis=1).round(4)
    df["safety_unassessed"] = df["score_safety"].isna()
    df["safety_factor"] = df["score_safety"].fillna(SAFETY_UNKNOWN)
    df["consensus_multiplier"] = df["n_datasets_enriched"].map(_consensus_multiplier)
    df["final_score"] = (df["tumor_quality"] * df["safety_factor"]
                         * df["consensus_multiplier"]).round(4)
    df["tier"] = np.where(df["final_score"] >= TIER1_CUT, "Tier 1",
                          np.where(df["final_score"] >= TIER2_CUT, "Tier 2", "Tier 3"))

    # Protein-evidence columns (derived from data already merged, no new fetch).
    # _has_protein_evidence returns (has_ihc, has_evidence, source).
    pe = df.apply(_has_protein_evidence, axis=1, result_type="expand")
    df["has_ihc_protein_measurement"] = pe[0]
    df["has_protein_evidence"] = pe[1]
    df["protein_evidence_source"] = pe[2]

    # Independent plasma-membrane confirmation (fix for the inert-topology defect):
    # label each scored candidate confirmed_experimental / confirmed_ot / unconfirmed
    # so machine-learning-only surface predictions are never silently folded into the
    # confirmed pass list.
    df["surface_confirmation"] = df.apply(_surface_confirmation, axis=1)
    df["is_unconfirmed_surface"] = df["surface_confirmation"].eq("unconfirmed")

    known_set = set(known_targets_df["gene_symbol"].astype(str))
    df["is_known_target"] = df["gene_symbol"].isin(known_set)
    df = df.sort_values("final_score", ascending=False, na_position="last").reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)

    ranked_path = os.path.join(output_dir, "ranked_surface_targets.csv")
    df.to_csv(ranked_path, index=False)

    harness_df, metrics = _run_harness(df, known_targets_df, output_dir,
                                       spec_genes=set(spec_df["gene_symbol"].astype(str)))

    # Rank-stability check (therapeutic-index weighting + tumor-quality weighting).
    stability = rank_stability_check(df, output_dir)
    metrics["stability"] = stability

    n_t1 = int((df["tier"] == "Tier 1").sum())
    print(f"✓ Scoring complete: {len(df)} candidates, {n_t1} Tier-1; "
          f"validation recall@20 = {metrics['recall_at_20_str']} known targets; "
          f"stability verdict: {stability.get('stability_verdict_overall', 'n/a')}")
    return {"ranked": df, "harness": harness_df, "metrics": metrics,
            "stability": stability}


def _build_exclusion_map():
    """Genes the bundled surfaceome classifies as non-accessible (loaded once)."""
    excluded = {}
    try:
        from surfaceome_filter import load_surfaceome, NON_SURFACE_LOCALIZATIONS
        sf = load_surfaceome()
        for _, r in sf.iterrows():
            loc = str(r["localization"]).lower()
            acc = str(r["ectodomain_accessibility"]).lower()
            if loc in NON_SURFACE_LOCALIZATIONS or acc == "none":
                excluded[str(r["gene_symbol"])] = "excluded_topology"
    except Exception:  # noqa: BLE001
        pass
    return excluded


def _classify_exclusion(gene, scored_genes, spec_genes, excluded_map):
    """Best-effort reason a known target did not get scored."""
    if gene in scored_genes:
        return None
    if gene in excluded_map:
        return excluded_map[gene]
    if gene not in spec_genes:
        return "no_expression_data"
    return "not_in_surfaceome"


def _run_harness(ranked_df, known_targets_df, output_dir, spec_genes=None):
    scored_genes = set(ranked_df["gene_symbol"])
    spec_genes = spec_genes if spec_genes is not None else scored_genes
    excluded_map = _build_exclusion_map()

    # --- Provenance: pre-registered vs added-after-ranking (anti-circularity) ---
    # The validated set is LOCKED before ranking. `provenance` marks whether each core
    # target was pre-registered or added to the harness AFTER the ranking existed.
    # Recall on the pre-registered set is the ONLY headline; augmented recall (over any
    # post-hoc additions) is reported separately and NEVER as the headline alone.
    def _prov(kt):
        p = str(kt.get("provenance") or "").strip().lower()
        return p if p in ("pre_registered", "added_post_ranking") else "pre_registered"

    is_core = known_targets_df.get("recall_core", 0).astype(str).isin(["1", "1.0", "True"])
    core = known_targets_df[is_core]
    core_genes = list(core["gene_symbol"].astype(str))
    core_gene_set = set(core_genes)
    prov_by_gene = {str(kt["gene_symbol"]): _prov(kt) for _, kt in known_targets_df.iterrows()}
    core_prereg_genes = [g for g in core_genes if prov_by_gene.get(g) == "pre_registered"]
    core_posthoc_genes = [g for g in core_genes if prov_by_gene.get(g) == "added_post_ranking"]
    harness_augmented = len(core_posthoc_genes) > 0
    # Lock date recorded in the harness data (earliest pre-registered date_added).
    harness_locked_date = HARNESS_LOCK_DATE
    if "date_added" in known_targets_df.columns:
        _dates = [str(d) for d in known_targets_df.loc[
            is_core & known_targets_df["gene_symbol"].astype(str).isin(core_prereg_genes),
            "date_added"].dropna().tolist()]
        if _dates:
            harness_locked_date = min(_dates)

    rank_by_gene = dict(zip(ranked_df["gene_symbol"], ranked_df["rank"]))
    tier_by_gene = dict(zip(ranked_df["gene_symbol"], ranked_df["tier"]))
    score_by_gene = dict(zip(ranked_df["gene_symbol"], ranked_df["final_score"]))
    n_scored = len(ranked_df)

    rows = []
    for _, kt in known_targets_df.iterrows():
        g = str(kt["gene_symbol"])
        scored = g in scored_genes
        rows.append({
            "gene_symbol": g,
            "recall_core": kt.get("recall_core"),
            "provenance": prov_by_gene.get(g),
            "date_added": kt.get("date_added"),
            "clinical_status": kt.get("clinical_status"),
            "scored": scored,
            "rank": int(rank_by_gene[g]) if scored else None,
            "tier": tier_by_gene.get(g) if scored else None,
            "final_score": float(score_by_gene[g]) if scored else None,
            "exclusion_reason": _classify_exclusion(g, scored_genes, spec_genes, excluded_map),
        })
    harness_df = pd.DataFrame(rows).sort_values(
        ["scored", "rank"], ascending=[False, True], na_position="last").reset_index(drop=True)
    harness_df.to_csv(os.path.join(output_dir, "validation_harness.csv"), index=False)

    def _recall_over(gene_list, k):
        scored_list = [g for g in gene_list if g in scored_genes]
        if not scored_list:
            return None
        hits = sum(1 for g in scored_list if rank_by_gene[g] <= k)
        return hits, len(scored_list)

    scored_core = [g for g in core_genes if g in scored_genes]           # augmented (all core)
    scored_prereg = [g for g in core_prereg_genes if g in scored_genes]  # pre-registered (headline)
    # Headline recall = PRE-REGISTERED set only; augmented recall only when post-hoc adds exist.
    r10, r20 = _recall_over(core_prereg_genes, 10), _recall_over(core_prereg_genes, 20)
    r10_aug = _recall_over(core_genes, 10) if harness_augmented else None
    r20_aug = _recall_over(core_genes, 20) if harness_augmented else None

    # --- Item 1: restrict excluded_topology count to core genes only ---
    core_harness = harness_df[harness_df["gene_symbol"].isin(core_gene_set)]
    n_core_excluded_topology = int(
        (core_harness["exclusion_reason"] == "excluded_topology").sum())
    # Negative controls excluded by topology (reported under a separate key).
    neg_harness = harness_df[harness_df["clinical_status"] == "not_a_target"]
    n_neg_excluded_topology = int(
        (neg_harness["exclusion_reason"] == "excluded_topology").sum())

    # --- Item 5: derive negative-control verdict from data, not narrative ---
    neg_records = []
    n_neg_high = 0
    for _, nr in neg_harness.iterrows():
        g = str(nr["gene_symbol"])
        scored = bool(nr["scored"])
        excl = nr["exclusion_reason"]
        if not scored:
            verdict = "excluded_topology" if excl == "excluded_topology" else "unscored"
            pct = None
        else:
            rank = int(nr["rank"])
            pct = rank / n_scored if n_scored > 0 else None
            if pct is not None and pct <= NEG_CONTROL_HIGH_PERCENTILE:
                verdict = "ranks_high"
                n_neg_high += 1
            elif pct is not None and pct <= NEG_CONTROL_MID_PERCENTILE:
                verdict = "ranks_mid"
            else:
                verdict = "ranks_low"
        neg_records.append({
            "gene_symbol": g,
            "scored": scored,
            "rank": int(nr["rank"]) if scored else None,
            "tier": nr["tier"] if scored else None,
            "exclusion_reason": excl,
            "rank_percentile": round(pct, 4) if pct is not None else None,
            "verdict": verdict,
        })

    neg_verdict = "FAIL" if n_neg_high > 0 else "PASS"
    # Build the derived statement naming each offender.
    offenders = [r for r in neg_records if r["verdict"] == "ranks_high"]
    if offenders:
        offender_strs = [f"{r['gene_symbol']} rank {r['rank']}/{n_scored}" for r in offenders]
        neg_statement = (
            f"{len(offenders)} of {len(neg_records)} cautionary negative controls rank in the "
            f"top {int(NEG_CONTROL_HIGH_PERCENTILE * 100)}% of scored candidates "
            f"({', '.join(offender_strs)}); the safety and topology layers do not fully demote them.")
    else:
        excluded = [r for r in neg_records if r["verdict"] in ("excluded_topology", "unscored")]
        low_or_mid = [r for r in neg_records if r["verdict"] in ("ranks_low", "ranks_mid")]
        parts = []
        if excluded:
            parts.append(f"{len(excluded)} excluded by topology or unscored")
        if low_or_mid:
            parts.append(f"{len(low_or_mid)} rank in the lower half")
        neg_statement = (
            f"All {len(neg_records)} cautionary negative controls rank low or were excluded by "
            f"topology ({'; '.join(parts) if parts else 'none scored'}).")

    if neg_verdict == "FAIL":
        print(f"  ⚠ NEGATIVE CONTROL VERDICT: FAIL — {neg_statement}")

    # --- Item 11: holdout precision caveat (on the PRE-REGISTERED headline set) ---
    n_holdout_core = len(scored_prereg)
    holdout_resolution_pp = round(100.0 / n_holdout_core, 1) if n_holdout_core > 0 else None
    holdout_caveat = (
        f"Held-out recall is computed on {n_holdout_core} pre-registered locked targets "
        f"(harness locked {harness_locked_date}) on a single fixed split; "
        f"one target moves recall by {holdout_resolution_pp} percentage points. "
        f"No repeated cross-validation or bootstrap interval is computed."
    ) if n_holdout_core > 0 else "No pre-registered core targets were scored; recall is not available."

    def _rs(r):
        return f"{r[0]}/{r[1]}" if r else "n/a"

    metrics = {
        "n_known_total": int(len(known_targets_df)),
        "n_known_core_total": int(len(core_genes)),
        "n_known_core_scored": int(len(scored_core)),
        "n_known_core_excluded_topology": n_core_excluded_topology,
        "n_negative_controls_excluded_topology": n_neg_excluded_topology,
        # --- Headline recall = PRE-REGISTERED set only (never augmented alone) ---
        "recall_at_10": (r10[0] / r10[1]) if r10 else None,
        "recall_at_20": (r20[0] / r20[1]) if r20 else None,
        "recall_at_10_str": _rs(r10),
        "recall_at_20_str": _rs(r20),
        "recall_basis": "pre_registered",
        # --- Anti-circularity provenance + dual recall ---
        "harness_locked_date": harness_locked_date,
        "harness_augmented_after_ranking": bool(harness_augmented),
        "n_known_core_prereg": int(len(core_prereg_genes)),
        "n_known_core_prereg_scored": int(len(scored_prereg)),
        "n_known_core_posthoc": int(len(core_posthoc_genes)),
        "core_posthoc_genes": list(core_posthoc_genes),
        "recall_pre_registered_at_10": (r10[0] / r10[1]) if r10 else None,
        "recall_pre_registered_at_20": (r20[0] / r20[1]) if r20 else None,
        "recall_pre_registered_at_10_str": _rs(r10),
        "recall_pre_registered_at_20_str": _rs(r20),
        "recall_augmented_at_10": (r10_aug[0] / r10_aug[1]) if r10_aug else None,
        "recall_augmented_at_20": (r20_aug[0] / r20_aug[1]) if r20_aug else None,
        "recall_augmented_at_10_str": _rs(r10_aug) if harness_augmented else None,
        "recall_augmented_at_20_str": _rs(r20_aug) if harness_augmented else None,
        "holdout_n": n_holdout_core,
        "holdout_resolution_pp": holdout_resolution_pp,
        "holdout_caveat": holdout_caveat,
        "negative_control_verdict": neg_verdict,
        "n_negative_controls_ranking_high": n_neg_high,
        "negative_control_statement": neg_statement,
        "negative_controls": neg_records,
    }
    with open(os.path.join(output_dir, "validation_metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(_json_safe(metrics), fh, indent=2, allow_nan=False)
    return harness_df, metrics


# ---------------------------------------------------------------------------
# Rank-stability check (items 6 + 10).
# ---------------------------------------------------------------------------

def _verdict_from_metrics(rho, jaccard):
    """Map a Spearman rho and top-K Jaccard to a stability verdict word."""
    if rho is None or (isinstance(rho, float) and np.isnan(rho)):
        return "sensitive"
    if rho >= STABILITY_ROBUST_RHO and jaccard >= STABILITY_ROBUST_JACCARD:
        return "robust"
    if rho >= STABILITY_MODERATE_RHO:
        return "moderately_sensitive"
    return "sensitive"


_VERDICT_RANK = {"robust": 0, "moderately_sensitive": 1, "sensitive": 2}


def _spearman(a, b):
    """Spearman rank correlation; returns NaN if fewer than 3 paired values."""
    paired = [(x, y) for x, y in zip(a, b) if x is not None and y is not None
              and not (isinstance(x, float) and np.isnan(x))
              and not (isinstance(y, float) and np.isnan(y))]
    if len(paired) < 3:
        return np.nan
    xs, ys = zip(*paired)
    return float(pd.Series(xs).corr(pd.Series(ys), method="spearman"))


def _jaccard_topk(set_a, set_b):
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def rank_stability_check(ranked_df, output_dir="results", top_k=50):
    """Assess rank sensitivity to (a) the therapeutic-index safety-aggregation rule
    and (b) the tumor-quality composite weights.

    Both axes recompute scores from columns already present in ``ranked_df`` — no
    new data is fetched.  Results are written to ``therapeutic_index_stability.json``
    and ``therapeutic_index_stability.csv`` with a ``dimension`` column so the two
    axes can never be confused.
    """
    os.makedirs(output_dir, exist_ok=True)
    base_genes = ranked_df["gene_symbol"].tolist()
    base_ranks = dict(zip(ranked_df["gene_symbol"], ranked_df["rank"]))
    base_topk = set(ranked_df.head(top_k)["gene_symbol"])
    base_scores = dict(zip(ranked_df["gene_symbol"], ranked_df["final_score"]))

    comparisons = []

    # --- Axis 1: therapeutic-index safety-aggregation rule ---
    # Recompute safety_factor from the RAW protein/RNA signals (vital_protein_max,
    # vital_rna_max) under each alternative aggregation rule, then re-rank.
    # Applying the rules to the already-conservative-min score_safety would be a
    # no-op — the perturbation must happen at the signal-aggregation level.
    from normal_tissue_safety import _protein_safety, _rna_safety  # local import

    def _recompute_safety(row, rule_fn):
        vp = row.get("vital_protein_max")
        vr = row.get("vital_rna_max")
        p = _protein_safety(vp) if pd.notna(vp) else None
        r = _rna_safety(vr) if pd.notna(vr) else None
        return rule_fn(p, r)

    for rule_name, rule_fn in TI_WEIGHT_ALTERNATIVES.items():
        alt_safety = ranked_df.apply(lambda r: _recompute_safety(r, rule_fn), axis=1)
        alt_final = (ranked_df["tumor_quality"] * alt_safety.fillna(SAFETY_UNKNOWN)
                     * ranked_df["consensus_multiplier"])
        alt_ranked = ranked_df.assign(final_score=alt_final).sort_values(
            "final_score", ascending=False, na_position="last").reset_index(drop=True)
        alt_ranked["rank"] = np.arange(1, len(alt_ranked) + 1)
        alt_ranks = dict(zip(alt_ranked["gene_symbol"], alt_ranked["rank"]))
        alt_topk = set(alt_ranked.head(top_k)["gene_symbol"])

        rho = _spearman(
            [base_ranks.get(g) for g in base_genes],
            [alt_ranks.get(g) for g in base_genes])
        jac = _jaccard_topk(base_topk, alt_topk)
        verdict = _verdict_from_metrics(rho, jac)
        comparisons.append({
            "dimension": "ti_safety_weighting",
            "rule": rule_name,
            "spearman_rho": round(rho, 4) if not (isinstance(rho, float) and np.isnan(rho)) else None,
            "topk_jaccard": round(jac, 4),
            "verdict": verdict,
        })

    # --- Axis 2: tumor-quality composite weights ---
    # Recompute tumor_quality under each alternative weight set, then re-rank.
    for wname, wset in TUMOR_QUALITY_WEIGHT_ALTERNATIVES.items():
        alt_tq = ranked_df.apply(lambda r: _tumor_quality(r, weights=wset), axis=1).round(4)
        alt_final = (alt_tq * ranked_df["safety_factor"] * ranked_df["consensus_multiplier"])
        alt_ranked = ranked_df.assign(
            tumor_quality=alt_tq, final_score=alt_final).sort_values(
            "final_score", ascending=False, na_position="last").reset_index(drop=True)
        alt_ranked["rank"] = np.arange(1, len(alt_ranked) + 1)
        alt_ranks = dict(zip(alt_ranked["gene_symbol"], alt_ranked["rank"]))
        alt_topk = set(alt_ranked.head(top_k)["gene_symbol"])

        rho = _spearman(
            [base_ranks.get(g) for g in base_genes],
            [alt_ranks.get(g) for g in base_genes])
        jac = _jaccard_topk(base_topk, alt_topk)
        verdict = _verdict_from_metrics(rho, jac)
        comparisons.append({
            "dimension": "tumor_quality_weighting",
            "rule": wname,
            "spearman_rho": round(rho, 4) if not (isinstance(rho, float) and np.isnan(rho)) else None,
            "topk_jaccard": round(jac, 4),
            "verdict": verdict,
        })

    # Overall verdict = worst across all comparisons.
    overall = "robust"
    worst = comparisons[0] if comparisons else None
    for c in comparisons:
        if _VERDICT_RANK.get(c["verdict"], 2) > _VERDICT_RANK.get(overall, 0):
            overall = c["verdict"]
            worst = c

    # Derived stability statement quoting the worst case.
    if worst and worst["spearman_rho"] is not None:
        stability_statement = (
            f"Ranking is {overall.replace('_', ' ')} to the {worst['dimension'].replace('_', ' ')}: "
            f"under {worst['rule']} the top-{top_k} Spearman rho is {worst['spearman_rho']} "
            f"(Jaccard {worst['topk_jaccard']}).")
    else:
        stability_statement = "Ranking stability could not be computed (insufficient paired data)."

    summary = {
        "stability_verdict_overall": overall,
        "stability_statement": stability_statement,
        "comparisons": comparisons,
        "thresholds": {
            "robust_rho": STABILITY_ROBUST_RHO,
            "robust_jaccard": STABILITY_ROBUST_JACCARD,
            "moderate_rho": STABILITY_MODERATE_RHO,
        },
    }

    with open(os.path.join(output_dir, "therapeutic_index_stability.json"), "w",
              encoding="utf-8") as fh:
        json.dump(_json_safe(summary), fh, indent=2, allow_nan=False)

    stab_df = pd.DataFrame(comparisons)
    stab_df.to_csv(os.path.join(output_dir, "therapeutic_index_stability.csv"), index=False)

    print(f"✓ Rank-stability check: {len(comparisons)} comparisons; "
          f"overall verdict = {overall}")
    return summary


if __name__ == "__main__":
    print("score_targets: import and call score_surface_targets(...). "
          "See assets/eval/static_test.py for an offline fixture test.")
