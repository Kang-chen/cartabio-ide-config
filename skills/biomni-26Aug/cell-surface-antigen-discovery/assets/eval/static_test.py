#!/usr/bin/env python3
"""Offline static tests for the cell-surface-antigen-discovery skill.

Exercises the three defensive gates added in the fix round, plus the long-standing
compartment-assignment regression. No network, no Census, no Open Targets — every
test builds a small in-memory fixture.

  Fix 1  cohort cell-count gate (analysed != discovery catalogue)   [export_results]
  Fix 2  per-gene SURFY topology mapping + inert-gate check          [surfaceome_filter]
  Fix 3  dual-recall (pre-registered vs augmented) gate             [score_targets/export_results]

Run:  python assets/eval/static_test.py     (exit code 0 = all pass)
"""

import os
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", "..", "scripts"))
sys.path.insert(0, _SCRIPTS)

import surfaceome_filter as sfm          # noqa: E402
import score_targets as st               # noqa: E402
import export_results as ex              # noqa: E402
from census_pull import assign_compartment  # noqa: E402

_results = []


def check(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        _results.append(True)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {name}: {exc}")
        traceback.print_exc()
        _results.append(False)


def expect_raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return
    raise AssertionError("expected an exception but none was raised")


# ---------------------------------------------------------------------------
# Fix 2 — SURFY per-gene topology mapping (no blanket plasma_membrane)
# ---------------------------------------------------------------------------

def t_parse_noncyt_len():
    # ESYT3 topology: only a 5-residue non-cytoplasmic loop.
    assert sfm._parse_noncyt_len("CY:1-27;TM:28-46;NC:47-51;TM:52-72;CY:73-886") == 5
    assert sfm._parse_noncyt_len("SP:1-24;NC:25-305;TM:306-330;CY:331-362") == 281
    assert np.isnan(sfm._parse_noncyt_len(""))


def t_accessibility():
    assert sfm._surfy_accessibility(2, 5, "pos. trainingset", "Unclassified") == "none"   # ESYT3-like
    assert sfm._surfy_accessibility(1, 244, "machine learning", "Receptors") == "high"     # single-pass big ecto
    assert sfm._surfy_accessibility(7, 300, "machine learning", "Receptors") == "low"      # GPCR
    assert sfm._surfy_accessibility(0, 0, "GPI (UniProt)", "") == "high"                   # GPI-anchored
    assert sfm._surfy_accessibility(1, 30, "machine learning", "") == "low"                # small single-pass ecto


def t_localization():
    assert sfm._surfy_localization("surface", "Endoplasmic reticulum membrane") == "intracellular_er"
    # genuine PM protein that also transits the ER stays plasma_membrane (e.g. EGFR/MSLN)
    assert sfm._surfy_localization("surface", "Cell membrane;Endoplasmic reticulum membrane") == "plasma_membrane"
    assert sfm._surfy_localization("surface", "Cell membrane") == "plasma_membrane"
    assert sfm._surfy_localization("nonsurface", "Secreted") == "secreted_ecm"


def _mk_surf(n, accessibility="high", localization="plasma_membrane"):
    return pd.DataFrame({
        "gene_symbol": [f"G{i}" for i in range(n)],
        "surfaceome_class": "in-silico",
        "topology": "1TM",
        "ectodomain_accessibility": accessibility,
        "localization": localization,
        "notes": "",
    })


def t_gate_inert_raises():
    # genome-scale set, everything accessible PM -> the gate removes nothing -> raise
    surf = _mk_surf(250)
    expect_raises(lambda: sfm.apply_topology_filter(None, surf), RuntimeError)


def t_gate_fires_ok():
    surf = _mk_surf(250)
    surf.loc[0:9, "localization"] = "intracellular_er"      # 10 non-PM -> gated
    surf.loc[10:12, "ectodomain_accessibility"] = "none"    # 3 no-ectodomain -> gated
    out = sfm.apply_topology_filter(None, surf)
    assert len(out) == 237, len(out)


def t_gate_small_set_no_raise():
    # below the genome-scale threshold: excluding nothing is allowed (curated seed case)
    surf = _mk_surf(50)
    out = sfm.apply_topology_filter(None, surf)
    assert len(out) == 50


def t_surfy_loader_synthetic():
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("    (sub-skip: openpyxl not installed; helper-level SURFY tests still cover parsing)")
        return
    cols = ["UniProt gene", "Surfaceome Label", "Surfaceome Label Source", "TM domains",
            "topology", "Membranome Almen main-class", "UniProt subcellular", "Ensembl gene"]

    def r(gene, label, src, tm, topo, alm, sub):
        return dict(zip(cols, [gene, label, src, tm, topo, alm, sub, "ENSG0"]))

    df = pd.DataFrame([
        r("SANE", "surface", "machine learning", 1,
          "SP:1-24;NC:25-305;TM:306-330;CY:331-362", "Receptors", "Cell membrane"),
        r("ESYTX", "surface", "pos. trainingset", 2,
          "CY:1-27;TM:28-46;NC:47-51;TM:52-72;CY:73-886", "Unclassified",
          "Cell membrane;Endoplasmic reticulum membrane"),
        r("ORGO", "surface", "machine learning", 1,
          "SP:1-24;NC:25-305;TM:306-330;CY:331-362", "Transporters", "Endoplasmic reticulum membrane"),
        r("GPIX", "surface", "GPI (UniProt)", 0, "SP:1-20;NC:21-400", "Miscellaneous", "Cell membrane"),
    ])
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s3.xlsx")
        # title row 0, header row 1 (matches Table S3 layout read with header=1)
        df.to_excel(path, sheet_name=sfm.SURFY_MASTER_SHEET, index=False, startrow=1)
        loaded = sfm.load_surfy_surfaceome(source=path, cache_dir=d)
    conf = dict(zip(loaded["gene_symbol"], loaded["surface_confirmation_surfy"]))
    acc = dict(zip(loaded["gene_symbol"], loaded["ectodomain_accessibility"]))
    loc = dict(zip(loaded["gene_symbol"], loaded["localization"]))
    assert conf["ESYTX"] == "confirmed_experimental" and conf["SANE"] == "predicted"
    assert acc["ESYTX"] == "none"            # contact-site, no epitope
    assert acc["GPIX"] == "high"             # GPI-anchored
    assert loc["ORGO"] == "intracellular_er" # organelle-only -> gated
    out = sfm.apply_topology_filter(None, loaded, genome_scale=True)
    kept = set(out["gene_symbol"])
    assert kept == {"SANE", "GPIX"}, kept     # ESYTX (none) + ORGO (organelle) excluded


# ---------------------------------------------------------------------------
# Fix 3 — dual-recall (pre-registered vs augmented) reporting gate
# ---------------------------------------------------------------------------

def t_recall_gate_augmented_missing_raises():
    val = {"harness_augmented_after_ranking": True,
           "recall_pre_registered_at_10_str": "0/9",
           "recall_augmented_at_10_str": None,
           "core_posthoc_genes": ["ITGB6"]}
    expect_raises(lambda: ex._assert_recall_reporting_consistent(val), RuntimeError)


def t_recall_gate_both_present_ok():
    val = {"harness_augmented_after_ranking": True,
           "recall_pre_registered_at_10_str": "0/9",
           "recall_augmented_at_10_str": "1/10",
           "core_posthoc_genes": ["ITGB6"]}
    ex._assert_recall_reporting_consistent(val)  # no raise


def t_recall_gate_not_augmented_ok():
    ex._assert_recall_reporting_consistent({"harness_augmented_after_ranking": False})


def t_run_harness_dual_recall():
    ranked = pd.DataFrame({
        "gene_symbol": [f"X{i}" for i in range(30)],
        "rank": list(range(1, 31)),
        "tier": ["Tier 3"] * 30,
        "final_score": [1.0 - i * 0.01 for i in range(30)],
    })
    ranked.loc[14, "gene_symbol"] = "PRECORE"   # pre-registered core at rank 15
    ranked.loc[4, "gene_symbol"] = "POSTCORE"   # post-hoc core at rank 5
    known = pd.DataFrame({
        "gene_symbol": ["PRECORE", "POSTCORE", "NEGC"],
        "recall_core": [1, 1, 0],
        "clinical_status": ["clinical", "clinical", "not_a_target"],
        "provenance": ["pre_registered", "added_post_ranking", "pre_registered"],
        "date_added": ["2026-08-06", "2026-09-01", "2026-08-06"],
    })
    with tempfile.TemporaryDirectory() as d:
        _harness, metrics = st._run_harness(ranked, known, d, spec_genes=set(ranked["gene_symbol"]))
    assert metrics["harness_augmented_after_ranking"] is True
    assert metrics["recall_pre_registered_at_10_str"] == "0/1", metrics["recall_pre_registered_at_10_str"]
    assert metrics["recall_pre_registered_at_20_str"] == "1/1", metrics["recall_pre_registered_at_20_str"]
    assert metrics["recall_augmented_at_10_str"] == "1/2", metrics["recall_augmented_at_10_str"]
    # headline recall == pre-registered (never the augmented figure alone)
    assert metrics["recall_at_10_str"] == metrics["recall_pre_registered_at_10_str"]
    assert metrics["core_posthoc_genes"] == ["POSTCORE"]


# ---------------------------------------------------------------------------
# Fix 1 — cohort cell-count gate (analysed matrices, not the discovery catalogue)
# ---------------------------------------------------------------------------

def _write_comp(d, per):
    rows = []
    for ds, comp, n in per:
        for g in ("A", "B"):  # n_cells is constant across genes within a (ds, compartment)
            rows.append({"gene_symbol": g, "compartment": comp, "n_cells": n,
                         "n_expressing": 1, "pct_expressing": 1.0,
                         "mean_expression": 0.1, "dataset_id": ds})
    pd.DataFrame(rows).to_csv(os.path.join(d, "compartment_expression.csv"), index=False)


def t_cohort_gate_ok():
    import json
    with tempfile.TemporaryDirectory() as d:
        _write_comp(d, [("ds1", "epithelial", 130), ("ds1", "immune", 500),
                        ("ds2", "epithelial", 2000), ("ds2", "caf", 300)])
        analyzed = 130 + 500 + 2000 + 300
        json.dump({"n_cells_discovered_full": 831387, "n_cells_analyzed": analyzed,
                   "cohort_statement": "ok"},
                  open(os.path.join(d, "cohort_summary.json"), "w"))
        cohort = ex._cohort_facts(d)
        assert cohort["matrices_analyzed_cells"] == analyzed
        ex._assert_cohort_consistent(cohort, d)  # no raise
        # per-atlas breakdown exposes the atlas contributing only 130 epithelial cells
        rec = {r["dataset_id"]: r for r in cohort["per_atlas_compartment_counts"]}
        assert rec["ds1"]["epithelial"] == 130


def t_cohort_gate_mismatch_raises():
    import json
    with tempfile.TemporaryDirectory() as d:
        _write_comp(d, [("ds1", "epithelial", 130), ("ds1", "immune", 500)])
        # headline set to the discovery catalogue (the defect) -> must raise
        json.dump({"n_cells_discovered_full": 831387, "n_cells_analyzed": 831387,
                   "cohort_statement": "bad"},
                  open(os.path.join(d, "cohort_summary.json"), "w"))
        cohort = ex._cohort_facts(d)
        expect_raises(lambda: ex._assert_cohort_consistent(cohort, d), RuntimeError)


# ---------------------------------------------------------------------------
# Long-standing regression: compartment assignment (word-boundary matching)
# ---------------------------------------------------------------------------

def t_compartment_regression():
    assert assign_compartment("malignant cell") == "epithelial"
    assert assign_compartment("neoplastic cell") == "epithelial"
    assert assign_compartment("CD8-positive, alpha-beta T cell") == "immune"
    assert assign_compartment("fibroblast of lung") == "caf"
    assert assign_compartment("endothelial cell") == "endothelial"


def main():
    print("== cell-surface-antigen-discovery static tests ==")
    print("-- Fix 2: SURFY per-gene topology + inert-gate --")
    check("parse_noncyt_len", t_parse_noncyt_len)
    check("surfy_accessibility", t_accessibility)
    check("surfy_localization", t_localization)
    check("topology_gate_inert_raises", t_gate_inert_raises)
    check("topology_gate_fires_ok", t_gate_fires_ok)
    check("topology_gate_small_set_no_raise", t_gate_small_set_no_raise)
    check("surfy_loader_synthetic_xlsx", t_surfy_loader_synthetic)
    print("-- Fix 3: dual-recall gate --")
    check("recall_gate_augmented_missing_raises", t_recall_gate_augmented_missing_raises)
    check("recall_gate_both_present_ok", t_recall_gate_both_present_ok)
    check("recall_gate_not_augmented_ok", t_recall_gate_not_augmented_ok)
    check("run_harness_dual_recall", t_run_harness_dual_recall)
    print("-- Fix 1: cohort cell-count gate --")
    check("cohort_gate_ok", t_cohort_gate_ok)
    check("cohort_gate_mismatch_raises", t_cohort_gate_mismatch_raises)
    print("-- regression --")
    check("compartment_assignment", t_compartment_regression)

    n_pass = sum(_results)
    n_total = len(_results)
    print(f"\n{n_pass}/{n_total} tests passed.")
    sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
