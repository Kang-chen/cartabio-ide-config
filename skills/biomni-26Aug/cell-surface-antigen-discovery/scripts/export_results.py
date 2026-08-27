#!/usr/bin/env python3
"""Export all results + analysis objects for the cell-surface target discovery skill.

Writes the canonical CSVs, per-target evidence cards, a pickle of all analysis
objects (for downstream skills), a provenance manifest, a coverage report that
distinguishes annotation coverage from boolean positive rate, a pre-export
consistency gate, and a report_facts.json of pre-derived numbers and sentences.
"""

import json
import os
import pickle
from datetime import datetime

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# JSON safety — same sanitizer as score_targets.py (kept local to avoid a
# cross-module import that would couple export to the scorer).
# ---------------------------------------------------------------------------

def _json_safe(obj):
    """Recursively convert to JSON-safe plain Python; NaN/inf -> None."""
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


def _ranked(scores):
    if isinstance(scores, dict):
        return scores["ranked"], scores.get("harness"), scores.get("metrics", {}), \
            scores.get("stability", {})
    return scores, None, {}, {}


def _clean(v):
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, float) and np.isnan(v):
        return None
    return v


def _evidence_cards(ranked, top_n=25):
    cards = []
    cols = ["rank", "gene_symbol", "tier", "final_score", "tumor_quality",
            "safety_factor", "safety_unassessed", "spec_vs_tme", "epithelial_mean",
            "epithelial_pct", "n_datasets", "n_datasets_enriched", "consensus_fraction",
            "ectodomain_accessibility", "topology", "surface_confirmation",
            "is_unconfirmed_surface", "safety_score", "vital_organ_flag",
            "top_normal_tissues", "antibody_tractability_bucket", "has_known_drug",
            "max_clinical_phase", "depmap_mean_gene_effect", "is_known_target",
            "has_protein_evidence", "has_ihc_protein_measurement",
            "protein_evidence_source"]
    for _, r in ranked.head(top_n).iterrows():
        cards.append({c: _clean(r[c]) for c in cols if c in ranked.columns})
    return cards


# ---------------------------------------------------------------------------
# Coverage report (item 4) — annotation coverage vs boolean positive rate.
# ---------------------------------------------------------------------------

_COVERAGE_COLUMNS = [
    "spec_vs_tme", "epithelial_mean", "epithelial_pct", "n_datasets_enriched",
    "ectodomain_accessibility", "surface_confirmation", "is_unconfirmed_surface",
    "antibody_tractability_score",
    "antibody_tractability_bucket", "is_plasma_membrane", "safety_score",
    "safety_unassessed", "vital_organ_flag", "has_known_drug",
    "max_clinical_phase", "depmap_mean_gene_effect",
    "has_protein_evidence", "has_ihc_protein_measurement",
    "protein_evidence_source",
]


def _is_bool_column(series):
    """Heuristic: treat a column as boolean if its non-null values are all
    True/False (or 0/1 with a bool-like dtype)."""
    if series.dtype == bool:
        return True
    non_null = series.dropna()
    if len(non_null) == 0:
        return False
    unique = set(non_null.unique())
    return unique <= {True, False, 0, 1}


