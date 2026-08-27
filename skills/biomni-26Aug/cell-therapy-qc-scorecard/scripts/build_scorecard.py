"""
============================================================================
BUILD SCORECARD  —  cell-therapy scRNA-seq QC release scorecard
============================================================================

Aggregate per-cell module flags to per-unit headline metrics, apply the
GREEN/AMBER/RED thresholds, and compute the overall (worst-active-module) call.

Writes:
  03_per_unit_qc_metrics.csv         headline metric per unit x module
  05_scorecard_calls.csv             GREEN/AMBER/RED per unit x module + overall
  06_thresholds_reference.csv        exact thresholds used (auditable)
  07_scorecard_summary_readable.csv  one human-readable row per unit

Functions
  - call_level(value, thr)                  -> "GREEN"/"AMBER"/"RED"
  - build_scorecard(units, per_cell, cfg, qc_summary=None) -> (metrics_df, calls_df)

Usage
  from build_scorecard import build_scorecard
  metrics_df, calls_df = build_scorecard(units, per_cell, cfg, qc_summary)
"""

import os
from typing import Dict, Optional

import numpy as np
import pandas as pd

CALL_ORDER = {"GREEN": 0, "AMBER": 1, "RED": 2}


def call_level(value: float, thr: Dict) -> str:
    """Map a value to GREEN/AMBER/RED given a threshold spec with direction."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "NA"
    g, r, d = thr["green"], thr["red"], thr["direction"]
    if d == "high_good":
        if value >= g:
            return "GREEN"
        if value < r:
            return "RED"
        return "AMBER"
    else:  # low_good
        if value < g:
            return "GREEN"
        if value > r:
            return "RED"
        return "AMBER"


def _pct(numer, denom):
    return float(100.0 * numer / denom) if denom else 0.0


def build_scorecard(units: Dict[str, "object"], per_cell: pd.DataFrame,
                    cfg: Dict, qc_summary: Optional[pd.DataFrame] = None):
    """Compute per-unit metrics and GREEN/AMBER/RED calls."""
    mods = cfg["modules"]
    thr = cfg["thresholds"]
    metric_rows, call_rows = [], []

    # index qc_summary by unit if provided (for retention/mito/species)
    qc = qc_summary.set_index("unit") if (qc_summary is not None and "unit" in qc_summary) else None

    for name, ad in units.items():
        n = ad.n_obs
        obs = ad.obs
        m = {"unit": name, "type": obs["unit_type"].iloc[0] if "unit_type" in obs else
             (cfg.get("unit_types", {}).get(name, "")), "n_cells_final": n}

        # ---- Module A: purity ----
        purity = _pct(obs["is_target"].sum(), n) if "is_target" in obs else np.nan
        clean = _pct(obs["is_clean_target"].sum(), n) if "is_clean_target" in obs else np.nan
        aberr = _pct(obs["is_aberrant_target"].sum(), n) if "is_aberrant_target" in obs else np.nan
        contam = _pct(obs["is_true_contaminant"].sum(), n) if "is_true_contaminant" in obs else np.nan
        m.update({"pct_target_purity": purity, "pct_clean_target": clean,
                  "pct_aberrant_target": aberr, "pct_true_contaminant": contam})

        # ---- Module B: residual pluripotency ----
        if mods.get("B") and "is_residual_pluripotent" in obs:
            resid = _pct(obs["is_residual_pluripotent"].sum(), n)
            m["pct_residual_pluripotent"] = resid
            m["n_triad_all3"] = int(ad.uns.get("n_triad_all3", 0))

        # ---- Module C: off-target ----
        if mods.get("C") and "is_offtarget_any" in obs:
            m["pct_offtarget"] = _pct(obs["is_offtarget_any"].sum(), n)

        # ---- Module D: maturity ----
        if mods.get("D") and "is_mature" in obs:
            denom = max(int(obs["is_target"].sum()), 1) if "is_target" in obs else n
            m["pct_mature_of_target"] = _pct(obs["is_mature"].sum(), denom)

        # ---- Module E: technical QC (unit-level from qc_summary) ----
        if qc is not None and name in qc.index:
            row = qc.loc[name]
            m["retention_pct"] = float(row.get("retention_pct", np.nan))
            m["median_pct_mito"] = float(row.get("median_pct_mito", np.nan))
            m["doublet_rate_pct"] = float(row.get("doublet_rate_pct", np.nan))
        # species contamination from species table if present
        m["species_contam_pct"] = float(getattr(ad, "uns", {}).get("species_contam_pct", np.nan)) \
            if hasattr(ad, "uns") else np.nan

        metric_rows.append(m)

        # ---------- CALLS ----------
        calls = {"unit": name}
        calls["A_identity_purity"] = call_level(purity, thr["purity_pct"])
        if mods.get("B") and "pct_residual_pluripotent" in m:
            calls["B_residual_pluripotency"] = call_level(m["pct_residual_pluripotent"], thr["resid_pluri_pct"])
        if mods.get("C") and "pct_offtarget" in m:
            calls["C_offtarget_lineage"] = call_level(m["pct_offtarget"], thr["offtarget_pct"])
        if mods.get("D") and "pct_mature_of_target" in m:
            calls["D_maturity"] = call_level(m["pct_mature_of_target"], thr["maturity_pct"])
        # Module E composite = worst of retention/species/mito
        e_calls = []
        if "retention_pct" in m and not np.isnan(m["retention_pct"]):
            e_calls.append(call_level(m["retention_pct"], thr["retention_pct"]))
        if not np.isnan(m.get("species_contam_pct", np.nan)):
            e_calls.append(call_level(m["species_contam_pct"], thr["species_contam_pct"]))
        if "median_pct_mito" in m and not np.isnan(m["median_pct_mito"]):
            e_calls.append(call_level(m["median_pct_mito"], thr["mito_pct"]))
        if e_calls:
            calls["E_technical_qc"] = max(e_calls, key=lambda c: CALL_ORDER.get(c, -1))

        # overall = worst ACTIVE module
        active = [v for k, v in calls.items() if k != "unit" and v in CALL_ORDER]
        calls["OVERALL"] = max(active, key=lambda c: CALL_ORDER[c]) if active else "NA"
        call_rows.append(calls)
        print(f"  ✓ {name}: purity {purity:.1f}%  ->  OVERALL {calls['OVERALL']}")

    metrics_df = pd.DataFrame(metric_rows)
    calls_df = pd.DataFrame(call_rows)

    # thresholds reference
    thr_rows = []
    label = {"purity_pct": "A. Target purity (%)", "resid_pluri_pct": "B. Residual pluripotency (%)",
             "offtarget_pct": "C. Off-target lineage (%)", "maturity_pct": "D. Maturity (%)",
             "retention_pct": "E. Cell retention (%)", "species_contam_pct": "E. Cross-species contam (%)",
             "mito_pct": "E. Median mito (%)", "contam_pct": "Contamination (%)"}
    for k, v in thr.items():
        thr_rows.append({"module_metric": label.get(k, k), "green": v["green"],
                         "amber_between": f"{min(v['green'],v['red'])}-{max(v['green'],v['red'])}",
                         "red": v["red"], "direction": v["direction"]})
    thr_df = pd.DataFrame(thr_rows)

    d = cfg["dirs"]["tables"]
    metrics_df.to_csv(os.path.join(d, "03_per_unit_qc_metrics.csv"), index=False)
    calls_df.to_csv(os.path.join(d, "05_scorecard_calls.csv"), index=False)
    thr_df.to_csv(os.path.join(d, "06_thresholds_reference.csv"), index=False)

    # readable summary = metrics + overall call merged
    readable = metrics_df.merge(calls_df[["unit", "OVERALL"]], on="unit", how="left")
    readable = readable.rename(columns={"OVERALL": "overall_call"})
    readable.to_csv(os.path.join(d, "07_scorecard_summary_readable.csv"), index=False)

    print(f"✓ scorecard written to {d} (tables 03, 05, 06, 07)")
    return metrics_df, calls_df


if __name__ == "__main__":
    # tiny unit test of the calling logic (no data)
    thr = {"green": 90.0, "red": 75.0, "direction": "high_good"}
    assert call_level(99.5, thr) == "GREEN"
    assert call_level(80.0, thr) == "AMBER"
    assert call_level(60.0, thr) == "RED"
    thr2 = {"green": 2.0, "red": 10.0, "direction": "low_good"}
    assert call_level(1.0, thr2) == "GREEN"
    assert call_level(5.0, thr2) == "AMBER"
    assert call_level(20.0, thr2) == "RED"
    print("✓ build_scorecard call-logic smoke test passed")
