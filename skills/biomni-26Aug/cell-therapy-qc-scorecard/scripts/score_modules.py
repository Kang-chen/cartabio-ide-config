"""
============================================================================
SCORE MODULES  —  cell-therapy scRNA-seq QC release scorecard
============================================================================

Compute per-cell flags for every ACTIVE release module, per unit, using the
marker panels from derive_marker_panels.py. This is the scientific core of the
skill; the logic encodes hard-won corrections (see SKILL.md "Scientific
Caveats" and references/qc_release_methodology.md). READ THOSE before editing.

Modules
  A  Target-cell identity & purity  (always)  — EXPRESSION-ANCHORED, no score gate
  B  Residual pluripotency          (iPSC/ESC) — co-expression specificity + null
  C  Off-target lineage             (always)  — restricted to anchor-NEGATIVE cells
  D  Target-cell maturity           (if axis) — mature vs immature signature index
  E  Technical QC                   (always)  — retention/species/mito (unit-level)

Key helpers
  - B(ad, col)          : dtype-safe boolean mask (int 0/1 obs -> bool)
  - expr_matrix(ad, gs) : dense lognorm sub-matrix for a gene list
  - score_modules(units, panels, cfg) -> writes 04_per_cell_module_scores.csv,
                                          returns per-cell dict + augments units

Usage
  from score_modules import score_modules
  units, per_cell = score_modules(units, panels, cfg)
"""

import os
from typing import Dict, List

import numpy as np
import pandas as pd
import scanpy as sc


# ---------------------------------------------------------------------------
# dtype-safe helpers  (CRITICAL — see caveat #5)
# ---------------------------------------------------------------------------
def B(ad, col):
    """Return a boolean numpy mask from an obs column stored as int 0/1 or bool.
    Integer obs + Python '~' bitwise-NOT corrupts masks; always coerce to bool."""
    return ad.obs[col].values.astype(bool)


def present(genes: List[str], ad) -> List[str]:
    """Subset a gene list to those present in the AnnData var_names."""
    s = set(ad.var_names)
    return [g for g in genes if g in s]


def _get_lognorm(ad):
    """Return the log-normalized layer (prefer explicit layer, else .X)."""
    if "lognorm" in ad.layers:
        return ad.layers["lognorm"]
    return ad.X


def expr_matrix(ad, genes: List[str]) -> np.ndarray:
    """Dense (n_cells × n_genes) log-normalized matrix for `genes` (present only)."""
    g = present(genes, ad)
    if not g:
        return np.zeros((ad.n_obs, 0))
    X = _get_lognorm(ad[:, g])
    return np.asarray(X.todense()) if hasattr(X, "todense") else np.asarray(X)


def _score_genes(ad, genes: List[str], name: str) -> np.ndarray:
    """Wrap sc.tl.score_genes; returns the score array (NaN-safe)."""
    g = present(genes, ad)
    if not g:
        ad.obs[name] = 0.0
        return ad.obs[name].values
    sc.tl.score_genes(ad, g, score_name=name, use_raw=False)
    return ad.obs[name].values


def _null_pluri_threshold(ad, genes: List[str], n_perm: int = 50,
                          pct: float = 99.9, seed: int = 0) -> float:
    """Per-unit null pluripotency-score threshold via gene-label shuffling.

    Shuffle which genes form the 'pluripotency' set and recompute the score;
    the high percentile of the shuffled score is the noise floor. A real call
    must exceed max(this, 0)."""
    g = present(genes, ad)
    if not g:
        return 0.0
    rng = np.random.default_rng(seed)
    all_genes = np.array(ad.var_names)
    null_max = []
    for _ in range(n_perm):
        rnd = rng.choice(all_genes, size=len(g), replace=False).tolist()
        sc.tl.score_genes(ad, rnd, score_name="_null_tmp", use_raw=False)
        null_max.append(ad.obs["_null_tmp"].values)
    if "_null_tmp" in ad.obs:
        del ad.obs["_null_tmp"]
    return float(np.percentile(np.concatenate(null_max), pct))