def coverage_report(ranked, output_dir="results"):
    """Emit coverage_report.csv distinguishing annotation coverage (how many
    candidates have any value) from positive rate (for boolean columns, how
    many are True).

    For every column in ``_COVERAGE_COLUMNS``:
    - ``n_annotated`` / ``percent_annotated`` — always ``series.notna().sum()``
    - ``n_positive`` / ``percent_positive`` — only for boolean columns; null
      otherwise.
    """
    n_total = len(ranked)
    rows = []
    for col in _COVERAGE_COLUMNS:
        if col not in ranked.columns:
            rows.append({
                "column": col, "n_total": n_total,
                "n_annotated": 0, "percent_annotated": 0.0,
                "n_positive": None, "percent_positive": None,
            })
            continue
        series = ranked[col]
        n_annotated = int(series.notna().sum())
        pct_annotated = round(100.0 * n_annotated / n_total, 1) if n_total else 0.0
        row = {
            "column": col, "n_total": n_total,
            "n_annotated": n_annotated, "percent_annotated": pct_annotated,
            "n_positive": None, "percent_positive": None,
        }
        if _is_bool_column(series):
            n_pos = int(series.fillna(False).astype(bool).sum())
            row["n_positive"] = n_pos
            row["percent_positive"] = round(100.0 * n_pos / n_total, 1) if n_total else 0.0
        rows.append(row)

    cov_df = pd.DataFrame(rows)
    cov_path = os.path.join(output_dir, "coverage_report.csv")
    cov_df.to_csv(cov_path, index=False)
    print(f"  Saved coverage report: {cov_path} ({len(cov_df)} columns)")
    return cov_df


# ---------------------------------------------------------------------------
# Pre-export consistency gate (item 8).
# ---------------------------------------------------------------------------

def _assert_export_consistent(ranked, harness, metrics):
    """Print a clearly marked warning block for each violated invariant.

    Does not raise — the run continues — but every warning is also returned as a
    list so it can be mirrored into report_facts.json and cannot be lost to
    scrollback.
    """
    warnings = []

    # (a) has_protein_evidence constant across all rows
    if "has_protein_evidence" in ranked.columns:
        n_unique = ranked["has_protein_evidence"].nunique(dropna=True)
        if n_unique <= 1:
            warnings.append(
                "has_protein_evidence is not discriminating in this run "
                "(constant across all rows); do not cite it as validation.")

    # (b) n_known_core_scored > n_known_core_total
    n_scored = metrics.get("n_known_core_scored")
    n_total = metrics.get("n_known_core_total")
    if n_scored is not None and n_total is not None and n_scored > n_total:
        warnings.append(
            f"n_known_core_scored ({n_scored}) > n_known_core_total ({n_total}): "
            "arithmetic contradiction in validation metrics.")

    # (c) any holdout gene also present in the tuning partition
    # (No holdout/tuning split exists in the current harness; this check is a
    # no-op guard for when one is added.)
    if harness is not None and "holdout" in harness.columns and "tuning" in harness.columns:
        holdout_genes = set(harness.loc[harness["holdout"] == True, "gene_symbol"])
        tuning_genes = set(harness.loc[harness["tuning"] == True, "gene_symbol"])
        overlap = holdout_genes & tuning_genes
        if overlap:
            warnings.append(
                f"Holdout/tuning partition overlap: {sorted(overlap)} appear in both.")

    # (d) negative_control_verdict == FAIL
    neg_verdict = metrics.get("negative_control_verdict")
    if neg_verdict == "FAIL":
        warnings.append(
            f"Negative-control verdict is FAIL: {metrics.get('negative_control_statement', '')}")

    # (e) n_tier1 + n_tier2 == 0
    n_t1 = int((ranked["tier"] == "Tier 1").sum()) if "tier" in ranked.columns else 0
    n_t2 = int((ranked["tier"] == "Tier 2").sum()) if "tier" in ranked.columns else 0
    if n_t1 + n_t2 == 0:
        warnings.append(
            "No candidates cleared the Tier 1/2 thresholds (n_tier1 + n_tier2 == 0). "
            "Frame results as 'highest-ranked candidates', not 'candidates clearing the "
            "nomination bar' — an empty Tier 1/2 is a legitimate outcome of absolute cutoffs.")

    if warnings:
        print("\n" + "=" * 60)
        print("⚠ EXPORT CONSISTENCY GATE — WARNINGS:")
        for w in warnings:
            print(f"  ⚠ {w}")
        print("=" * 60 + "\n")
    else:
        print("  ✓ Export consistency gate: all invariants satisfied.")
    return warnings


# ---------------------------------------------------------------------------
# Report facts (item 7) — pre-derived numbers and sentences.
# ---------------------------------------------------------------------------

