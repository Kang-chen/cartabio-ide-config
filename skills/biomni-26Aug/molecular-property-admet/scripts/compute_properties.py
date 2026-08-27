"""
Standardize molecules and compute physicochemical, drug-likeness, and ADMET properties.

Input SMILES are first desalted, neutralized, canonicalized, and de-duplicated so
that downstream descriptors are computed on the actual drug-like parent (not a salt
or counterion). Uses RDKit for molecular descriptors, Lipinski/Veber rules, QED, and
structural alerts, and ADMET-AI for ADMET endpoint predictions.

ADMET-AI reports each endpoint's percentile against a reference set of approved drugs.
Its bundled reference (DrugBank) is licensed CC BY-NC (no commercial use), so this
skill instead uses a **commercially-permissive** reference regenerated from ChEMBL
approved drugs (CC BY-SA) — see ``scripts/build_reference_set.py`` and
``assets/chembl_approved_reference.csv``. The reference is passed to ADMET-AI via
``ADMETModel(drugbank_path=<chembl reference>)`` and the resulting percentile columns
are renamed from ``*_drugbank_approved_percentile`` to ``*_chembl_approved_percentile``
so the column names honestly reflect the source.
"""

import pandas as pd
import numpy as np

from rdkit import Chem
from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors, QED
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams
from rdkit.Chem.MolStandardize import rdMolStandardize

# Import the sibling endpoint registry robustly, regardless of how this module was
# loaded or what the working directory is (Biomni runs scripts via exec() and does not
# guarantee the skill dir is on sys.path). Adding this file's own directory makes the
# bare import resolve in every case.
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
try:
    from admet_endpoints import resolve_endpoints, SAFETY_KEYS
except ImportError:  # last resort: imported as part of the 'scripts' package
    from scripts.admet_endpoints import resolve_endpoints, SAFETY_KEYS


# =============================================================================
# Input standardization (desalt / neutralize / canonicalize / de-duplicate)
# =============================================================================

# Advisory sanity bounds for "small-molecule-like" parents (flagged, not dropped).
_MIN_MW = 100.0
_MAX_MW = 1500.0

# --- Commercially-permissive percentile reference (replaces DrugBank CC BY-NC) ------
# ADMET-AI computes percentiles against a reference set of approved drugs. We ship a
# ChEMBL-derived reference (CC BY-SA) instead of the bundled DrugBank reference.
from pathlib import Path as _Path
_SKILL_DIR = _os.path.dirname(_HERE)
REFERENCE_CSV = _Path(_SKILL_DIR) / "assets" / "chembl_approved_reference.csv"
# ADMET-AI hardcodes the suffix "drugbank_approved_percentile" regardless of the
# reference file passed; we rename to this honest suffix reflecting the true source.
PERCENTILE_SUFFIX = "chembl_approved_percentile"
_ADMET_AI_SUFFIX = "drugbank_approved_percentile"


def _standardize_one(raw):
    """
    Standardize a single SMILES string to its drug-like parent.

    Order matters (validated RDKit recipe): Cleanup (normalize/sanitize/disconnect
    metals) -> FragmentParent (desalt) -> Uncharger (neutralize the parent) ->
    canonical isomeric SMILES.

    Returns (parent_smiles_or_None, note).
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, "empty_input"
    mol = Chem.MolFromSmiles(raw.strip())
    if mol is None:
        return None, "parse_failed"
    try:
        in_mw = Descriptors.MolWt(mol)
        n_frags = len(Chem.GetMolFrags(mol))

        clean = rdMolStandardize.Cleanup(mol)
        parent = rdMolStandardize.FragmentParent(clean)
        had_charge = any(a.GetFormalCharge() != 0 for a in parent.GetAtoms())
        parent = rdMolStandardize.Uncharger().uncharge(parent)

        smi = Chem.MolToSmiles(parent)  # isomeric by default — keeps stereoisomers distinct
        if not smi or Chem.MolFromSmiles(smi) is None:
            return None, "standardization_failed:bad_output"

        notes = []
        if n_frags > 1:
            removed = in_mw - Descriptors.MolWt(parent)
            # Advisory: 'largest fragment' can be the wrong one for big organic
            # counterions (e.g. pamoate) — surface the removed mass for review.
            notes.append(f"desalted ({n_frags} frags, kept largest, removed ~{removed:.0f} Da)")
        if had_charge:
            notes.append("neutralized")
        return smi, ("; ".join(notes) if notes else "ok")
    except Exception as e:
        return None, f"standardization_failed:{type(e).__name__}"


def _apply_sanity_gate(df):
    """Flag (do NOT drop) parents that aren't small-molecule-like."""
    flags = []
    for smi in df["smiles"]:
        mol = Chem.MolFromSmiles(smi) if isinstance(smi, str) else None
        if mol is None:
            flags.append("standardization_failed")
        elif not any(a.GetSymbol() == "C" for a in mol.GetAtoms()):
            flags.append("no_carbon/inorganic")
        else:
            mw = Descriptors.MolWt(mol)
            if mw < _MIN_MW:
                flags.append(f"MW<{_MIN_MW:.0f}")
            elif mw > _MAX_MW:
                flags.append(f"MW>{_MAX_MW:.0f}")
            else:
                flags.append("")
    df = df.copy()
    df["sanity_flag"] = flags
    n_flagged = sum(bool(f) for f in flags)
    if n_flagged:
        print(f"   Sanity gate: flagged {n_flagged}/{len(df)} non-drug-like input(s) "
              "(kept, not dropped — see sanity_flag column)")
    return df