# ---------------------------------------------------------------------------
# Module A — target-cell identity & purity (EXPRESSION-ANCHORED)
# ---------------------------------------------------------------------------
def module_A_identity(ad, panels, min_anchor: int = 1, aberrant_min: int = 2):
    """Anchor identity on RAW expression of target markers (>=min_anchor detected).
    Do NOT gate on a positive signature score (background correction drives it
    negative in target-dominated products). Classify fidelity by counting
    aberrant off-lineage programs among anchor-positive cells."""
    anchor_genes = panels["identity_anchor"]
    E = expr_matrix(ad, anchor_genes)
    anchor_pos = (E > 0).sum(1) >= min_anchor if E.shape[1] else np.zeros(ad.n_obs, bool)
    ad.obs["target_anchor_pos"] = anchor_pos.astype(int)

    # count aberrant lineage programs among anchor+ cells (fidelity)
    n_aberrant = np.zeros(ad.n_obs, int)
    for lin, gl in panels["fidelity_lineages"].items():
        Ei = expr_matrix(ad, gl)
        lin_pos = (Ei > 0).sum(1) >= aberrant_min if Ei.shape[1] else np.zeros(ad.n_obs, bool)
        ad.obs[f"aberrant_{lin}"] = lin_pos.astype(int)
        n_aberrant += lin_pos.astype(int)
    ad.obs["n_aberrant_lineages"] = n_aberrant

    clean = anchor_pos & (n_aberrant == 0)
    aberrant = anchor_pos & (n_aberrant >= 1)
    is_target = anchor_pos
    # a TRUE contaminant is anchor-NEGATIVE and expresses a non-target lineage
    contaminant = (~anchor_pos) & (n_aberrant >= 1)
    unassigned = (~anchor_pos) & (n_aberrant == 0)

    ad.obs["is_target"] = is_target.astype(int)
    ad.obs["is_clean_target"] = clean.astype(int)
    ad.obs["is_aberrant_target"] = aberrant.astype(int)
    ad.obs["is_true_contaminant"] = contaminant.astype(int)
    ad.obs["is_unassigned"] = unassigned.astype(int)
    return ad


# ---------------------------------------------------------------------------
# Module B — residual pluripotency (iPSC/ESC only)  co-expression + null
# ---------------------------------------------------------------------------
def module_B_pluripotency(ad, panels, k_core: int = 2):
    """Call a cell residual-pluripotent only if it co-expresses >=k_core core TFs
    AND >=1 SPECIFIC TF AND exceeds a per-unit shuffled-null score AND is NOT
    target-identity-positive. Also tally the canonical triad co-expression."""
    core = panels["pluripotency"]["core"]
    specific = panels["pluripotency"]["specific"]
    triad = panels["pluripotency"]["triad"]

    Ecore = expr_matrix(ad, core)
    n_core = (Ecore > 0).sum(1) if Ecore.shape[1] else np.zeros(ad.n_obs, int)
    Espec = expr_matrix(ad, specific)
    has_specific = (Espec > 0).sum(1) >= 1 if Espec.shape[1] else np.zeros(ad.n_obs, bool)

    score = _score_genes(ad, core, "score_pluripotency")
    null_thr = _null_pluri_threshold(ad, core)
    ad.uns["pluri_null_threshold"] = null_thr

    is_target = B(ad, "is_target") if "is_target" in ad.obs else np.zeros(ad.n_obs, bool)
    call = (n_core >= k_core) & has_specific & (score > max(null_thr, 0.0)) & (~is_target)
    ad.obs["is_residual_pluripotent"] = call.astype(int)

    # triad co-expression (strongest evidence) — audit only
    Etri = expr_matrix(ad, triad)
    if Etri.shape[1] >= 2:
        n_triad = (Etri > 0).sum(1)
        ad.obs["n_triad_pos"] = n_triad
        ad.uns["n_triad_all3"] = int((n_triad >= 3).sum())
        ad.uns["n_triad_ge2"] = int((n_triad >= 2).sum())
    else:
        ad.uns["n_triad_all3"] = 0
        ad.uns["n_triad_ge2"] = 0
    return ad