def _tiering_statement(ranked):
    n_t1 = int((ranked["tier"] == "Tier 1").sum())
    n_t2 = int((ranked["tier"] == "Tier 2").sum())
    n_t3 = int((ranked["tier"] == "Tier 3").sum())
    n_total = len(ranked)
    return (f"Of {n_total} scored candidates, {n_t1} are Tier 1 (≥0.55), "
            f"{n_t2} are Tier 2 (0.35–0.55), and {n_t3} are Tier 3 (<0.35)."), {
        "n_tier1": n_t1, "n_tier2": n_t2, "n_tier3": n_t3, "n_total": n_total}


def _validation_facts(metrics):
    """Validation recall facts. The HEADLINE recall is the PRE-REGISTERED set
    (locked before ranking). If any target was added to the harness after the
    ranking existed, an AUGMENTED recall is reported separately and labelled — the
    augmented figure is never the headline alone (see _assert_recall_reporting)."""
    augmented = bool(metrics.get("harness_augmented_after_ranking"))
    pre10 = metrics.get("recall_pre_registered_at_10_str", metrics.get("recall_at_10_str", "n/a"))
    pre20 = metrics.get("recall_pre_registered_at_20_str", metrics.get("recall_at_20_str", "n/a"))
    aug10 = metrics.get("recall_augmented_at_10_str")
    aug20 = metrics.get("recall_augmented_at_20_str")
    posthoc = metrics.get("core_posthoc_genes", []) or []
    locked = metrics.get("harness_locked_date")

    statement = (
        f"Recall@10 = {pre10}, recall@20 = {pre20} of {metrics.get('n_known_core_prereg_scored', 'n/a')} "
        f"pre-registered core validated targets (harness locked {locked}). "
        f"{metrics.get('holdout_caveat', '')}"
    )
    if augmented:
        statement += (
            f" Augmented harness (adds {len(posthoc)} target(s) promoted AFTER ranking: "
            f"{', '.join(posthoc)}): recall@10 = {aug10}, recall@20 = {aug20} — reported for "
            f"transparency, NOT as the headline (a benchmark that admits the discovery it "
            f"validates is circular)."
        )

    return {
        # headline (pre-registered)
        "recall_at_10_str": pre10,
        "recall_at_20_str": pre20,
        "recall_basis": metrics.get("recall_basis", "pre_registered"),
        "n_known_core_total": metrics.get("n_known_core_total"),
        "n_known_core_scored": metrics.get("n_known_core_scored"),
        "n_known_core_excluded_topology": metrics.get("n_known_core_excluded_topology"),
        "n_negative_controls_excluded_topology": metrics.get("n_negative_controls_excluded_topology"),
        # anti-circularity provenance + dual recall
        "harness_locked_date": locked,
        "harness_augmented_after_ranking": augmented,
        "n_known_core_prereg": metrics.get("n_known_core_prereg"),
        "n_known_core_prereg_scored": metrics.get("n_known_core_prereg_scored"),
        "n_known_core_posthoc": metrics.get("n_known_core_posthoc"),
        "core_posthoc_genes": posthoc,
        "recall_pre_registered_at_10_str": pre10,
        "recall_pre_registered_at_20_str": pre20,
        "recall_augmented_at_10_str": aug10,
        "recall_augmented_at_20_str": aug20,
        "holdout_n": metrics.get("holdout_n"),
        "holdout_resolution_pp": metrics.get("holdout_resolution_pp"),
        "holdout_caveat": metrics.get("holdout_caveat"),
        "validation_statement": statement,
    }


def _negative_control_facts(metrics):
    return {
        "verdict": metrics.get("negative_control_verdict"),
        "n_ranking_high": metrics.get("n_negative_controls_ranking_high"),
        "statement": metrics.get("negative_control_statement"),
    }


