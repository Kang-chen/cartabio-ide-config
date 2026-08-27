"""
Score the full generated library, then apply a config-driven filtering cascade
and select the top-N designs.

Cascade (each stage is optional/threshold-configurable):
  unique & valid
    -> NOVELTY      : max ECFP4 Tanimoto to known actives < novelty_max (0.4)
    -> ACTIVE+DRUGLIKE : activity > activity_min AND QED > qed_min
    -> ALERT-CLEAN  : no PAINS/Brenk match
    -> RING-SANITY  : no strained/oversized/over-fused ring systems
    -> SYNTHESIZABLE: SA_Score <= sa_max (Tier-1 makeability proxy)
    -> TOP-N by combined score

A fallback relaxes the QED gate if too few molecules survive (the validated
behavior), so the pipeline always returns *something* to report.

`ring_sanity` rejects the strained bridged/fused polycyclic artifacts a graph GA
can produce: it fails if the largest ring is > 7 atoms, there are > 2 bridgehead
atoms, or > 1 spiro atom.
"""
from __future__ import annotations
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit import RDLogger
from rdkit.Chem import rdMolDescriptors

import scoring as S

RDLogger.DisableLog("rdApp.*")


def ring_sanity(mol, max_ring_size: int = 7, max_bridgeheads: int = 2,
                max_spiro: int = 1):
    """Return (ok: bool, reason: str). Guards against GA polycyclic artifacts."""
    ri = mol.GetRingInfo()
    rings = ri.AtomRings()
    if not rings:
        return True, "no_rings"
    max_ring = max(len(r) for r in rings)
    if max_ring > max_ring_size:
        return False, f"ring_size_{max_ring}"
    try:
        n_bridge = rdMolDescriptors.CalcNumBridgeheadAtoms(mol)
    except Exception:
        n_bridge = 0
    if n_bridge > max_bridgeheads:
        return False, f"bridgeheads_{n_bridge}"
    try:
        n_spiro = rdMolDescriptors.CalcNumSpiroAtoms(mol)
    except Exception:
        n_spiro = 0
    if n_spiro > max_spiro:
        return False, f"spiro_{n_spiro}"
    return True, "ok"


def lipinski_pass(row) -> bool:
    viol = 0
    if row["MW"] > 500:
        viol += 1
    if row["LogP"] > 5:
        viol += 1
    if row["HBD"] > 5:
        viol += 1
    if row["HBA"] > 10:
        viol += 1
    return viol <= 1


def veber_pass(row) -> bool:
    return (row["RotB"] <= 10) and (row["TPSA"] <= 140)


def score_library(all_scored: Dict[str, float], gen_first: Dict[str, int],
                  scoring_fn: S.ScoringFunction, known_smiles: List[str],
                  alert_catalogs=("PAINS", "BRENK")) -> pd.DataFrame:
    """Build the full property table for every unique generated molecule.

    all_scored : {canonical_smiles: combined_fitness} from the GA.
    Returns a DataFrame with combined score, activity, QED, physchem, SA_Score,
    alerts, ring-sanity, and novelty (nn_known_tanimoto + nearest_known)."""
    smis = [s for s in all_scored.keys() if Chem.MolFromSmiles(s) is not None]
    raw = scoring_fn.raw_properties(smis)  # activity (+ any components) in one batch

    # Ensure we have every property we want to report, even if not a component.
    mols = {s: Chem.MolFromSmiles(s) for s in smis}
    def col(name):
        if name in raw:
            return raw[name]
        calc = S.PROPERTY_CALCS[name]
        return [float(calc(mols[s])) for s in smis]

    cat = S.build_alert_catalog(alert_catalogs)
    pains_cat = S.build_alert_catalog(("PAINS",))
    brenk_cat = S.build_alert_catalog(("BRENK",))

    # Known-actives fingerprints for novelty.
    known_mols = [Chem.MolFromSmiles(s) for s in known_smiles]
    known_fps = [S.ecfp(m) for m in known_mols if m is not None]
    known_valid = [s for s, m in zip(known_smiles, known_mols) if m is not None]

    rows = []
    activity = col("activity")
    qed = col("QED")
    mw = col("MW"); logp = col("LogP"); tpsa = col("TPSA")
    hbd = col("HBD"); hba = col("HBA"); rotb = col("RotB"); sa = col("SA_Score")
    for i, s in enumerate(smis):
        m = mols[s]
        sim, j = S.nearest_tanimoto(m, known_fps)
        ok_ring, ring_reason = ring_sanity(m)
        row = {
            "smiles": s,
            "combined": float(all_scored[s]),
            "activity": float(activity[i]),
            "QED": float(qed[i]),
            "MW": float(mw[i]), "LogP": float(logp[i]), "TPSA": float(tpsa[i]),
            "HBD": int(hbd[i]), "HBA": int(hba[i]), "RotB": int(rotb[i]),
            "SA_Score": float(sa[i]),
            "PAINS": bool(pains_cat.HasMatch(m)),
            "Brenk": bool(brenk_cat.HasMatch(m)),
            "alert": bool(cat.HasMatch(m)),
            "nn_known_tanimoto": round(sim, 3),
            "nearest_known": (known_valid[j] if j >= 0 else ""),
            "gen_first_seen": int(gen_first.get(s, -1)),
            "ring_ok": bool(ok_ring), "ring_reason": ring_reason,
        }
        row["Lipinski_pass"] = lipinski_pass(row)
        row["Veber_pass"] = veber_pass(row)
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("combined", ascending=False).reset_index(drop=True)
    return df


def select_top(df: pd.DataFrame, cfg: Optional[dict] = None):
    """Apply the filtering cascade and return (top_df, cascade_counts, working_df).

    cfg keys (all optional): novelty_max=0.4, activity_min=0.5, qed_min=0.6,
    sa_max=4.5, drop_alerts=True, require_ring_sanity=True, top_n=10,
    qed_min_fallback=0.5."""
    cfg = cfg or {}
    novelty_max = cfg.get("novelty_max", 0.4)
    activity_min = cfg.get("activity_min", 0.5)
    qed_min = cfg.get("qed_min", 0.6)
    sa_max = cfg.get("sa_max", 4.5)
    drop_alerts = cfg.get("drop_alerts", True)
    require_ring = cfg.get("require_ring_sanity", True)
    top_n = cfg.get("top_n", 10)
    qed_min_fallback = cfg.get("qed_min_fallback", 0.5)

    counts = {"unique_valid": len(df)}
    w = df.copy()

    w = w[w["nn_known_tanimoto"] < novelty_max]
    counts["after_novelty"] = len(w)

    def apply_active(frame, qmin):
        return frame[(frame["activity"] > activity_min) & (frame["QED"] > qmin)]

    a = apply_active(w, qed_min)
    if len(a) < top_n:  # fallback: relax QED gate to still return top_n
        a = apply_active(w, qed_min_fallback)
        counts["qed_fallback_used"] = True
    else:
        counts["qed_fallback_used"] = False
    w = a
    counts["after_active_druglike"] = len(w)

    if drop_alerts:
        w = w[~w["PAINS"]]  # PAINS is the hard drop; Brenk kept as a flag
    counts["after_pains_clean"] = len(w)

    if require_ring:
        w = w[w["ring_ok"]]
    counts["after_ring_sane"] = len(w)

    w = w[w["SA_Score"] <= sa_max]
    counts["after_sa"] = len(w)

    top = w.sort_values("combined", ascending=False).head(top_n).reset_index(drop=True)
    counts["selected"] = len(top)
    return top, counts, w