def _deduplicate_structures(df):
    """Collapse identical canonical (isomeric) structures, aggregating names."""
    df = df.copy().reset_index(drop=True)
    valid = df["smiles"].apply(lambda s: isinstance(s, str))
    if not valid.any():
        df["names_aggregated"] = df["name"].astype(str)
        return df

    # Aggregate display names per unique structure (valid rows only)
    name_map = (df[valid].groupby("smiles")["name"]
                .apply(lambda s: "; ".join(map(str, s))))

    # Drop later duplicates of valid structures; keep all invalid (failed) rows
    dup_mask = valid & df.duplicated(subset="smiles", keep="first")
    n_collapsed = int(dup_mask.sum())
    out = df[~dup_mask].copy().reset_index(drop=True)

    def _agg(smi, fallback):
        return name_map.loc[smi] if isinstance(smi, str) and smi in name_map.index else str(fallback)

    def _disp(smi, fallback):
        if isinstance(smi, str) and smi in name_map.index:
            names = name_map.loc[smi].split("; ")
            return f"{names[0]} (+{len(names) - 1})" if len(names) > 1 else names[0]
        return str(fallback)

    out["names_aggregated"] = [_agg(s, n) for s, n in zip(out["smiles"], out["name"])]
    out["name"] = [_disp(s, n) for s, n in zip(out["smiles"], out["name"])]

    if n_collapsed:
        print(f"   Collapsed {n_collapsed} duplicate structure(s) → {len(out)} unique molecules")
    return out


def standardize_molecules(df):
    """
    Standardize, sanity-check, and de-duplicate input molecules.

    - Desalt / neutralize / canonicalize each SMILES to its drug-like parent
      (records original as ``input_smiles`` and what changed as ``standardization_note``).
    - Flag non-drug-like parents in ``sanity_flag`` (no carbon, MW out of range) — flagged,
      never dropped.
    - Collapse identical isomeric structures (``names_aggregated``); stereoisomers stay distinct.
    - Assign a guaranteed-unique ``mol_id`` (used as the stable key for plots/merges).

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a 'smiles' column.

    Returns
    -------
    pd.DataFrame
        Standardized, de-duplicated DataFrame with added columns:
        input_smiles, standardization_note, sanity_flag, names_aggregated, mol_id.
        The 'smiles' column is overwritten with the standardized parent (NaN if it failed).
    """
    print("Standardizing molecules (desalt / neutralize / canonicalize)...")

    df = df.copy().reset_index(drop=True)
    if "name" not in df.columns:
        df["name"] = [f"Mol_{i + 1}" for i in range(len(df))]

    df["input_smiles"] = df["smiles"].astype(object)
    parents, notes = [], []
    for raw in df["input_smiles"]:
        smi, note = _standardize_one(raw)
        parents.append(smi)
        notes.append(note)
    df["smiles"] = parents
    df["standardization_note"] = notes

    n_total = len(df)
    if n_total:
        n_failed = sum(p is None for p in parents)
        n_desalted = sum("desalt" in n for n in notes)
        n_neutral = sum("neutral" in n for n in notes)
        print(f"   Standardized {n_total - n_failed}/{n_total}; desalted {n_desalted}; "
              f"neutralized {n_neutral}; failed {n_failed} ({100 * n_failed / n_total:.0f}%)")

    df = _apply_sanity_gate(df)
    df = _deduplicate_structures(df)

    # Stable unique id assigned on the final, de-duplicated set
    df = df.reset_index(drop=True)
    df["mol_id"] = [f"MOL_{i + 1:04d}" for i in range(len(df))]

    return df