def _protein_evidence_facts(ranked, top_n=20):
    n_total = len(ranked)
    n_any = int(ranked["has_protein_evidence"].sum()) if "has_protein_evidence" in ranked.columns else 0
    n_ihc = int(ranked["has_ihc_protein_measurement"].sum()) if "has_ihc_protein_measurement" in ranked.columns else 0

    top = ranked.head(top_n)
    n_top_any = int(top["has_protein_evidence"].sum()) if "has_protein_evidence" in top.columns else 0
    n_top_ihc = int(top["has_ihc_protein_measurement"].sum()) if "has_ihc_protein_measurement" in top.columns else 0

    source_counts = {}
    if "protein_evidence_source" in ranked.columns:
        source_counts = ranked["protein_evidence_source"].value_counts().to_dict()
    top_source_counts = {}
    if "protein_evidence_source" in top.columns:
        top_source_counts = top["protein_evidence_source"].value_counts().to_dict()

    statement = (
        f"{n_ihc} of {n_total} scored candidates have HPA IHC protein measurement; "
        f"{n_any} have protein evidence (IHC or plasma-membrane localization). "
        f"Within the top {top_n}, {n_top_ihc} have IHC measurement and {n_top_any} have "
        f"any protein evidence."
    )
    return {
        "n_total": n_total,
        "n_with_ihc_measurement": n_ihc,
        "n_with_any_protein_evidence": n_any,
        "n_top_with_ihc_measurement": n_top_ihc,
        "n_top_with_any_protein_evidence": n_top_any,
        "source_counts_overall": {str(k): int(v) for k, v in source_counts.items()},
        "source_counts_top": {str(k): int(v) for k, v in top_source_counts.items()},
        "protein_evidence_statement": statement,
    }


def _safety_facts(ranked):
    n_total = len(ranked)
    n_computed = int((~ranked["safety_unassessed"]).sum()) if "safety_unassessed" in ranked.columns else 0
    n_neutral = int(ranked["safety_unassessed"].sum()) if "safety_unassessed" in ranked.columns else 0

    assessed = ranked[~ranked["safety_unassessed"]] if "safety_unassessed" in ranked.columns else ranked
    dist = {}
    if "safety_score" in assessed.columns and len(assessed) > 0:
        dist = {
            "n_safe_ge_0.6": int((assessed["safety_score"] >= 0.6).sum()),
            "n_moderate_0.4_0.6": int(((assessed["safety_score"] >= 0.4) & (assessed["safety_score"] < 0.6)).sum()),
            "n_unsafe_lt_0.4": int((assessed["safety_score"] < 0.4).sum()),
        }

    statement = (
        f"{n_computed} of {n_total} scored candidates carry a computed HPA safety score; "
        f"{n_neutral} ({round(100.0 * n_neutral / n_total, 1) if n_total else 0}%) fall back to "
        f"the neutral 0.7 default (safety_unassessed). "
        f"Among the assessed subset: {dist.get('n_safe_ge_0.6', 0)} safe (≥0.6), "
        f"{dist.get('n_moderate_0.4_0.6', 0)} moderate (0.4–0.6), "
        f"{dist.get('n_unsafe_lt_0.4', 0)} unsafe (<0.4)."
    )
    return {
        "n_computed": n_computed,
        "n_neutral_default": n_neutral,
        "assessed_subset_distribution": dist,
        "safety_statement": statement,
    }


def _coverage_facts(cov_df):
    """Build a coverage statement from the coverage_report DataFrame."""
    lines = []
    for _, r in cov_df.iterrows():
        col = r["column"]
        n_ann = r["n_annotated"]
        pct_ann = r["percent_annotated"]
        n_pos = r["n_positive"]
        if n_pos is not None:
            lines.append(f"{col}: {n_ann}/{r['n_total']} annotated ({pct_ann}%), "
                         f"{n_pos} positive ({r['percent_positive']}%)")
        else:
            lines.append(f"{col}: {n_ann}/{r['n_total']} annotated ({pct_ann}%)")
    statement = "Coverage: " + "; ".join(lines) + "."
    return {
        "columns": cov_df.to_dict("records"),
        "coverage_statement": statement,
    }