# ---------------------------------------------------------------------------
# Module C — off-target lineage (restricted to anchor-NEGATIVE cells)
# ---------------------------------------------------------------------------
def module_C_offtarget(ad, panels, min_markers: int = 2):
    """Count a cell as off-target only if it co-expresses >=min_markers of a
    non-target lineage AND is target-anchor-NEGATIVE AND has a positive lineage
    signature score (guards against sporadic single-gene noise)."""
    anchor_neg = ~B(ad, "target_anchor_pos") if "target_anchor_pos" in ad.obs \
        else np.ones(ad.n_obs, bool)
    off_any = np.zeros(ad.n_obs, bool)
    detail = {}
    for lin, gl in panels["offtarget"].items():
        E = expr_matrix(ad, gl)
        score = _score_genes(ad, gl, f"score_off_{lin}")
        call = ((E > 0).sum(1) >= min_markers) & anchor_neg & (score > 0) \
            if E.shape[1] else np.zeros(ad.n_obs, bool)
        ad.obs[f"is_offtarget_{lin}"] = call.astype(int)
        off_any |= call
        detail[lin] = int(call.sum())
    ad.obs["is_offtarget_any"] = off_any.astype(int)
    ad.uns["offtarget_detail"] = detail
    return ad


# ---------------------------------------------------------------------------
# Module D — target-cell maturity (mature vs immature signature index)
# ---------------------------------------------------------------------------
def module_D_maturity(ad, panels, prolif_thr: float = 0.1):
    """Maturity index = score(mature markers) - score(immature markers), computed
    on target cells. is_mature = index > 0. Also flag proliferating cells."""
    mature = panels["maturity"]["mature"]
    immature = panels["maturity"]["immature"]
    s_mat = _score_genes(ad, mature, "score_mature")
    s_imm = _score_genes(ad, immature, "score_immature")
    idx = s_mat - s_imm
    ad.obs["maturity_index"] = idx
    is_target = B(ad, "is_target") if "is_target" in ad.obs else np.ones(ad.n_obs, bool)
    ad.obs["is_mature"] = ((idx > 0) & is_target).astype(int)

    s_prolif = _score_genes(ad, panels["proliferation"], "score_proliferation")
    ad.obs["is_proliferating"] = (s_prolif > prolif_thr).astype(int)
    return ad


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def score_modules(units: Dict[str, "sc.AnnData"], panels: Dict, cfg: Dict):
    """Run all active modules per unit; write per-cell table; return per-cell dict."""
    mods = cfg["modules"]
    per_cell_frames = []
    for name, ad in units.items():
        # ensure a lognorm layer exists (normalize step should have set .X to lognorm)
        if "lognorm" not in ad.layers:
            ad.layers["lognorm"] = ad.X.copy()

        module_A_identity(ad, panels)                       # always
        if mods.get("B"):
            module_B_pluripotency(ad, panels)
        if mods.get("C"):
            module_C_offtarget(ad, panels)
        if mods.get("D") and panels["maturity"]["has_axis"]:
            module_D_maturity(ad, panels)

        # engineering / transgene detection (reporting only)
        eng = present(panels.get("engineering_features", []), ad)
        if eng:
            E = expr_matrix(ad, eng)
            ad.obs["transgene_pos"] = ((E > 0).sum(1) >= 1).astype(int)

        # collect the per-cell flags into a tidy frame
        keep_cols = [c for c in ad.obs.columns if c.startswith(
            ("is_", "n_aberrant", "target_anchor", "maturity_index",
             "score_pluripotency", "transgene_pos"))]
        f = ad.obs[keep_cols].copy()
        f.insert(0, "unit", name)
        f.insert(1, "cell", ad.obs_names)
        per_cell_frames.append(f)
        print(f"  ✓ scored '{name}': "
              f"target+={int(ad.obs['is_target'].sum())}/{ad.n_obs}, "
              + (f"resid_pluri={int(ad.obs['is_residual_pluripotent'].sum())}, " if mods.get('B') else "")
              + (f"offtarget={int(ad.obs['is_offtarget_any'].sum())}, " if mods.get('C') else "")
              + (f"mature={int(ad.obs['is_mature'].sum())}" if (mods.get('D') and panels['maturity']['has_axis']) else ""))

    per_cell = pd.concat(per_cell_frames, ignore_index=True)
    out = os.path.join(cfg["dirs"]["tables"], "04_per_cell_module_scores.csv")
    per_cell.to_csv(out, index=False)
    print(f"✓ per-cell module scores ({len(per_cell)} cells) -> {out}")
    return units, per_cell


if __name__ == "__main__":
    print("score_modules.py — import and call score_modules(units, panels, cfg).")