# =============================================================================
# Physicochemical descriptors (RDKit)
# =============================================================================

def compute_physicochemical(df):
    """
    Compute 8 physicochemical descriptors from SMILES using RDKit.

    Adds columns: MW, LogP, HBD, HBA, TPSA, RotatableBonds, AromaticRings, FractionCSP3

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a 'smiles' column.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with 8 new descriptor columns added.
    """
    print("Computing physicochemical descriptors (RDKit)...")

    mols = [Chem.MolFromSmiles(s) if isinstance(s, str) else None for s in df["smiles"]]

    df = df.copy()
    df["MW"] = [Descriptors.MolWt(m) if m else np.nan for m in mols]
    df["LogP"] = [Descriptors.MolLogP(m) if m else np.nan for m in mols]
    df["HBD"] = [Descriptors.NumHDonors(m) if m else np.nan for m in mols]
    df["HBA"] = [Descriptors.NumHAcceptors(m) if m else np.nan for m in mols]
    df["TPSA"] = [Descriptors.TPSA(m) if m else np.nan for m in mols]
    df["RotatableBonds"] = [Descriptors.NumRotatableBonds(m) if m else np.nan for m in mols]
    df["AromaticRings"] = [Descriptors.NumAromaticRings(m) if m else np.nan for m in mols]
    df["FractionCSP3"] = [rdMolDescriptors.CalcFractionCSP3(m) if m else np.nan for m in mols]

    print(f"   Computed 8 descriptors for {len(df)} molecules")
    return df


# =============================================================================
# Drug-likeness assessment
# =============================================================================

def assess_druglikeness(df):
    """
    Assess Lipinski Rule of Five and Veber rules for drug-likeness.

    Adds columns: Lipinski_Violations, Lipinski_Pass, Veber_Pass, Druglike

    Lipinski Ro5: MW ≤ 500, LogP ≤ 5, HBD ≤ 5, HBA ≤ 10
    Veber rules: RotatableBonds ≤ 10, TPSA ≤ 140 Å²

    Parameters
    ----------
    df : pd.DataFrame
        Must contain MW, LogP, HBD, HBA, TPSA, RotatableBonds columns.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with drug-likeness columns added.
    """
    print("Assessing drug-likeness (Lipinski/Veber)...")

    df = df.copy()

    # Lipinski Rule of Five violations
    violations = (
        (df["MW"] > 500).astype(int)
        + (df["LogP"] > 5).astype(int)
        + (df["HBD"] > 5).astype(int)
        + (df["HBA"] > 10).astype(int)
    )
    df["Lipinski_Violations"] = violations
    df["Lipinski_Pass"] = violations <= 1  # Allow 1 violation

    # Veber rules
    df["Veber_Pass"] = (df["RotatableBonds"] <= 10) & (df["TPSA"] <= 140)

    # Overall drug-likeness
    df["Druglike"] = df["Lipinski_Pass"] & df["Veber_Pass"]

    n_pass = df["Druglike"].sum()
    print(f"   Drug-like: {n_pass}/{len(df)} molecules pass Lipinski + Veber rules")
    return df


# =============================================================================
# QED — Quantitative Estimate of Drug-likeness (composite developability score)
# =============================================================================

def compute_qed(df):
    """
    Compute QED (Quantitative Estimate of Drug-likeness, Bickerton et al. 2012).

    QED is a single 0–1 desirability score (higher = more drug-like) that
    integrates MW, LogP, HBA, HBD, TPSA, rotatable bonds, aromatic rings, and
    structural-alert count. Useful as a one-number developability ranking.

    Adds column: QED

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a 'smiles' column.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with a 'QED' column added (NaN for invalid SMILES).
    """
    print("Computing QED developability score (RDKit)...")

    df = df.copy()
    vals = []
    for s in df["smiles"]:
        mol = Chem.MolFromSmiles(s) if isinstance(s, str) else None
        try:
            vals.append(float(QED.qed(mol)) if mol is not None else np.nan)
        except Exception:
            vals.append(np.nan)
    df["QED"] = vals

    mean_qed = float(np.nanmean(df["QED"])) if len(df) else float("nan")
    print(f"   QED computed for {len(df)} molecules (mean {mean_qed:.2f})")
    return df