def _stability_facts(stability):
    return {
        "verdict": stability.get("stability_verdict_overall"),
        "statement": stability.get("stability_statement"),
        "n_comparisons": len(stability.get("comparisons", [])),
    }


def _topology_facts(ranked, top_n=20):
    """Confirmed vs unconfirmed plasma-membrane residency among scored candidates.

    Unconfirmed = a machine-learning surface prediction with NO independent
    confirmation (no CSPA/GPI experimental evidence and no Open Targets
    plasma-membrane call). Reporting this count prevents ML-only hits (e.g. an
    ER–PM contact-site protein) from being silently folded into the confirmed list.
    """
    n_total = len(ranked)
    if "surface_confirmation" not in ranked.columns:
        return {"n_total": n_total, "available": False,
                "topology_statement": "surface_confirmation was not computed for this run "
                                      "(seed run without SURFY/Open Targets confirmation)."}
    vc = ranked["surface_confirmation"].value_counts().to_dict()
    n_exp = int(vc.get("confirmed_experimental", 0))
    n_ot = int(vc.get("confirmed_ot", 0))
    n_unconf = int(vc.get("unconfirmed", 0))
    n_conf = n_exp + n_ot
    top = ranked.head(top_n)
    top_unconf = top.loc[top["surface_confirmation"] == "unconfirmed", "gene_symbol"].astype(str).tolist()
    statement = (
        f"{n_conf} of {n_total} scored candidates have independently confirmed "
        f"plasma-membrane residency ({n_exp} experimental CSPA/GPI, {n_ot} Open Targets "
        f"subcellular-location); {n_unconf} are unconfirmed machine-learning surface "
        f"predictions. Within the top {top_n}, {len(top_unconf)} are unconfirmed"
        + (f" ({', '.join(top_unconf)})" if top_unconf else "") + "."
    )
    return {
        "n_total": n_total,
        "available": True,
        "n_confirmed_total": n_conf,
        "n_confirmed_experimental": n_exp,
        "n_confirmed_ot": n_ot,
        "n_unconfirmed": n_unconf,
        "n_unconfirmed_top20": len(top_unconf),
        "unconfirmed_top20_genes": top_unconf,
        "topology_statement": statement,
    }


def _cohort_facts(output_dir):
    """Cell-cohort facts: DISCOVERED (catalogue) vs ANALYSED (post-subsampling
    matrices), plus a per-atlas x per-compartment breakdown. Returns None on the
    own-data path (no Census compartment matrix written)."""
    comp_path = os.path.join(output_dir, "compartment_expression.csv")
    if not os.path.exists(comp_path):
        return None
    comp = pd.read_csv(comp_path)
    # Analysed = one count per (dataset, compartment) summed over the four compartments
    # (n_cells is constant across genes within a dataset/compartment).
    per = comp.groupby(["dataset_id", "compartment"])["n_cells"].max().reset_index()
    matrices_analyzed = int(per["n_cells"].sum())
    per_atlas = (per.pivot_table(index="dataset_id", columns="compartment",
                                 values="n_cells", aggfunc="max", fill_value=0)
                 .reset_index())
    summary = {}
    summ_path = os.path.join(output_dir, "cohort_summary.json")
    if os.path.exists(summ_path):
        with open(summ_path, encoding="utf-8") as fh:
            summary = json.load(fh)
    cohort = dict(summary)
    cohort["matrices_analyzed_cells"] = matrices_analyzed
    cohort["per_atlas_compartment_counts"] = per_atlas.to_dict("records")
    if cohort.get("n_cells_analyzed") is None:
        cohort["n_cells_analyzed"] = matrices_analyzed
    if not cohort.get("cohort_statement"):
        cohort["cohort_statement"] = (
            f"{matrices_analyzed:,} cells analysed across the four compartments "
            f"(per-atlas breakdown in cohort_cell_counts.csv).")
    return cohort


def _assert_cohort_consistent(cohort, output_dir):
    """HARD gate (raises): the analysed cell count the report will headline MUST equal
    the post-subsampling matrices. Blocks headlining the discovery catalogue (~831k)
    when only ~a tenth of the cells were analysed."""
    comp_path = os.path.join(output_dir, "compartment_expression.csv")
    if not os.path.exists(comp_path):
        print("  (cohort gate skipped: no compartment_expression.csv — own-data path)")
        return
    if not cohort or cohort.get("n_cells_analyzed") is None:
        raise RuntimeError(
            "Cohort gate: compartment_expression.csv exists but no analysed cell count was "
            "derived. The report must headline the analysed cohort, not the discovery "
            "catalogue. Run census_pull.pull_compartment_expression (writes cohort_summary.json).")
    reported = int(cohort["n_cells_analyzed"])
    matrices = int(cohort["matrices_analyzed_cells"])
    if reported != matrices:
        raise RuntimeError(
            f"Cohort gate: headline analysed cell count ({reported:,}) != cells in the analysed "
            f"matrices ({matrices:,}). The headline must be the count the analysis actually used "
            f"(per-atlas subsampled compartments), not the discovery catalogue "
            f"({cohort.get('n_cells_discovered_full')}).")
    print(f"  ✓ Cohort gate: analysed cell count {reported:,} matches the analysed matrices "
          f"(discovered {cohort.get('n_cells_discovered_full')}).")


def _assert_recall_reporting_consistent(validation):
    """HARD gate (raises): if a target was added to the harness AFTER the ranking was
    computed, BOTH the pre-registered and the augmented recall must be reported.
    Reporting a single number when the harness was augmented hides the circularity."""
    if not validation.get("harness_augmented_after_ranking"):
        return
    pre = validation.get("recall_pre_registered_at_10_str")
    aug = validation.get("recall_augmented_at_10_str")
    if not pre or pre == "n/a" or not aug or aug == "n/a":
        raise RuntimeError(
            "Recall-reporting gate: the harness was augmented with target(s) added after ranking "
            f"({validation.get('core_posthoc_genes')}), but both recall figures are not available "
            f"(pre-registered={pre!r}, augmented={aug!r}). Report recall against the pre-registered "
            "set AND the augmented set; never the augmented figure alone.")
    print("  ✓ Recall-reporting gate: harness augmented — both pre-registered and augmented recall present.")