# =============================================================================
# Structural alerts — PAINS (assay interference) + toxicophores (Brenk/NIH)
# =============================================================================

_ALERT_CATALOG = None


def _get_alert_catalog():
    """Build (once) a combined PAINS + Brenk + NIH structural-alert catalog."""
    global _ALERT_CATALOG
    if _ALERT_CATALOG is None:
        params = FilterCatalogParams()
        params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)  # assay interference (Baell 2010)
        params.AddCatalog(FilterCatalogParams.FilterCatalogs.BRENK)  # unwanted/reactive groups (Brenk 2008)
        params.AddCatalog(FilterCatalogParams.FilterCatalogs.NIH)    # problematic functional groups
        _ALERT_CATALOG = FilterCatalog(params)
    return _ALERT_CATALOG


def flag_structural_alerts(df):
    """
    Flag PAINS and toxicophore substructure alerts using the RDKit FilterCatalog.

    - **PAINS** (Baell & Holloway 2010): pan-assay interference substructures.
      A strong triage *exclusion* signal — likely false positives in bioassays.
    - **Toxicophores** (Brenk 2008 + NIH): unwanted/reactive functional groups.
      *Advisory* only — many approved drugs carry at least one (e.g., aspirin's
      phenol ester), so treat as a flag for review, not automatic exclusion.

    Adds columns: PAINS_Count, PAINS_Alerts, Toxicophore_Count,
    Toxicophore_Alerts, PAINS_Pass

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a 'smiles' column.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with structural-alert columns added.
    """
    print("Flagging structural alerts (PAINS / Brenk / NIH)...")

    catalog = _get_alert_catalog()
    df = df.copy()

    pains_count, pains_desc, tox_count, tox_desc = [], [], [], []
    for s in df["smiles"]:
        mol = Chem.MolFromSmiles(s) if isinstance(s, str) else None
        if mol is None:
            pains_count.append(np.nan)
            pains_desc.append("")
            tox_count.append(np.nan)
            tox_desc.append("")
            continue

        pains, tox = [], []
        for match in catalog.GetMatches(mol):
            try:
                fset = match.GetProp("FilterSet")
            except Exception:
                fset = ""
            desc = match.GetDescription()
            if fset.upper().startswith("PAINS"):
                pains.append(desc)
            else:  # Brenk, NIH
                tox.append(desc)

        # De-duplicate while preserving order
        pains = list(dict.fromkeys(pains))
        tox = list(dict.fromkeys(tox))
        pains_count.append(len(pains))
        pains_desc.append("; ".join(pains))
        tox_count.append(len(tox))
        tox_desc.append("; ".join(tox))

    df["PAINS_Count"] = pains_count
    df["PAINS_Alerts"] = pains_desc
    df["Toxicophore_Count"] = tox_count
    df["Toxicophore_Alerts"] = tox_desc
    # NaN (invalid SMILES) counts as "not passing" so it cannot slip through clean.
    df["PAINS_Pass"] = df["PAINS_Count"].fillna(1) == 0

    n_pains = int((df["PAINS_Count"].fillna(0) > 0).sum())
    n_tox = int((df["Toxicophore_Count"].fillna(0) > 0).sum())
    print(f"   PAINS alerts: {n_pains}/{len(df)} compounds; "
          f"toxicophore alerts: {n_tox}/{len(df)}")
    return df


# =============================================================================
# ADMET predictions (ADMET-AI)
# =============================================================================