def build_report_facts(ranked, harness, metrics, cov_df, stability_summary, output_dir):
    """Emit results/report_facts.json — one dict of pre-derived numbers AND
    pre-formatted sentences, all read back out of artifacts the run produced.

    SKILL.md Step 8 tells the agent to paste these strings verbatim, never
    compose its own.
    """
    validation = _validation_facts(metrics)
    cohort = _cohort_facts(output_dir)
    topology = _topology_facts(ranked)

    # --- HARD gates (raise BEFORE any report facts are written) ---
    _assert_cohort_consistent(cohort, output_dir)          # analysed != discovered
    _assert_recall_reporting_consistent(validation)        # dual recall when augmented

    tiering_stmt, tier_counts = _tiering_statement(ranked)
    neg_controls = _negative_control_facts(metrics)
    protein = _protein_evidence_facts(ranked)
    safety = _safety_facts(ranked)
    coverage = _coverage_facts(cov_df)
    stability = _stability_facts(stability_summary)

    # Existing soft-warning consistency gate (mirrors warnings into report_facts).
    warnings = _assert_export_consistent(ranked, harness, metrics)

    facts = {
        "cohort": cohort,
        "tiering": {**tier_counts, "tiering_statement": tiering_stmt},
        "validation": validation,
        "topology": topology,
        "negative_controls": neg_controls,
        "protein_evidence": protein,
        "safety": safety,
        "coverage": coverage,
        "stability": stability,
        "warnings": warnings,
    }

    path = os.path.join(output_dir, "report_facts.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_json_safe(facts), fh, indent=2, allow_nan=False)
    print(f"  Saved report facts: {path}")
    return facts


def export_all(spec_df, surf_df, ann_df, ti_df, scores, known_targets_df,
               output_dir="results"):
    os.makedirs(output_dir, exist_ok=True)
    ranked, harness, metrics, stability = _ranked(scores)

    # 1. Primary ranked list (authoritative copy).
    ranked.to_csv(os.path.join(output_dir, "ranked_surface_targets.csv"), index=False)

    # 2. Per-target evidence cards.
    with open(os.path.join(output_dir, "target_evidence_cards.json"), "w", encoding="utf-8") as fh:
        json.dump(_json_safe(_evidence_cards(ranked)), fh, indent=2, allow_nan=False)

    # 3. Analysis objects pickle (downstream / custom use).
    objs = {
        "compartment_consensus": spec_df,
        "surfaceome_topology": surf_df,
        "annotations": ann_df,
        "therapeutic_index": ti_df,
        "ranked_targets": ranked,
        "validation_harness": harness,
        "validation_metrics": metrics,
        "known_targets": known_targets_df,
    }
    pkl = os.path.join(output_dir, "analysis_objects.pkl")
    with open(pkl, "wb") as fh:
        pickle.dump(objs, fh)
    print(f"  Saved analysis objects: {pkl}")
    print(f"  (Load with: import pickle; objs = pickle.load(open('{pkl}','rb')))")

    # 4. Coverage report (annotation coverage vs positive rate).
    cov_df = coverage_report(ranked, output_dir)

    # 5. Report facts (pre-derived numbers + sentences) + consistency gates.
    facts = build_report_facts(ranked, harness, metrics, cov_df, stability, output_dir)
    cohort = facts.get("cohort") or {}
    topology = facts.get("topology") or {}
    val = facts.get("validation") or {}

    # 6. Provenance manifest.
    manifest = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        # Cohort: report ANALYSED cells (post-subsampling), not the discovery catalogue.
        "n_cells_discovered": cohort.get("n_cells_discovered_full"),
        "n_cells_analyzed": cohort.get("n_cells_analyzed"),
        "n_datasets_analyzed": cohort.get("n_datasets_analyzed"),
        "subsample_cap_per_dataset": cohort.get("subsample_cap_per_dataset"),
        "cohort_statement": cohort.get("cohort_statement"),
        "n_candidates_scored": int(len(ranked)),
        # Topology confirmation (fix for the inert-gate defect).
        "n_surface_confirmed": topology.get("n_confirmed_total"),
        "n_surface_unconfirmed": topology.get("n_unconfirmed"),
        "n_tier1": int((ranked["tier"] == "Tier 1").sum()),
        "n_tier2": int((ranked["tier"] == "Tier 2").sum()),
        # Recall: pre-registered headline + augmentation provenance (anti-circularity).
        "validation_recall_at_10": val.get("recall_at_10_str"),
        "validation_recall_at_20": val.get("recall_at_20_str"),
        "validation_recall_basis": val.get("recall_basis"),
        "harness_locked_date": val.get("harness_locked_date"),
        "harness_augmented_after_ranking": val.get("harness_augmented_after_ranking"),
        "recall_augmented_at_10": val.get("recall_augmented_at_10_str"),
        "n_known_core_excluded_topology": metrics.get("n_known_core_excluded_topology"),
        "n_negative_controls_excluded_topology": metrics.get("n_negative_controls_excluded_topology"),
        "negative_control_verdict": metrics.get("negative_control_verdict"),
        "stability_verdict": stability.get("stability_verdict_overall") if stability else None,
        "essentiality_role": "annotation_only (NOT a selection gate)",
        "outputs": sorted(f for f in os.listdir(output_dir)
                          if f.endswith((".csv", ".json", ".png", ".svg", ".pkl"))),
    }
    with open(os.path.join(output_dir, "analysis_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(_json_safe(manifest), fh, indent=2, allow_nan=False)

    print("\n=== Export Complete ===")
    return objs


if __name__ == "__main__":
    print("export_results: call export_all(...). See assets/eval/static_test.py.")