def predict_admet(df):
    """
    Predict ADMET properties using ADMET-AI (Chemprop-RDKit ensemble).

    Predicts ~41 ADMET endpoints including:
    - Absorption: Caco-2, HIA, Pgp substrate/inhibitor
    - Distribution: BBB, PPB
    - Metabolism: CYP1A2/2C9/2C19/2D6/3A4 inhibition and substrate
    - Excretion: Half-life, clearance
    - Toxicity: hERG, AMES, DILI, LD50

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a 'smiles' column.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with ADMET prediction columns added.
    dict
        ADMET-AI model metadata (endpoints predicted, model version).
    """
    print("Predicting ADMET properties (ADMET-AI)...")

    try:
        from admet_ai import ADMETModel
    except ImportError:
        print("   ERROR: admet-ai not installed. Run: pip install admet-ai")
        print("   Returning physicochemical properties only.")
        return df, {"endpoints": [], "error": "admet-ai not installed"}

    df = df.copy().reset_index(drop=True)

    # Predict only on rows with a valid standardized parent; invalid rows stay NaN.
    smiles_list = df["smiles"].tolist()
    valid_pos = [i for i, s in enumerate(smiles_list) if isinstance(s, str)]
    valid_smiles = [smiles_list[i] for i in valid_pos]

    if not valid_smiles:
        print("   WARNING: no valid standardized SMILES to predict; skipping ADMET.")
        return df, {"endpoints": [], "error": "no valid smiles"}

    # Use the commercially-permissive ChEMBL reference for percentile scoring instead
    # of ADMET-AI's bundled DrugBank reference (CC BY-NC). If the reference file is
    # missing, fall back to NO reference (drugbank_path=None) so predictions still work
    # but percentile columns are simply omitted — we never silently use the DrugBank set.
    if REFERENCE_CSV.exists():
        model = ADMETModel(drugbank_path=REFERENCE_CSV)  # NOTE: must be a Path, not str
        _ref_note = f"ChEMBL approved-drug reference ({REFERENCE_CSV.name})"
    else:
        print(f"   WARNING: percentile reference not found at {REFERENCE_CSV}.")
        print("   Proceeding WITHOUT percentile columns (predictions unaffected). "
              "Rebuild it with scripts/build_reference_set.py.")
        model = ADMETModel(drugbank_path=None)
        _ref_note = "none (percentile columns omitted)"
    print(f"   Running predictions for {len(valid_smiles)} molecules...")
    print(f"   Percentile reference: {_ref_note}")

    # ADMET-AI accepts a list of SMILES and returns a DataFrame (one row per input).
    preds = model.predict(smiles=valid_smiles).reset_index(drop=True)

    # ADMET-AI hardcodes the percentile suffix as 'drugbank_approved_percentile' even
    # when a custom reference is supplied. Rename to reflect the true (ChEMBL) source.
    _renames = {c: c.replace(_ADMET_AI_SUFFIX, PERCENTILE_SUFFIX)
                for c in preds.columns if c.endswith(_ADMET_AI_SUFFIX)}
    if _renames:
        preds = preds.rename(columns=_renames)
        print(f"   Renamed {len(_renames)} percentile columns -> *_{PERCENTILE_SUFFIX}")

    # Soft guard: inputs are de-duplicated upstream so a mismatch is unexpected. If it
    # still happens, warn and continue WITHOUT ADMET rather than discarding the correct
    # physicochemical/QED/alert results.
    if len(preds) != len(valid_smiles):
        print(f"   WARNING: ADMET-AI returned {len(preds)} rows for {len(valid_smiles)} valid "
              "molecules — skipping ADMET columns and continuing (physchem/QED/alerts unaffected).")
        return df, {"endpoints": [], "error": "row_count_mismatch"}

    # ADMET-AI also returns its own columns (e.g. 'QED'); drop overlaps we already computed.
    overlap = [c for c in preds.columns if c in df.columns]
    if overlap:
        preds = preds.drop(columns=overlap)
        print(f"   (skipped {len(overlap)} ADMET-AI column(s) already computed: {overlap})")

    # Scatter predictions back onto their original rows; invalid rows become NaN.
    preds.index = valid_pos
    aligned = preds.reindex(range(len(df))).reset_index(drop=True)
    df = pd.concat([df, aligned], axis=1)

    endpoints = list(preds.columns)
    print(f"   Predicted {len(endpoints)} ADMET endpoints")

    metadata = {
        "endpoints": endpoints,
        "n_molecules": len(df),
        "model": "ADMET-AI (Chemprop-RDKit)",
        "percentile_reference": _ref_note,
        "percentile_suffix": PERCENTILE_SUFFIX,
    }

    return df, metadata


# =============================================================================
# Full analysis pipeline
# =============================================================================

def run_full_analysis(df, include_admet=True):
    """
    Run the molecular property analysis pipeline.

    Standardizes inputs, then computes physicochemical descriptors, drug-likeness
    (Lipinski/Veber + QED), and structural alerts. ADMET-AI prediction is included
    by default and can be turned off for a fast, dependency-light run.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain 'smiles' and 'name' columns.
    include_admet : bool, default True
        If False, skip ADMET-AI prediction — physicochemical + drug-likeness + QED
        + structural alerts only. Much faster and needs no admet-ai/torch install;
        use it for quick Lipinski/physicochemical checks or ADMET-free environments.

    Returns
    -------
    pd.DataFrame
        Full results with all computed properties.
    dict
        Analysis summary with counts, flags, and metadata.
    """
    print("\n" + "=" * 60)
    print("MOLECULAR PROPERTY & ADMET ANALYSIS")
    print("=" * 60)

    # Standardize inputs first so every descriptor is computed on the drug-like parent
    df = standardize_molecules(df)

    # Physicochemical descriptors
    df = compute_physicochemical(df)

    # Drug-likeness (Lipinski/Veber) + QED developability score
    df = assess_druglikeness(df)
    df = compute_qed(df)

    # Structural alerts (PAINS / toxicophores)
    df = flag_structural_alerts(df)

    # ADMET predictions (optional)
    if include_admet:
        df, admet_meta = predict_admet(df)
    else:
        print("Skipping ADMET-AI predictions (include_admet=False) — "
              "physicochemical + drug-likeness + QED + alerts only.")
        admet_meta = {"endpoints": [], "skipped": True}

    # Build summary
    summary = _build_summary(df, admet_meta)

    print("\n" + "-" * 60)
    print(f"✓ Analysis completed successfully! "
          f"{len(df)} molecules × {summary['n_properties']} properties computed.")
    print("-" * 60)

    return df, summary


def _build_summary(df, admet_meta):
    """Build analysis summary dictionary."""
    meta_cols = {"name", "smiles", "therapeutic_class"}
    n_properties = len([c for c in df.columns if c not in meta_cols])

    summary = {
        "n_molecules": len(df),
        "n_druglike": int(df["Druglike"].sum()) if "Druglike" in df.columns else 0,
        "n_lipinski_pass": int(df["Lipinski_Pass"].sum()) if "Lipinski_Pass" in df.columns else 0,
        "n_veber_pass": int(df["Veber_Pass"].sum()) if "Veber_Pass" in df.columns else 0,
        "admet_endpoints": admet_meta.get("endpoints", []),
        "n_properties": n_properties,  # all computed columns (physchem + druglike + QED + alerts + ADMET)
        "mw_range": (float(df["MW"].min()), float(df["MW"].max())),
        "logp_range": (float(df["LogP"].min()), float(df["LogP"].max())),
    }

    # QED developability summary
    if "QED" in df.columns:
        summary["qed_mean"] = float(np.nanmean(df["QED"]))
        summary["n_qed_ge_05"] = int((df["QED"] >= 0.5).sum())

    # Structural-alert summary
    if "PAINS_Count" in df.columns:
        summary["n_pains"] = int((df["PAINS_Count"].fillna(0) > 0).sum())
        summary["n_toxicophore"] = int((df["Toxicophore_Count"].fillna(0) > 0).sum())
        summary["n_alert_free"] = int(
            ((df["PAINS_Count"].fillna(1) == 0) & (df["Toxicophore_Count"].fillna(1) == 0)).sum()
        )

    # Standardization / input-quality summary
    if "sanity_flag" in df.columns:
        summary["n_sanity_flagged"] = int((df["sanity_flag"].astype(str) != "").sum())
    if "standardization_note" in df.columns:
        summary["n_standardization_failed"] = int(
            df["standardization_note"].astype(str).str.contains("fail", case=False).sum()
        )

    # Flag safety concerns using the endpoint registry (robust to ADMET-AI column names)
    safety_flags = {}
    if summary["admet_endpoints"]:
        resolved = resolve_endpoints(df.columns, verbose=False)
        for key in SAFETY_KEYS:
            col = resolved.get(key)
            if col and col in df.columns:
                # Binary predictions: >0.5 = positive
                n_flagged = int((pd.to_numeric(df[col], errors="coerce") > 0.5).sum())
                safety_flags[key] = {"column": col, "n_flagged": n_flagged}

    summary["safety_flags"] = safety_flags
    return summary


if __name__ == "__main__":
    from load_example_data import load_example_drugs

    df = load_example_drugs()
    results, summary = run_full_analysis(df)
    print(f"\nSummary: {summary['n_molecules']} molecules, "
          f"{summary['n_druglike']} drug-like, "
          f"{summary['n_properties']} properties")
